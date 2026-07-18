"""终端绑定 API 路由。

Agent 对话与终端会话的绑定关系管理。
绑定后 Agent 在工具调用时，通过该绑定找到对应的终端/SSH 连接来执行命令。
"""

import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ssh/terminal-binding", tags=["Terminal Binding"])


def _ok(data: object = None) -> dict:
    return {"code": "0000", "info": "成功", "data": data}


# ── 内存绑定表（Phase 3 迁移到数据库）─────────────────────────

_bindings: dict[str, str] = {}  # chat_session_id → terminal_session_id


# ── Request Schemas ────────────────────────────────────────────

class BindTerminalRequest(BaseModel):
    chat_session_id: str = Field(..., alias="chatSessionId")
    terminal_session_id: str = Field(..., alias="terminalSessionId")


class UnbindTerminalRequest(BaseModel):
    chat_session_id: str = Field(..., alias="chatSessionId")


# ── Routes ─────────────────────────────────────────────────────

@router.post("/bind")
async def bind_terminal(req: BindTerminalRequest):
    """将终端会话绑定到 Agent 对话。"""
    _bindings[req.chat_session_id] = req.terminal_session_id
    logger.info(
        "终端已绑定 chat_session_id=%s terminal_session_id=%s",
        req.chat_session_id, req.terminal_session_id,
    )
    return _ok()


@router.post("/unbind")
async def unbind_terminal(req: UnbindTerminalRequest):
    """解除 Agent 对话的终端绑定。"""
    removed = _bindings.pop(req.chat_session_id, None)
    if removed:
        logger.info("终端已解绑 chat_session_id=%s", req.chat_session_id)
    return _ok()


@router.get("/query")
async def query_binding(chatSessionId: str = Query(..., alias="chatSessionId")):
    """查询 Agent 对话绑定的终端 ID。"""
    tid = _bindings.get(chatSessionId)
    return _ok({"terminalSessionId": tid})
