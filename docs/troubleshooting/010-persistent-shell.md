# execute_command 的 shell 无状态化（cd 不跨调用生效）

> 日期：2026-07-22
> 状态：已解决
> 影响范围：多步骤运维/部署任务（跨命令的 cd、环境变量）

## 现象

009 号方案排查部署认知混淆时发现的底层技术根因：agent 执行 `cd /path` 后，下一次 `execute_command` 调用 `pwd` 看到的仍是登录目录，不是 `/path`。此前一直靠模型每次手动拼 `cd /path && 真正命令` 来"手动模拟"持久化，模型忘记加时就出错。

## 根因

`terminal.py::exec_command` 每次调用都是 `conn.run(command)`——asyncssh 里每次都会开一条全新的 SSH channel，不共享任何 shell 进程或状态。`cd` 只在那一次性 channel 的临时 shell 里生效，channel 关闭即丢失。

## 借鉴

调研 OpenHands 后确认：它在每个会话的容器内维持一个**持久 Bash Shell 进程**，客户端显式追踪 cwd 状态，而不是每次开新 shell。这验证了持久 shell 是业界通用解法，不是我们自己的特例。

## 解决方案

新增 `ssh/persistent_shell.py`：每个 `conversation_id` 绑定一条持久 `bash --noprofile --norc` 进程（`create_process`，非 PTY，管道干净无回显干扰）。

- **命令边界**：给 stdout/stderr 各自追加一个随机 token 的 sentinel 标记，并发读两条流直到各自读到标记，从标记行解析退出码
- **串行化**：`asyncio.Lock` 保证同一条 shell 内命令不并发写入交叉
- **超时恢复**：超时先尝试发 `Ctrl+C` 中断挂起命令 + 探测响应；恢复失败则整条 shell 标记失效
- **失效重建**：`get_shell()` 检测到失效自动重建，返回 `was_rebuilt=True`
- **模型可感知**（呼应 007/008 号经验——软提示不可靠）：`execute_command` 检测到 `was_rebuilt=True` 时，在返回结果**最前面**强制插入一行提示"执行环境已重新创建，之前的 cd 目录已丢失"，而不是只写进 system prompt 让模型自己留意
- **连接断开清理**：`session.py::disconnect()` 时清理该连接下所有持久 shell，避免占用已失效的 channel 引用
- **保守边界**：只有 `conversation_id` 存在时才走持久 shell；`get_environment` 等一次性并行探测继续走原 `terminal.exec_command`，不接入（它们本不依赖 cwd）

## 效果

新增 `tests/test_persistent_shell.py`（本地起临时 SSH server 代理到真实 bash，无需系统 sshd），5 项验证全部通过：

```
[PASS] cd 状态跨调用持久化成功
[PASS] stdout/stderr 正确分离
[PASS] 退出码正确解析
[PASS] 无输出交叉（并发串行化生效）
[PASS] 崩溃重建 + was_rebuilt 标记正确
```

## 后续

这是 009 号工作区隔离方案的第一步（P0-1a：修 shell 无状态化）。下一步（1b）是在此基础上加 `workspace_path` 钉住 + 结果强制回显当前工作区，解决"认错项目"的问题。
