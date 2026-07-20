"""SSH 连接相关 DTO。"""

from pydantic import BaseModel, Field


class CreateConnectionRequest(BaseModel):
    """创建 / 更新连接请求（与前端 SshConnectionPayload 对齐）。"""
    connection_id: str | None = Field(default=None, alias="connectionId")
    connection_name: str = Field(..., alias="connectionName")
    host: str
    port: int = 22
    username: str
    auth_type: int = Field(default=1, alias="authType")  # 1=密码, 2=私钥
    password: str | None = None
    private_key: str | None = Field(default=None, alias="privateKey")
    user_id: str = Field(default="default", alias="userId")
    connect_timeout: int = Field(default=30, alias="connectTimeout")
    keepalive_interval: int = Field(default=60, alias="keepaliveInterval")
    startup_command: str | None = Field(default=None, alias="startupCommand")
    compression: bool = False
    strict_host_key_check: bool = Field(default=True, alias="strictHostKeyCheck")


class ConnectRequest(BaseModel):
    host: str = Field(..., description="远程主机地址")
    port: int = Field(default=22)
    username: str = Field(..., description="SSH 用户名")
    password: str = Field(..., description="SSH 密码（明文）")
