"""
配置管理模块。

两层设计：
- BaseModel 子类 → 建模 ssh-agent.yml 的嵌套结构（不需要环境变量覆盖的内容）
- BaseSettings 子类 → 部署时可被环境变量覆盖的参数（端口、数据库、API Key）

加载顺序：YAML 文件 → Pydantic 默认值 → 环境变量覆盖（HAOSSH_ 前缀）
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ============================================================
# 一、ssh-agent.yml 的结构建模（BaseModel）
#    这些配置不需要环境变量覆盖，提示词、agent 定义直接读 YAML
# ============================================================

class AiApiConfig(BaseModel):
    """对应 ssh-agent.yml: module.ai-api"""
    base_url: str = "https://apis.itedus.cn"
    api_key: str = ""
    completions_path: str = "v1/chat/completions"
    embeddings_path: str = "v1/embeddings"


class ChatModelConfig(BaseModel):
    """对应 ssh-agent.yml: module.chat-model"""
    model: str = "gpt-5.1"


class AgentDef(BaseModel):
    """对应 ssh-agent.yml: agents 列表中的单个 agent 定义"""
    name: str = ""
    description: str = ""
    instruction: str = ""
    output_key: Optional[str] = None


class RunnerConfig(BaseModel):
    """对应 ssh-agent.yml: runner"""
    agent_name: str = ""


class AgentModule(BaseModel):
    """一个 agent table 的 module 部分"""
    ai_api: AiApiConfig = Field(default_factory=AiApiConfig)
    chat_model: ChatModelConfig = Field(default_factory=ChatModelConfig)
    agents: list[AgentDef] = Field(default_factory=list)
    runner: RunnerConfig = Field(default_factory=RunnerConfig)


class AgentInfo(BaseModel):
    """对应 ssh-agent.yml: agent（agent 元信息）"""
    agent_id: str = "100000"
    agent_name: str = "SSH AI Agent"
    agent_desc: str = ""

    @field_validator("agent_id", mode="before")
    @classmethod
    def coerce_str(cls, v: object) -> str:
        """YAML 里 agent-id: 100000 是整数，Pydantic 不会自动转 str，手动转。"""
        return str(v)


class AgentTable(BaseModel):
    """对应 ssh-agent.yml: tables 中一个 agent 配置项"""
    app_name: str = ""
    agent: AgentInfo = Field(default_factory=AgentInfo)
    module: AgentModule = Field(default_factory=AgentModule)


class AgentConfigRoot(BaseModel):
    """对应整个 ssh-agent.yml 中 ai.agent.config 部分"""
    tables: dict[str, AgentTable] = Field(default_factory=dict)


# ============================================================
# 二、部署时可覆盖的配置（BaseSettings）
#    每个字段有三种来源，优先级从高到低：
#    1. 环境变量（HAOSSH_ 前缀）
#    2. YAML 文件中读取的值
#    3. 字段默认值
# ============================================================

class Settings(BaseSettings):
    """haossh 全局配置。"""

    model_config = SettingsConfigDict(
        env_prefix="HAOSSH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 服务端口 ----
    server_port: int = 8091

    # ---- 数据库 ----
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = "12345678"
    db_name: str = "haossh"

    # ---- Agent AI API ----
    agent_ai_base_url: str = "https://apis.itedus.cn"
    agent_ai_api_key: str = ""
    agent_ai_model: str = "gpt-5.1"

    # ---- 意图识别 AI API ----
    intent_ai_base_url: str = "https://apis.itedus.cn"
    intent_ai_api_key: str = ""
    intent_ai_model: str = "gpt-5.1"

    # ---- Profile ----
    profile: str = "dev"

    @property
    def database_url(self) -> str:
        """构建异步数据库连接 URL（用于 SQLAlchemy）"""
        return (
            f"mysql+asyncmy://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            "?charset=utf8mb4"
        )


# ============================================================
# 三、工具函数 & YAML 加载
# ============================================================

_RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"


def _kebab_to_snake_dict(d: dict) -> dict:
    """
    递归把 dict 的所有 key 从 kebab-case 转成 snake_case。

    例如 {"agent-desc": "hello"} → {"agent_desc": "hello"}
    嵌套 dict 和 list 也会递归处理。
    """
    result = {}
    for key, value in d.items():
        snake_key = key.replace("-", "_")
        if isinstance(value, dict):
            result[snake_key] = _kebab_to_snake_dict(value)
        elif isinstance(value, list):
            result[snake_key] = [
                _kebab_to_snake_dict(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[snake_key] = value
    return result


def load_agent_config(agent_yml_path: Optional[Path] = None) -> AgentConfigRoot:
    """
    从 ssh-agent.yml 加载 Agent 配置。

    返回类型安全的 AgentConfigRoot，通过 .tables["sshAgent"] 访问。
    """
    if agent_yml_path is None:
        agent_yml_path = _RESOURCES_DIR / "agents" / "ssh-agent.yml"

    if not agent_yml_path.exists():
        return AgentConfigRoot()

    with open(agent_yml_path) as f:
        raw = yaml.safe_load(f)

    # YAML 路径 ai.agent.config → AgentConfigRoot
    # 先转换 kebab-case → snake_case，再传给 Pydantic
    config_dict = raw.get("ai", {}).get("agent", {}).get("config", {})
    config_dict = _kebab_to_snake_dict(config_dict)
    return AgentConfigRoot(**config_dict)


def load_app_yml(profile: str = "dev") -> dict:
    """从 application-{profile}.yml 加载应用配置，返回 kebab→snake 转换后的 dict。"""
    path = _RESOURCES_DIR / f"application-{profile}.yml"
    if not path.exists():
        return {}

    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return _kebab_to_snake_dict(raw)


def _env_has(var_name: str) -> bool:
    """检查某个 HAOSSH_ 环境变量是否已被设置（用于判断 YAML 值是否该让步）。"""
    return bool(os.environ.get(var_name, "").strip())


def init_settings() -> Settings:
    """
    创建最终的 Settings 实例。

    加载顺序（优先级从低到高）：
    1. Settings 字段默认值
    2. YAML 文件值（application-dev.yml + ssh-agent.yml）
    3. 环境变量（HAOSSH_XXX）——如果环境变量存在且非空，YAML 值让步

    在 lifespan 的 startup 阶段调用一次。
    """
    s = Settings()  # 1. 从环境变量 + 字段默认值开始

    # 2. application-dev.yml
    app = load_app_yml(s.profile)

    server = app.get("server", {})
    if "port" in server:
        s.server_port = int(server["port"])

    datasource = app.get("spring", {}).get("datasource", {})
    if datasource.get("username") and not _env_has("HAOSSH_DB_USER"):
        s.db_user = datasource["username"]
    if datasource.get("password") and not _env_has("HAOSSH_DB_PASSWORD"):
        s.db_password = datasource["password"]

    intent = app.get("intent_ai_api", {})
    if intent.get("base_url") and not _env_has("HAOSSH_INTENT_AI_BASE_URL"):
        s.intent_ai_base_url = intent["base_url"]
    if intent.get("api_key") and not _env_has("HAOSSH_INTENT_AI_API_KEY"):
        s.intent_ai_api_key = intent["api_key"]
    chat_model = intent.get("chat_model", {})
    if chat_model.get("model") and not _env_has("HAOSSH_INTENT_AI_MODEL"):
        s.intent_ai_model = chat_model["model"]

    # 3. ssh-agent.yml：YAML 值作为 fallback
    agent_cfg = load_agent_config()
    ssh_agent = agent_cfg.tables.get("sshAgent")
    if ssh_agent:
        if not _env_has("HAOSSH_AGENT_AI_API_KEY"):
            s.agent_ai_api_key = ssh_agent.module.ai_api.api_key
        if not _env_has("HAOSSH_AGENT_AI_BASE_URL"):
            s.agent_ai_base_url = ssh_agent.module.ai_api.base_url
        if not _env_has("HAOSSH_AGENT_AI_MODEL"):
            s.agent_ai_model = ssh_agent.module.chat_model.model

    return s


# ============================================================
# 四、全局单例（lifespan 启动时初始化）
# ============================================================

settings = Settings()
agent_config = load_agent_config()
