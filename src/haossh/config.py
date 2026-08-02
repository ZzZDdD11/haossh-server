
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """haossh 全局配置。"""
    model_config = SettingsConfigDict(
        env_prefix="HAOSSH_",
        env_file=".env",
        env_file_encoding="utf-8"
    )

    agent_model_name: str = "deepseek-v4-flash"
    agent_base_url: str = "https://api.deepseek.com"
    agent_api_key: str = ""

    # 数据持久化
    # SQLite 异步驱动；未来切 Postgres 改成 postgresql+asyncpg://...
    database_url: str = "sqlite+aiosqlite:///./haossh.db"
    # Fernet 对称加密密钥（32 字节 base64），用 Fernet.generate_key() 生成
    # 用于加密 SSH 密码/私钥。务必通过 env 注入，勿提交到代码库
    secret_key: str = ""

    # 多租户认证：JWT 签名密钥，务必通过 env 注入，勿提交到代码库
    jwt_secret: str = ""
    jwt_expire_days: int = 7



settings = Settings()
