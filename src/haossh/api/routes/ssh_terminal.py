"""SSH 终端相关 API 路由。"""

import asyncio
import json
import logging

import jwt
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from haossh.api.schemas.ssh_terminal import (
    ExecCommandRequest,
    OpenTerminalRequest,
    ResizeTerminalRequest,
    WriteTerminalRequest,
)
from haossh.auth.security import COOKIE_NAME, decode_token
from haossh.db import repo_connection
from haossh.ssh import terminal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ssh/terminal", tags=["SSH Terminal"])


# ── 辅助 ─────────────────────────────────────────────────────

def _ok(data: object = None) -> dict:
    return {"code": "0000", "info": "成功", "data": data}


def _err(info: str) -> dict:
    return {"code": "1001", "info": info, "data": None}


async def _require_owned_connection(connection_id: str, tenant_id: str) -> None:
    """校验 connectionId 属于当前登录租户，不属于则抛 403（越权/不存在统一处理）。"""
    if not await repo_connection.get_owned(connection_id, tenant_id):
        raise HTTPException(status_code=403, detail=f"连接不存在或无权访问: {connection_id}")


async def _require_owned_terminal(terminal_session_id: str, tenant_id: str) -> None:
    """校验 terminalSessionId 背后绑定的 connectionId 属于当前登录租户。

    write/read/resize/close 只带 terminalSessionId，先反查它绑定的
    connection_id，再走跟其他接口一样的归属校验，堵住"猜中别人 terminalSessionId
    就能操作别人终端"这个越权路径。
    """
    connection_id = terminal.get_connection_id(terminal_session_id)
    if connection_id is None:
        raise HTTPException(status_code=404, detail=f"终端会话不存在: {terminal_session_id}")
    await _require_owned_connection(connection_id, tenant_id)


# ── 打开 / 关闭 / 读写 ────────────────────────────────────────

@router.post("/open")
async def open_terminal(req: OpenTerminalRequest, request: Request):
    """打开 PTY 终端。需先通过 /ssh/connect 建立 SSH 连接。"""
    await _require_owned_connection(req.connection_id, request.state.tenant_id)
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
async def write_terminal(req: WriteTerminalRequest, request: Request):
    """向终端写入数据。"""
    await _require_owned_terminal(req.terminal_session_id, request.state.tenant_id)
    try:
        await terminal.write(req.terminal_session_id, req.data)
        return _ok()
    except ValueError as e:
        logger.warning("写入终端失败: %s", e)
        return _err(str(e))


@router.get("/read")
async def read_terminal(request: Request, terminalSessionId: str = Query(...)):
    """读取终端累积输出。"""
    await _require_owned_terminal(terminalSessionId, request.state.tenant_id)
    try:
        data = await terminal.read(terminalSessionId)
        return _ok({"data": data})
    except ValueError as e:
        logger.warning("读取终端失败: %s", e)
        return _err(str(e))


@router.post("/resize")
async def resize_terminal(req: ResizeTerminalRequest, request: Request):
    """调整终端窗口大小。"""
    await _require_owned_terminal(req.terminal_session_id, request.state.tenant_id)
    try:
        await terminal.resize(req.terminal_session_id, req.cols, req.rows)
        return _ok()
    except ValueError as e:
        logger.warning("调整终端失败: %s", e)
        return _err(str(e))


@router.post("/close")
async def close_terminal(request: Request, terminalSessionId: str = Query(..., alias="terminalSessionId")):
    """关闭终端会话。"""
    await _require_owned_terminal(terminalSessionId, request.state.tenant_id)
    ok = await terminal.close(terminalSessionId)
    if ok:
        return _ok()
    return _err("终端会话不存在")


# ── 单命令执行 ───────────────────────────────────────────────

@router.post("/exec")
async def exec_command(req: ExecCommandRequest, request: Request):
    """执行单条命令（非交互式，不走 PTY）。"""
    await _require_owned_connection(req.connection_id, request.state.tenant_id)
    try:
        stdout, stderr, exit_status = await terminal.exec_command(
            connection_id=req.connection_id,
            command=req.command,
            timeout=req.timeout,
        )
        return _ok({"stdout": stdout, "stderr": stderr, "exitStatus": exit_status})
    except ValueError as e:
        logger.warning("命令执行失败: %s", e)
        return _err(str(e))


# ── WebSocket 实时终端 ──────────────────────────────────────
# 注意：JWTAuthMiddleware 只拦截 scope["type"] == "http" 的请求，WebSocket
# 连接（scope["type"] == "websocket"）不经过它，必须在这里手动校验一次。

def _authenticate_websocket(websocket: WebSocket) -> dict:
    """从 WebSocket 握手请求的 Cookie 里解出并校验 JWT，返回身份信息。

    Raises:
        ValueError: 未登录或 token 无效/过期
    """
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        raise ValueError("未登录")
    try:
        return decode_token(token)
    except jwt.InvalidTokenError as e:
        raise ValueError("登录态已失效，请重新登录") from e


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket, connectionId: str = Query(...)):
    """实时终端 WebSocket：人在浏览器里直接操作的真 PTY 会话。

    协议：前端 -> 后端 使用 JSON 消息：
    - {type: "input", data: "..."} 写入 PTY
    - {type: "resize", cols: 120, rows: 35} 调整 PTY 窗口大小
    后端 -> 前端 仍然直接推原始 PTY 输出文本。
    """
    try:
        payload = _authenticate_websocket(websocket)
    except ValueError as e:
        # 未 accept() 前拒绝，浏览器侧会收到连接失败（不会进入正常的 close 流程）
        await websocket.close(code=4401, reason=str(e))
        return

    try:
        await _require_owned_connection(connectionId, payload["tenant_id"])
    except HTTPException as e:
        await websocket.close(code=4403, reason=str(e.detail))
        return

    await websocket.accept()

    terminal_session_id = None
    reader_task = None
    try:
        terminal_session_id = await terminal.create(connection_id=connectionId)
        logger.info(
            "实时终端 WebSocket 已建立 terminal_session_id=%s connection_id=%s user_id=%s",
            terminal_session_id[:12], connectionId[:12], payload["sub"][:12],
        )

        async def pump_output():
            """后台任务：PTY 有输出就推给浏览器。"""
            async for chunk in terminal.read_stream(terminal_session_id):
                await websocket.send_text(chunk)

        reader_task = asyncio.create_task(pump_output())

        # 主循环：读浏览器发来的 JSON 消息，按类型处理输入/窗口 resize
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("实时终端收到非法 JSON 消息 terminal_session_id=%s", terminal_session_id[:12])
                continue

            msg_type = msg.get("type")
            if msg_type == "input":
                await terminal.write(terminal_session_id, msg.get("data", ""))
            elif msg_type == "resize":
                cols = int(msg.get("cols", 0))
                rows = int(msg.get("rows", 0))
                if cols > 0 and rows > 0:
                    await terminal.resize(terminal_session_id, cols, rows)
            else:
                logger.warning("实时终端收到未知消息类型 terminal_session_id=%s type=%s", terminal_session_id[:12], msg_type)
    except WebSocketDisconnect:
        logger.info("实时终端 WebSocket 已断开 terminal_session_id=%s", (terminal_session_id or "")[:12])
    except Exception as e:
        logger.warning("实时终端 WebSocket 异常 terminal_session_id=%s: %s", (terminal_session_id or "")[:12], e)
    finally:
        if reader_task:
            reader_task.cancel()
        if terminal_session_id:
            await terminal.close(terminal_session_id)
