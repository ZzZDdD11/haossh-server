"""SSH 终端相关 DTO。"""

from pydantic import BaseModel, Field


class OpenTerminalRequest(BaseModel):
    connection_id: str = Field(..., alias="connectionId")
    cols: int = Field(default=80)
    rows: int = Field(default=24)


class WriteTerminalRequest(BaseModel):
    terminal_session_id: str = Field(..., alias="terminalSessionId")
    data: str = Field(..., description="写入终端的数据，如 'ls -la\\n'")


class ResizeTerminalRequest(BaseModel):
    terminal_session_id: str = Field(..., alias="terminalSessionId")
    cols: int
    rows: int


class ExecCommandRequest(BaseModel):
    connection_id: str = Field(..., alias="connectionId")
    command: str
    timeout: int = Field(default=30)
