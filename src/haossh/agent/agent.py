import os
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models.openai import OpenAIChatModel

from haossh.agent.context import trim_history
from haossh.agent.deps import AgentDeps
from haossh.agent.tools import tools
from haossh.config import settings

# pydantic-ai 自动从 DEEPSEEK_API_KEY 环境变量读取密钥
os.environ["DEEPSEEK_API_KEY"] = settings.agent_api_key

# 读取 system prompt
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
