
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





settings = Settings()
