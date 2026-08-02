"""SQLModel 表定义。

表关系：
  tenants 1 ── ∞ users
  tenants 1 ── ∞ ssh_connections 1 ── ∞ conversations 1 ── ∞ messages

设计要点：
- table=True 的模型既是数据库表，也兼容 pydantic 校验
- 时间戳用 ISO 字符串存（与现有代码风格一致），避免 datetime 序列化复杂度
- secret_enc 存 Fernet 加密后的 bytes，明文绝不落库
- connection_id 可空：纯聊天场景（未连 SSH）也能有对话
- tenant_id 是多租户隔离边界：SSHConnection/Conversation 的所有查询必须带
  tenant_id 条件（见 repo_connection.py/repo_conversation.py 的 get_owned 系列方法）
"""

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


class Tenant(SQLModel, table=True):
    """租户（组织）。多租户数据隔离的边界，所有业务数据最终按 tenant_id 归属。"""
    __tablename__ = "tenants"

    id: str = Field(primary_key=True)                      # uuid hex
    name: str                                              # 组织展示名
    plan: str = Field(default="free")                      # 预留：free/pro，暂不使用
    created_at: str = Field(default_factory=_now_iso)


class User(SQLModel, table=True):
    """用户。归属唯一一个租户（MVP 不支持跨租户共享账号，也暂不支持邀请成员）。"""
    __tablename__ = "users"

    id: str = Field(primary_key=True)                      # uuid hex
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    email: str = Field(unique=True, index=True)            # 登录账号，业务层统一转小写存取
    password_hash: str                                     # argon2 哈希，明文绝不落库
    role: str = Field(default="owner")                     # MVP 阶段注册者必为 owner
    created_at: str = Field(default_factory=_now_iso)


class SSHConnection(SQLModel, table=True):
    """SSH 连接元信息。

    对齐前端 CreateConnectionRequest 字段，secret 加密存储。
    auth_type: 1=密码, 2=私钥；secret_enc 存对应的加密密文。
    """
    __tablename__ = "ssh_connections"

    id: str = Field(primary_key=True)                      # uuid hex
    tenant_id: str = Field(foreign_key="tenants.id", index=True)   # 隔离边界
    user_id: str = Field(foreign_key="users.id", index=True)       # 创建者
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

    status: active（进行中）/ completed（已完成）
    task_summary: 任务简述，agent 调 record_milestone(done) 时自动填充
    """
    __tablename__ = "conversations"

    id: str = Field(primary_key=True)                      # uuid hex
    tenant_id: str = Field(foreign_key="tenants.id", index=True)   # 隔离边界
    user_id: str = Field(foreign_key="users.id", index=True)       # 创建者
    connection_id: str | None = Field(
        default=None, foreign_key="ssh_connections.id", index=True
    )
    title: str | None = None                               # 列表展示用
    status: str = Field(default="active", index=True)      # active / completed
    task_summary: str | None = None                        # 任务简述（完成时填充）
    workspace_path: str | None = None                       # 当前任务所在目录（从 shell 真实 cwd 观测得到，非声明）
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class Message(SQLModel, table=True):
    """对话消息。

    pydantic-ai 的 ModelMessage 序列化成 JSON 存 content_json。
    seq 用于保证顺序；读取时 ORDER BY seq 还原成 list[ModelMessage]。
    role 是简化角色（user/model），便于查询统计；完整结构在 content_json 里。

    没有单独的 tenant_id：归属通过 conversation_id 间接确定，且从不被前端
    直接按 message id 查询/操作，不需要重复冗余隔离字段。
    """
    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)  # 自增
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    seq: int                                                # 排序序号
    role: str                                               # user / model
    content_json: str                                       # ModelMessage 序列化 JSON
    created_at: str = Field(default_factory=_now_iso)


class Milestone(SQLModel, table=True):
    """对话里程碑——关键事件记忆，独立于消息裁剪。

    LLM 通过 record_milestone 工具主动记录关键事件。
    每轮 LLM 请求时通过动态 system prompt 注入，不受 TokenBudgetTrimmer 裁剪影响。
    """
    __tablename__ = "milestones"

    id: int | None = Field(default=None, primary_key=True)  # 自增
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    event_type: str                                         # error/solution/decision/done
    content: str                                            # 事件简述
    created_at: str = Field(default_factory=_now_iso)
