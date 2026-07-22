# 工具可见性与实际执行判断标准不一致（内存清空后工具集体消失）

> 日期：2026-07-22
> 状态：已解决
> 影响范围：所有依赖 SSH 连接的工具（execute_command/read_file/write_file/list_directory/get_environment/run_background/check_task）

## 现象

部署过程中用户问"项目部署完了吗"，agent 回复"我目前只有记录里程碑的工具可用，无法直接执行命令"，让用户手动跑命令。但这条对话此前的轮次里，agent 明明用过 `list_directory`、`execute_command`。

从模型的思考过程可以看到它自己也困惑："之前用过这些工具，但这轮工具不见了……让我重新看看我的工具列表"——模型对自己能力边界的认知和实际情况脱节。

## 根因

`require_connection`（工具可见性的 prepare 钩子）和 `execute_command`（工具实际执行逻辑）用了两套不一致的判断标准：

```python
# tools.py::require_connection —— 决定工具是否展示给模型
conn = ssh_sessions.get(ctx.deps.session_id)   # 只查内存 dict
if conn is not None and not conn.is_closed():
    return tool_def
return None   # 内存没有 → 直接隐藏，不做任何重连尝试
```

而 `execute_command` 真正执行时走 `persistent_shell.get_shell()` → `session.get_session()`，这里**有自动重连**：只要 DB 里有连接记录，就会自动从 DB 重连成功。

一旦内存里的 `ssh_sessions` 因任何原因清空（服务重启、`is_connected()` 心跳失败主动 pop、新进程接手对话），`require_connection` 立刻判定"未连接"，把所有需要连接的工具全部隐藏——但实际上只要真的调用 `execute_command`，它自己是能重连成功的。工具被隐藏纯粹是"看门人"比"执行者"更严格，不代表真的没法连。

这也解释了模型给出的错误建议：模型不知道问题出在"连接状态判断过严"，它只知道"我现在没有这些工具"——能力边界的错误信息被传递给了模型，模型只能顺着这个错误信息给建议。

## 解决方案

让 `require_connection` 的判断标准对齐 `execute_command` 实际执行时的标准：

```python
async def require_connection(ctx, tool_def):
    conn = ssh_sessions.get(ctx.deps.session_id)
    if conn is not None and not conn.is_closed():
        return tool_def
    if not ctx.deps.session_id:
        return None
    # 内存未命中，但 DB 里有连接记录 → 仍展示工具，重连交给实际调用时处理
    from haossh.db import repo_connection
    if await repo_connection.get(ctx.deps.session_id):
        return tool_def
    return None
```

热路径（内存命中，正常连接中）性能不变；只有内存未命中的冷路径才多查一次 DB（远快于网络心跳），不破坏原本"纳秒级返回"的设计目标。

## 效果

验证 3 个场景：
```
场景1 (未连接过，session_id 为空): 隐藏
场景2 (session_id 有值但 DB 无记录): 隐藏
场景3 (内存无/DB有，模拟重启后的情况): 展示 ← 修复前会被误隐藏
```

## 与其他问题的关联

这类 bug 的模式是"两处逻辑各自独立演化，判断标准逐渐脱节"——006 号问题（SSH 半开连接）也是类似的"表面状态和真实状态不一致"，只是那次是 `is_closed()` 的判断粒度不够细，这次是"是否展示"和"是否能执行"两处代码各写各的标准。以后新增/修改任何 `prepare` 钩子时，都要对照它所属工具的实际执行路径，确认两者的失败/成功判断标准是同一套逻辑。
