# SFTP channel 泄漏耗尽 MaxSessions，导致所有工具集体报 ChannelOpenError

> 日期：2026-07-22
> 状态：已解决
> 影响范围：`read_file`/`write_file`/`list_directory` 等所有 SFTP 相关工具；间接拖累同连接下的 `execute_command`

## 现象

用户让 agent 继续部署，任务进行到"看当前环境状态和项目结构"阶段（连续调用 `read_file`、`execute_command`）时集体报错：

```
命令执行失败 command=ls -la /root/HaoEnglishTeacher/frontend/ error=open failed
命令执行失败 command=docker --version && docker compose version error=open failed
asyncssh.misc.ChannelOpenError: open failed
  File ".../haossh/ssh/file.py", line 33, in _get_sftp
    return await conn.start_sftp_client()
pydantic_ai.exceptions.ModelRetry: 无法访问文件 /root/HaoEnglishTeacher/backend/Dockerfile: open failed
pydantic_ai.exceptions.UnexpectedModelBehavior: Tool 'read_file' exceeded max retries count of 1.
```

`read_file` 重试 1 次直接耗尽 `max_retries` 崩溃；紧接着同一连接上的 `execute_command`（`ls`、`docker --version`）也一起失败，看起来像连接整体坏了，但实际上连接本身完全正常——问题出在 channel 数量上。

## 根因

`haossh/ssh/file.py` 里的 `_get_sftp()` 每次都新开一个 SFTP channel，但**从未关闭**：

```python
# 修复前
async def _get_sftp(connection_id: str) -> asyncssh.SFTPClient:
    conn = await get_session(connection_id)
    return await conn.start_sftp_client()   # 每次开一个新 channel，用完不关

async def get_size(connection_id, path):
    sftp = await _get_sftp(connection_id)   # 开 channel #N，函数返回后 sftp 对象被 GC，
    attrs = await sftp.stat(path)           # 但底层 SSH channel 没有显式 exit()，
    return attrs.size or 0                  # 服务端认为这个 session 仍然存活
```

`read_file` 一次调用内部要调两次 `_get_sftp`（`get_size` + `read_chunk`），日志里能看到同一秒内连续开出 3~4 个 SFTP channel：

```
[conn=1, chan=2] Requesting new SSH session
[conn=1, chan=3] Requesting new SSH session
[conn=1, chan=4] Requesting new SSH session
[conn=1, chan=2]   Subsystem: sftp
...
```

而 OpenSSH 服务端默认 `MaxSessions 10`（同一条 TCP 连接上最多同时打开 10 个 session/channel）。检查完整日志，`chan=1` 到 `chan=10` 全部是 SFTP channel，且**全程没有一条 "Channel closed" 记录**——10 个配额在几次文件读取后就被耗尽。之后无论是新的 SFTP 请求还是普通 `execute_command` 想开的 session channel，都会因为服务端拒绝而抛：

```python
asyncssh.misc.ChannelOpenError: open failed
```

这正是 006 号问题描述的异常类型，但**根因完全不同**：
- 006 是连接"半开"（网络层面已经不可用，只是客户端没感知到）
- 本问题是连接完全正常，只是**同一条连接上的 channel 配额被自己攒的垃圾 SFTP session 占满**

`read_file` 里 `except Exception as e: raise ModelRetry(...)` 把这个报错吞成了"无法访问文件"，LLM 看到的错误信息完全没有暴露"channel 耗尽"的真实原因，重试同样的操作只会再开一个新 channel（此时已经没有配额了），所以重试必然失败，直接打满 `max_retries=1` 崩溃退出整个对话。

## 解决方案

给 SFTP client 补上生命周期管理：用完立即关闭底层 channel，而不是让它"挂"在服务端直到连接断开。

```python
# haossh/ssh/file.py
from contextlib import asynccontextmanager

@asynccontextmanager
async def _sftp_session(connection_id: str) -> AsyncIterator[asyncssh.SFTPClient]:
    """获取 SFTP 客户端，用完自动关闭底层 channel。"""
    conn = await get_session(connection_id)
    sftp = await conn.start_sftp_client()
    try:
        yield sftp
    finally:
        sftp.exit()              # 请求关闭 SFTP session
        await sftp.wait_closed()  # 等 channel 真正释放
```

所有文件操作函数（`list_dir`/`read_content`/`read_chunk`/`get_size`/`create_file`/`rename_file`/`delete`/`upload`/`download` 等）统一改为：

```python
async def get_size(connection_id: str, path: str) -> int:
    async with _sftp_session(connection_id) as sftp:
        attrs = await sftp.stat(path)
        return attrs.size or 0
```

每次操作用完即释放 channel，不再有累积泄漏。

## 效果

- 文件操作完成后立即释放 SFTP channel，`MaxSessions` 配额不再被占满
- 连续多次 `read_file`/`list_directory` 不再拖累同连接下其他工具（`execute_command` 等）
- 重启服务并重新连接后验证：部署流程里反复读取多个文件 + 穿插执行命令，不再出现 `ChannelOpenError`

## 与其他问题的关联

和 006 号问题（SSH 半开连接导致 `ChannelOpenError`）报的是**同一种异常**，容易被误判为同一个根因，但排查方向完全不同：

| 问题 | 现象 | 连接本身状态 | 根因 | 排查关键点 |
|------|------|------|------|------|
| [006](./006-ssh-channel-open-error.md) | 快速命令也失败 | 半开（`is_closed()=False` 但不可用） | 网络层面连接实际已断 | 强制重连即可恢复 |
| **015（本问题）** | 文件操作后所有工具集体失败 | 完全正常 | 自己的代码泄漏 channel，打满服务端配额 | 数 channel 有没有对应的 "Channel closed"，而不是只看连接是否 `is_closed()` |

排查方法论：遇到 `ChannelOpenError` 不要默认套用"重连"这一种解法——先看日志里同一 `conn=` 下 channel 的开/关是否配对，如果开得多、关得少，说明是资源泄漏而非连接失效，重连治标不治本（新连接建立后同样的代码会重新把 channel 攒满）。任何 `start_sftp_client()`/`create_process()`/`open_session()` 之类会在 SSH 连接上"开新 session"的调用，都要显式在 `finally` 里关闭，不能依赖垃圾回收或连接断开来自动释放。
