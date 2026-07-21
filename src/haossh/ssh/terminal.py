"""PTY 终端会话管理。

每个 TerminalSession 对应远程服务器上一个交互式 shell 进程。
通过 SSH 通道读写 PTY，实现类似于本地 Terminal 的体验。

读写模型：
  write: chan.write(data) → 远端 bash 收到输入
  read:  远端 bash 输出 → SSHClientSession.data_received() → 内存 buffer → read() 消费
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass

import asyncssh

from haossh.ssh.session import get_session

logger = logging.getLogger(__name__)


# ── 自定义 Session：捕获远程输出到 buffer ──────────────────────

class _TerminalClientSession(asyncssh.SSHClientSession):
    """自定义 SSH 客户端会话——将远端 bash 输出写入内存 buffer。"""

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._eof: bool = False

    def data_received(self, data: str, datatype: asyncssh.DataType) -> None:  # noqa: ARG002
        """远端 bash 有输出时自动回调。"""
        self._buffer.append(data)

    def connection_lost(self, exc: Exception | None) -> None:  # noqa: ARG002
        """远端 bash 退出/连接断开时回调。"""
        self._eof = True


# ── 数据结构 ─────────────────────────────────────────────────

@dataclass
class TerminalSession:
    terminal_session_id: str
    connection_id: str
    chan: asyncssh.SSHClientChannel
    client_session: _TerminalClientSession


terminals: dict[str, TerminalSession] = {}


# ── 辅助 ─────────────────────────────────────────────────────

def _get(terminal_session_id: str) -> TerminalSession:
    ts = terminals.get(terminal_session_id)
    if ts is None:
        raise ValueError(f"终端会话不存在: {terminal_session_id}")
    return ts


# ── 公开 API ─────────────────────────────────────────────────

async def create(connection_id: str, cols: int = 80, rows: int = 24) -> str:
    """在已连接的 SSH 连接上打开 PTY shell。

    Returns:
        terminal_session_id，用于后续读写操作
    """
    conn = await get_session(connection_id)

    client_session = _TerminalClientSession()
    chan, _ = await conn.create_session(
        lambda: client_session,  # pyright: ignore
        term_type="xterm-256color",
        term_size=(cols, rows),
    )

    terminal_session_id = uuid.uuid4().hex
    terminals[terminal_session_id] = TerminalSession(
        terminal_session_id=terminal_session_id,
        connection_id=connection_id,
        chan=chan,
        client_session=client_session,
    )

    logger.info(
        "终端已打开 terminal_session_id=%s connection_id=%s cols=%s rows=%s",
        terminal_session_id, connection_id, cols, rows,
    )
    return terminal_session_id


async def write(terminal_session_id: str, data: str) -> None:
    """向 PTY 写入数据（等同于用户在终端里敲的内容）。"""
    ts = _get(terminal_session_id)
    ts.chan.write(data)


async def read(terminal_session_id: str, timeout: float = 0.1) -> str:
    """读取 PTY 终端的累积输出（消费 buffer 中所有数据）。

    Args:
        terminal_session_id: 终端会话 ID
        timeout: 无数据时的最大等待时间（秒）

    Returns:
        终端输出的字符串，无数据时返回空串
    """
    ts = _get(terminal_session_id)
    buf = ts.client_session._buffer

    # 如果 buffer 为空，短暂等待新数据到来
    if not buf:
        try:
            await asyncio.wait_for(_wait_for_data(ts.client_session), timeout=timeout)
        except asyncio.TimeoutError:
            return ""

    # 消费并清空 buffer
    if buf:
        result = "".join(buf)
        buf.clear()
        return result
    return ""


async def _wait_for_data(session: _TerminalClientSession, poll_interval: float = 0.02) -> None:
    """轮询等待数据到达 buffer。"""
    while not session._buffer and not session._eof:
        await asyncio.sleep(poll_interval)


async def resize(terminal_session_id: str, cols: int, rows: int) -> None:
    """调整 PTY 终端窗口大小。"""
    ts = _get(terminal_session_id)
    ts.chan.change_terminal_size(cols, rows)
    logger.debug("终端窗口已调整 terminal_session_id=%s cols=%s rows=%s", terminal_session_id, cols, rows)


async def close(terminal_session_id: str) -> bool:
    """关闭 PTY 终端会话。"""
    ts = terminals.pop(terminal_session_id, None)
    if ts is None:
        return False

    ts.chan.close()
    await ts.chan.wait_closed()
    logger.info("终端已关闭 terminal_session_id=%s", terminal_session_id)
    return True


async def exec_command(connection_id: str, command: str, timeout: int = 30) -> tuple[str, str, int]:
    """在 SSH 连接上执行单条命令（非交互式，不走 PTY）。

    Returns:
        (stdout, stderr, exit_status)
    """
    conn = await get_session(connection_id)

    try:
        result = await conn.run(command, timeout=timeout)
    except asyncssh.misc.ChannelOpenError:
        # 连接半开状态（is_closed()=False 但无法开 channel），强制重连
        logger.warning("SSH channel 打开失败，强制重连 connection_id=%s", connection_id)
        from haossh.ssh.session import _reconnect
        conn = await _reconnect(connection_id)
        result = await conn.run(command, timeout=timeout)

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    exit_status = result.exit_status if result.exit_status is not None else -1
    logger.debug("命令执行完成 connection_id=%s command=%s exit=%s", connection_id, command, exit_status)
    return str(stdout), str(stderr), exit_status
