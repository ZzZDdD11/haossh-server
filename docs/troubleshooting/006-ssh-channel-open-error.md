# SSH 连接半开状态导致工具失败（ChannelOpenError）

> 日期：2026-07-21
> 状态：已解决
> 影响范围：SSH 长时间操作后的工具调用

## 现象

部署过程中，`ls -la` 等快速命令也失败，反复重试耗尽 max_retries：

```
WARNING 命令执行失败 command=ls -la /opt/haossh-server/ error=open failed
asyncssh.misc.ChannelOpenError: open failed
ModelRetry: 命令执行失败: open failed
UnexpectedModelBehavior: Tool 'execute_command' exceeded max retries count of 2
```

## 根因

SSH 连接处于**半开状态**——`conn.is_closed()` 返回 `False`（看起来活着），但实际无法打开新 channel。

`get_session` 的重连逻辑只检查 `is_closed()`：
```python
async def get_session(connection_id):
    conn = ssh_sessions.get(connection_id)
    if conn is not None and not conn.is_closed():
        return conn  # ← 半开连接在这里返回，不触发重连
    return await _reconnect(connection_id)
```

半开连接通过了 `is_closed()` 检查，`get_session` 认为正常直接返回。但 `conn.run()` 时 asyncssh 无法打开新 channel，抛 `ChannelOpenError: open failed`。

**半开状态怎么产生的**：长时间操作中 SSH 连接因网络抖动、服务器超时、非 UTF-8 输出等原因实际已断开，但 TCP 层面还没感知到，`is_closed()` 仍返回 `False`。

## 解决方案

在 `exec_command` 里捕获 `ChannelOpenError`，强制重连后重试：

```python
async def exec_command(connection_id, command, timeout=30):
    conn = await get_session(connection_id)

    try:
        result = await conn.run(command, timeout=timeout)
    except asyncssh.misc.ChannelOpenError:
        # 连接半开状态，强制重连
        logger.warning("SSH channel 打开失败，强制重连 connection_id=%s", connection_id)
        from haossh.ssh.session import _reconnect
        conn = await _reconnect(connection_id)
        result = await conn.run(command, timeout=timeout)

    ...
```

## 与 005 的区别

| 问题 | 原因 | 检测方式 | 修复 |
|------|------|---------|------|
| [005](./005-ssh-connection-drop.md) | 连接完全断开 | `is_closed()=True` | `get_session` 自动重连 |
| **006（本问题）** | 连接半开 | `is_closed()=False` 但 `conn.run()` 抛 `ChannelOpenError` | `exec_command` 捕获异常后强制重连 |

005 解决了"连接明确断开"的情况，006 解决了"连接看起来活着但实际不可用"的半开状态。两层防护叠加。

## 效果

- 半开连接 → `conn.run()` 抛 `ChannelOpenError` → 捕获 → `_reconnect` 重连 → 重试成功
- agent 无感知，命令正常执行
- 不额外开销——只在真正失败时才重连
