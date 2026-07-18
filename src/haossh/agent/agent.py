import os
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

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
)
