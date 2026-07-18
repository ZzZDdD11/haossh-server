from pydantic import BaseModel



class ChatRequest(BaseModel):
    session_id: str
    message: str
    terminal_session_id: str | None = None