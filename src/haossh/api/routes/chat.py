import json
import logging
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from haossh.agent import agent
from haossh.agent.deps import AgentDeps
from haossh.api.schemas.chat import ChatRequest
from haossh.db import repo_conversation
from haossh.db.models import Conversation

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse(payload: dict) -> str:
    """组装一条 SSE data 行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_to_payload(event) -> dict | None:
    """把 pydantic-ai 事件转成前端 JSON payload，不需要的事件返回 None。

    设计：
    - text/thinking → 从 PartStart/PartDelta 取增量（流式打字效果）
    - tool_call     → 从 FunctionToolCallEvent 取（此时 args 完整，框架即将执行）
    - tool_result   → 从 FunctionToolResultEvent 取（工具已执行完）
    - 模型生成阶段的 tool-call part（PartStart/Delta）忽略，避免推不完整 args
    """
    # ── 文本/思考的流式片段 ──────────────────────────
    if event.event_kind == "part_start":
        part = event.part
        if part.part_kind == "text":
            return {"type": "text", "delta": part.content}
        if part.part_kind == "thinking":
            return {"type": "thinking", "delta": part.content}
        # tool-call 的 PartStart 不推（args 可能不完整）

    elif event.event_kind == "part_delta":
        delta = event.delta
        if delta.part_delta_kind == "text":
            return {"type": "text", "delta": delta.content_delta}
        if delta.part_delta_kind == "thinking" and delta.content_delta:
            return {"type": "thinking", "delta": delta.content_delta}
        # tool_call 的增量不推

    # ── 工具调用语义事件（args/result 完整可靠）──────
    elif event.event_kind == "function_tool_call":
        part = event.part  # ToolCallPart
        args = part.args
        # args 可能是 str（JSON 字符串）或 dict，统一成 dict 便于前端
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass  # 保留原始字符串
        return {
            "type": "tool_call",
            "tool": part.tool_name,
            "args": args,
            "call_id": part.tool_call_id,
        }

    elif event.event_kind == "function_tool_result":
        part = event.part  # ToolReturnPart 或 RetryPromptPart
        if part.part_kind == "tool-return":
            return {
                "type": "tool_result",
                "tool": part.tool_name,
                "result": str(part.content),
                "call_id": part.tool_call_id,
                "outcome": part.outcome,
            }
        # RetryPromptPart（工具要求重试）— 当作 tool_result 的失败反馈
        if part.part_kind == "retry-prompt":
            return {
                "type": "tool_result",
                "tool": part.tool_name,
                "result": f"[retry] {part.content}",
                "call_id": part.tool_call_id,
                "outcome": "failed",
            }

    # part_end / final_result / enqueued_messages 等暂不推
    return None


@router.post("/chat_stream")
async def chat_stream(req: ChatRequest):
    deps = AgentDeps(
        session_id=req.session_id,
        terminal_session_id=req.terminal_session_id,
        allow_sudo=True,
    )

    async def generator():
        # 确定对话 ID：传了就复用，没传就新建
        if req.conversation_id:
            conv_id = req.conversation_id
            history = await repo_conversation.get_messages(conv_id)
            # 读入历史工作区路径，供持久 shell 崩溃重建时自动 cd 恢复
            conv = await repo_conversation.get_conversation(conv_id)
            if conv and conv.workspace_path:
                deps.workspace_path = conv.workspace_path
            logger.info("续聊 conversation_id=%s 历史消息数=%d", conv_id[:12], len(history))
        else:
            conv = Conversation(
                id=uuid.uuid4().hex,
                connection_id=req.session_id or None,
            )
            await repo_conversation.create_conversation(conv)
            conv_id = conv.id
            history = []
            # 规则层：新对话首轮自动记录 task_start，保留任务原始意图（防上下文裁剪丢失）
            await repo_conversation.append_milestone(conv_id, "task_start", req.message[:100])
            logger.info("新建对话 conversation_id=%s session_id=%s", conv_id[:12], req.session_id[:12] if req.session_id else '空')

        # 设置 conversation_id 到 deps，供 record_milestone 和 milestone_summary 使用
        deps.conversation_id = conv_id

        try:
            # 用 agent.iter() 而非 run_stream()，才能拿到完整事件流
            # message_history 传入历史，Agent 才能记得之前聊过什么
            async with agent.iter(
                req.message,
                deps=deps,
                message_history=history,
                usage_limits=UsageLimits(request_limit=200),
            ) as run:
                async for node in run:
                    # ModelRequestNode: 模型流式响应（text/thinking 片段）
                    # CallToolsNode:    工具调用执行（tool_call/tool_result）
                    if Agent.is_model_request_node(node) or Agent.is_call_tools_node(node):
                        async with node.stream(run.ctx) as stream:
                            async for event in stream:
                                payload = _event_to_payload(event)
                                if payload is not None:
                                    yield _sse(payload)
            # 存本轮新增消息（增量追加，不覆盖）
            await repo_conversation.append_messages(conv_id, run.new_messages())
        except Exception as e:
            logger.exception("chat_stream 执行异常 conversation_id=%s", conv_id)
            yield _sse({"type": "error", "message": str(e)})
        # done 事件带上 conversation_id，前端后续请求带上即可续聊
        yield _sse({"type": "done", "conversation_id": conv_id})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
    )
