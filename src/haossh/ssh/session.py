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
    # 清理该连接下所有持久 shell（避免占用已失效的 channel 引用）
    from haossh.ssh.persistent_shell import close_shells_for_connection
    await close_shells_for_connection(connection_id)
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


async def get_session(connection_id: str) -> SSHClientConnection:
    """获取连接对象，断了自动重连1次。

    连接正常直接返回；断了从 DB 读信息重连。
    重连失败（DB 没有记录或服务器不可达）抛 ConnectionError。
    """
    conn = ssh_sessions.get(connection_id)
    if conn is not None and not conn.is_closed():
        return conn
    # 连接断了或不存在，尝试重连
    return await _reconnect(connection_id)


async def _reconnect(connection_id: str) -> SSHClientConnection:
    """从 DB 读连接信息重连。失败抛异常。"""
    # 延迟 import 避免循环依赖
    from haossh.db import repo_connection
    from haossh.ssh.security import decrypt

    conn_info = await repo_connection.get(connection_id)
    if not conn_info:
        raise ConnectionError(f"SSH 连接已断开且无法重连：{connection_id} 不在数据库中")

    password = decrypt(conn_info.secret_enc)
    try:
        conn = await asyncssh.connect(
            host=conn_info.host, port=conn_info.port,
            username=conn_info.username, password=password,
            known_hosts=None,
        )
        ssh_sessions[connection_id] = conn  # 更新连接池
        logger.info("SSH 自动重连成功 connection_id=%s %s@%s:%s",
                    connection_id, conn_info.username, conn_info.host, conn_info.port)
        return conn
    except Exception as e:
        logger.warning("SSH 重连失败 connection_id=%s reason=%s", connection_id, e)
        raise ConnectionError(f"SSH 重连失败: {e}") from e
