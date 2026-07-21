import logging
import os
from pathlib import Path

from pydantic_ai import Agent, RunContext

logger = logging.getLogger(__name__)
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models.openai import OpenAIChatModel

from haossh.agent.context import trim_history
from haossh.agent.deps import AgentDeps
from haossh.agent.tools import tools
from haossh.config import settings

# pydantic-ai 自动从 DEEPSEEK_API_KEY 环境变量读取密钥
os.environ["DEEPSEEK_API_KEY"] = settings.agent_api_key

# 读取 system prompt（静态基础部分：身份、能力、安全红线、工作流程）
_prompt_file = Path(__file__).parent / "prompts" / "ssh_operator.md"
SYSTEM_PROMPT = _prompt_file.read_text(encoding="utf-8")

# 创建 Agent
agent = Agent(
    model=OpenAIChatModel(
        model_name=settings.agent_model_name,
        provider="deepseek",
    ),
    system_prompt=SYSTEM_PROMPT,
    tools=tools,
    deps_type=AgentDeps,
    capabilities=[
        # 上下文管理：框架在传 LLM 前自动裁剪历史，防超 context window
        ProcessHistory(processor=trim_history),
    ],
)


# ===== 动态 system prompt =====
# pydantic-ai 在每次构造 LLM 请求时调用，返回值拼接到静态 system_prompt 之后


@agent.system_prompt(dynamic=True)
def sudo_instruction(ctx: RunContext[AgentDeps]) -> str:
    """权限动态指令：allow_sudo=False 时覆盖静态 prompt 里的 sudo 规则。"""
    if ctx.deps.allow_sudo:
        return ""  # 静态 prompt 已有 sudo 指令，无需追加
    return (
        "## ⚠️ 权限限制（覆盖上方 sudo 规则）\n"
        "当前会话禁止使用 sudo。上方「主动使用 sudo」的规则作废。\n"
        "权限不足时，报告用户需要什么权限，不要自行提权。"
    )


@agent.system_prompt(dynamic=True)
async def connection_status(ctx: RunContext[AgentDeps]) -> str:
    """连接状态动态指令：告知 LLM 当前是否已连接 SSH。"""
    from haossh.ssh import session
    connected = bool(ctx.deps.session_id) and await session.is_connected(ctx.deps.session_id)
    logger.info("[动态prompt] connection_status 被调用 session_id=%r connected=%s", ctx.deps.session_id, connected)
    if connected:
        return "## 当前状态\nSSH 已连接，你可以直接调用工具执行命令。"
    return "## 当前状态\n未连接 SSH，你是运维顾问，只能提供建议，不能执行命令。"


@agent.system_prompt(dynamic=True)
async def milestone_summary(ctx: RunContext[AgentDeps]) -> str:
    """历史里程碑注入：让 LLM 始终能看到关键事件，不受消息裁剪影响。"""
    conv_id = ctx.deps.conversation_id
    if not conv_id:
        return ""
    from haossh.db import repo_conversation
    milestones = await repo_conversation.get_milestones(conv_id, limit=10)
    if not milestones:
        return ""
    lines = ["## 历史关键事件（里程碑）"]
    for m in milestones:
        lines.append(f"- [{m.event_type}] {m.content}")
    return "\n".join(lines)
