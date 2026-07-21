# SSH 连接断开导致工具失败（open failed）

> 日期：2026-07-20
> 状态：已解决
> 影响范围：长时间操作后工具调用

## 现象

部署过程中 SSH 连接断开，后续命令全部失败：

```
INFO [asyncssh] Connection failure: 'utf-8' codec can't decode byte 0xe9
WARNING 命令执行失败 command=ls -la error=open failed
```

反复超时后 SSH 连接耗断，`ssh_sessions` 里的连接对象失效。

## 根因

1. **SSH 连接不稳定**：长时间操作 + 非 UTF-8 输出导致 asyncssh 连接断开
2. **无自动重连**：连接断了后 `ssh_sessions` 里还存着旧对象，工具拿到的是失效连接
3. **错误传播**：`open failed` 异常 → `ModelRetry` → LLM 重试 → 又失败 → 超过 max_retries

## 解决方案

在 `session.py` 的 `get_session` 里加自动重连：

```python
async def get_session(connection_id: str) -> SSHClientConnection:
    conn = ssh_sessions.get(connection_id)
    if conn is not None and not conn.is_closed():
        return conn
    # 连接断了 → 自动重连
    return await _reconnect(connection_id)

async def _reconnect(connection_id: str) -> SSHClientConnection:
    from haossh.db import repo_connection
    from haossh.ssh.security import decrypt

    conn_info = await repo_connection.get(connection_id)
    if not conn_info:
        raise ConnectionError(f"无法重连：{connection_id} 不在数据库中")

    password = decrypt(conn_info.secret_enc)
    conn = await asyncssh.connect(
        host=conn_info.host, port=conn_info.port,
        username=conn_info.username, password=password,
        known_hosts=None,
    )
    ssh_sessions[connection_id] = conn
    return conn
```

所有调用方（`terminal.py`、`file.py`）从 `ssh_sessions.get()` 改成 `await get_session()`。

## 效果

- SSH 断了 → agent 下次调工具时自动重连（从 DB 读加密密码）
- 重连成功 → 命令继续执行，agent 无感
- 重连失败（DB 没记录或服务器挂了）→ 抛 ConnectionError，LLM 告知用户
