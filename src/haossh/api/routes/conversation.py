"""对话历史 API。

提供获取对话历史消息的接口，用于前端刷新页面后恢复对话上下文。
"""

import json
import logging

from fastapi import APIRouter

from haossh.db import repo_conversation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversation", tags=["Conversation"])


def _ok(data=None):
    return {"code": "0000", "info": "成功", "data": data}


def _err(info: str):
    return {"code": "1001", "info": info, "data": None}


def _messages_to_dto(messages) -> list[dict]:
    """把 pydantic-ai ModelMessage 列表转成前端友好格式。

    把 ModelRequest/ModelResponse 的 parts 拍平成：
    [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "...", "tool_calls": [
        {"tool": "execute_command", "args": {...}, "result": "..."}
      ]},
    ]
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse

    result = []

    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                pk = getattr(part, "part_kind", None)
                if pk == "user-prompt":
                    content = getattr(part, "content", "")
                    result.append({"role": "user", "content": str(content)})
                elif pk == "tool-return":
                    # 用 call_id 匹配到之前 assistant 消息里的 tool_call
                    call_id = getattr(part, "tool_call_id", "")
                    content = getattr(part, "content", "")
                    for msg_dto in reversed(result):
                        if msg_dto["role"] != "assistant":
                            continue
                        for tc in msg_dto.get("tool_calls") or []:
                            if tc.get("call_id") == call_id and not tc.get("result"):
                                tc["result"] = str(content)[:500] if content else ""
                                break
                        else:
                            continue
                        break
        elif isinstance(msg, ModelResponse):
            text_parts = []
            tool_calls = []
            for part in msg.parts:
                pk = getattr(part, "part_kind", None)
                if pk == "text":
                    text_parts.append(str(getattr(part, "content", "")))
                elif pk == "tool-call":
                    args = getattr(part, "args", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    tool_calls.append({
                        "tool": getattr(part, "tool_name", ""),
                        "args": args,
                        "call_id": getattr(part, "tool_call_id", ""),
                        "result": None,
                    })
            result.append({
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else "",
                "tool_calls": tool_calls if tool_calls else None,
            })

    return result


@router.get("/{conv_id}/messages")
async def get_conversation_messages(conv_id: str):
    """获取对话的历史消息（前端可渲染格式）。"""
    messages = await repo_conversation.get_messages(conv_id)
    if not messages:
        return _err(f"对话不存在或无消息: {conv_id}")
    return _ok({"conversation_id": conv_id, "messages": _messages_to_dto(messages)})
