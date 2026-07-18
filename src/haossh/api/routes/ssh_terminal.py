"""SSH 终端相关 API 路由。"""

import logging

from fastapi import APIRouter, Query

from haossh.api.schemas.ssh_terminal import (
    ExecCommandRequest,
    OpenTerminalRequest,
    ResizeTerminalRequest,
    WriteTerminalRequest,
)
from haossh.ssh import terminal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ssh/terminal", tags=["SSH Terminal"])


# ── 辅助 ─────────────────────────────────────────────────────

def _ok(data: object = None) -> dict:
    return {"code": "0000", "info": "成功", "data": data}


def _err(info: str) -> dict:
    return {"code": "1001", "info": info, "data": None}


# ── 打开 / 关闭 / 读写 ────────────────────────────────────────

@router.post("/open")
async def open_terminal(req: OpenTerminalRequest):
    """打开 PTY 终端。需先通过 /ssh/connect 建立 SSH 连接。"""
    try:
        tid = await terminal.create(
            connection_id=req.connection_id,
            cols=req.cols,
            rows=req.rows,
        )
        return _ok({"terminalSessionId": tid})
    except ValueError as e:
        logger.warning("打开终端失败: %s", e)
        return _err(str(e))


@router.post("/write")
async def write_terminal(req: WriteTerminalRequest):
    """向终端写入数据。"""
    try:
        await terminal.write(req.terminal_session_id, req.data)
        return _ok()
    except ValueError as e:
        logger.warning("写入终端失败: %s", e)
        return _err(str(e))


@router.get("/read")
async def read_terminal(terminalSessionId: str = Query(...)):
    """读取终端累积输出。"""
    try:
        data = await terminal.read(terminalSessionId)
        return _ok({"data": data})
    except ValueError as e:
        logger.warning("读取终端失败: %s", e)
        return _err(str(e))


@router.post("/resize")
async def resize_terminal(req: ResizeTerminalRequest):
    """调整终端窗口大小。"""
    try:
        await terminal.resize(req.terminal_session_id, req.cols, req.rows)
        return _ok()
    except ValueError as e:
        logger.warning("调整终端失败: %s", e)
        return _err(str(e))


@router.post("/close")
async def close_terminal(terminalSessionId: str = Query(..., alias="terminalSessionId")):
    """关闭终端会话。"""
    ok = await terminal.close(terminalSessionId)
    if ok:
        return _ok()
    return _err("终端会话不存在")


# ── 单命令执行 ───────────────────────────────────────────────

@router.post("/exec")
async def exec_command(req: ExecCommandRequest):
    """执行单条命令（非交互式，不走 PTY）。"""
    try:
        stdout, stderr = await terminal.exec_command(
            connection_id=req.connection_id,
            command=req.command,
            timeout=req.timeout,
        )
        return _ok({"stdout": stdout, "stderr": stderr})
    except ValueError as e:
        logger.warning("命令执行失败: %s", e)
        return _err(str(e))
