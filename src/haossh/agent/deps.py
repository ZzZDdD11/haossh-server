"""Agent 运行时上下文（deps）。

每次对话由路由层构造，通过 agent.run_stream(deps=...) 注入，
工具函数从 RunContext[AgentDeps].deps 取用。

deps 是请求级局部变量，不是全局状态——每次 run_stream 调用独立。
"""

from dataclasses import dataclass


@dataclass
class AgentDeps:
    session_id: str                          # SSH 连接 ID
    conversation_id: str = ""                # 对话 ID（里程碑关联用）
    terminal_session_id: str | None = None   # 可选的 PTY 会话（未来用）
    allow_sudo: bool = True                  # 权限控制：是否允许 sudo
    max_command_timeout: int = 300           # 限制单命令最长执行时间
