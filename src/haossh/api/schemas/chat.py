from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str = ""                     # SSH 连接 ID（用于 agent 操作 SSH，纯聊天可空）
    message: str
    conversation_id: str | None = None       # 对话 ID（可选，没传则自动新建）
    terminal_session_id: str | None = None
