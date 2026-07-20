"""SQLModel 表定义。

三张表，关系：
  ssh_connections 1 ── ∞ conversations 1 ── ∞ messages

设计要点：
- table=True 的模型既是数据库表，也兼容 pydantic 校验
- 时间戳用 ISO 字符串存（与现有代码风格一致），避免 datetime 序列化复杂度
- secret_enc 存 Fernet 加密后的 bytes，明文绝不落库
- connection_id 可空：纯聊天场景（未连 SSH）也能有对话
"""

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


class SSHConnection(SQLModel, table=True):
    """SSH 连接元信息。

    对齐前端 CreateConnectionRequest 字段，secret 加密存储。
    auth_type: 1=密码, 2=私钥；secret_enc 存对应的加密密文。
    """
    __tablename__ = "ssh_connections"

    id: str = Field(primary_key=True)                      # uuid hex
    user_id: str = Field(default="default", index=True)    # 按用户查列表
    name: str                                              # 连接名称
    host: str
    port: int = 22
    username: str
    auth_type: int = 1                                     # 1=密码, 2=私钥
    secret_enc: str                                        # AES-256-GCM 加密后的 base64 字符串（密码或私钥）
    connect_timeout: int = 30
    keepalive_interval: int = 60
    startup_command: str | None = None
    compression: bool = False
    strict_host_key_check: bool = True
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class Conversation(SQLModel, table=True):
    """对话会话。

    与 SSH 连接解耦：一个对话可选关联一台机器（connection_id 可空）。
    SSH 断开重连只需更新 connection_id，对话历史（messages）不动。
    """
    __tablename__ = "conversations"

    id: str = Field(primary_key=True)                      # uuid hex
    user_id: str = Field(default="default", index=True)
    connection_id: str | None = Field(
        default=None, foreign_key="ssh_connections.id", index=True
    )
    title: str | None = None                               # 列表展示用
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class Message(SQLModel, table=True):
    """对话消息。

    pydantic-ai 的 ModelMessage 序列化成 JSON 存 content_json。
    seq 用于保证顺序；读取时 ORDER BY seq 还原成 list[ModelMessage]。
    role 是简化角色（user/model），便于查询统计；完整结构在 content_json 里。
    """
    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)  # 自增
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    seq: int                                                # 排序序号
    role: str                                               # user / model
    content_json: str                                       # ModelMessage 序列化 JSON
    created_at: str = Field(default_factory=_now_iso)
