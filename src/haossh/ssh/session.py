from asyncssh import SSHClientConnection
import asyncssh
import logging

logger = logging.getLogger(__name__)

ssh_sessions: dict[str, SSHClientConnection] = {}




async def connect(connection_id: str, host: str, port: int, username: str, password: str) -> bool:
    try:
        conn = await asyncssh.connect(
            host=host, username=username, password=password, port=port,
            known_hosts=None,  # 跳过主机密钥验证（调试场景，等同 StrictHostKeyChecking=no）
        )
        ssh_sessions[connection_id] = conn
        logger.info("SSH 连接成功 connection_id=%s %s@%s:%s", connection_id, username, host, port)
        return True
    except Exception as e:
        logger.warning("SSH 连接失败 connection_id=%s %s@%s:%s reason=%s", connection_id, username, host, port, e)
        return False


async def disconnect(connection_id: str) -> bool:
    conn = ssh_sessions.pop(connection_id, None)
    if conn is None:
        logger.warning("SSH 断开连接失败：连接不存在 connection_id=%s", connection_id)
        return False
    try:
        conn.close()
        await conn.wait_closed()
        logger.info("SSH 连接已断开 connection_id=%s", connection_id)
        return True
    except Exception as e:
        logger.warning("SSH 断开连接异常 connection_id=%s reason=%s", connection_id, e)
        return False


async def is_connected(connection_id: str) -> bool:
    """检查连接是否真正存活。第一层 dict 检查，第二层心跳验证。"""
    conn = ssh_sessions.get(connection_id)
    if conn is None:
        logger.debug("连接检查：不在连接池 connection_id=%s", connection_id)
        return False
    if conn.is_closed():
        logger.warning("连接检查：已关闭，清理连接池 connection_id=%s", connection_id)
        ssh_sessions.pop(connection_id, None)
        return False
    try:
        result = await conn.run("echo ok", timeout=5)
        return result.stdout is not None and result.stdout.strip() == "ok"
    except Exception as e:
        logger.warning("连接检查：心跳失败，清理连接池 connection_id=%s reason=%s", connection_id, e)
        ssh_sessions.pop(connection_id, None)
        return False


def get_session(connection_id: str) -> SSHClientConnection | None:
    """获取连接对象，不存在则返回 None。"""
    return ssh_sessions.get(connection_id)