# 服务重启后续聊，session_id 未兜底导致工具集体消失（Unknown tool name）

> 日期：2026-07-22
> 状态：已解决
> 影响范围：服务重启（或内存连接池清空）后，前端未走"恢复连接"成功路径就继续续聊的场景

## 现象

服务重启后，用户在同一个历史对话里继续说"检查下目前的进度"，agent 报错崩溃：

```
pydantic_ai.exceptions.ModelRetry: Unknown tool name: 'execute_command'. Available tools: 'record_milestone'
pydantic_ai.exceptions.UnexpectedModelBehavior: Tool 'execute_command' exceeded max retries count of 1.
```

LLM 明明在这个对话的历史记录里已经连续调用过 8 次 `execute_command`，这一轮却突然"看不到"这个工具，尝试调用后被框架当成未知工具名直接拒绝，重试 1 次后直接抛异常，整个对话崩溃。

## 根因

链路：

1. 服务重启，内存里的 `ssh_sessions` 连接池被清空
2. 前端切换/加载历史对话时会调 `restoreConnectionUI(connId)`，请求 `GET /ssh/is_connected`
3. `session.is_connected()` **只做内存 dict 检查 + 心跳，没有 DB 兜底重连**（这是设计如此——`is_connected` 是纯查询接口，不应该有副作用去建立新连接）：
   ```python
   async def is_connected(connection_id: str) -> bool:
       conn = ssh_sessions.get(connection_id)
       if conn is None:
           return False   # 重启后必然直接返回 False
       ...
   ```
   于是重启后必然返回 `connected: false`，`restoreConnectionUI` 恢复失败，只打一条日志：
   ```js
   log('SYS', '该对话关联的 SSH 连接已断开，如需操作请重新连接', 'sys');
   ```
4. 但前端**没有因为恢复失败而禁用输入框**（`chatInput`/`sendBtn` 本来就没设置 disabled），用户完全可以正常继续发消息
5. 于是这一轮请求 `POST /chat_stream` 时 `session_id` 传的是空字符串 `state.connectionId || ''`
6. 后端 `require_connection` 的 DB 兜底逻辑（troubleshooting/013 号问题修复引入）专门处理"内存未命中但 DB 有记录"的场景，但前提是要有 `session_id` 才能去查 DB：
   ```python
   async def require_connection(ctx, tool_def):
       conn = ssh_sessions.get(ctx.deps.session_id)
       if conn is not None and not conn.is_closed():
           return tool_def
       if not ctx.deps.session_id:   # ← session_id 是空字符串，直接命中这里
           return None                # 工具被隐藏，即使这个对话本来绑定了一台机器
       ...
   ```
   `session_id` 是空字符串时，直接判定为"从未连接过"，工具集体隐藏——但实际上这个对话在数据库 `conversations.connection_id` 字段里，一直记着它绑定的是哪台机器。
7. LLM 看不到 `execute_command` 但历史记忆里明明用过，仍然尝试调用 → 框架识别不到这个工具名，走的是框架默认 `default_max_retries=1`（而不是 `execute_command` 自己配置的 `max_retries=2`）→ 1 次失败后直接 `UnexpectedModelBehavior` 崩溃退出整个对话

本质上是**前端连接恢复失败时没有阻断用户继续操作**，加上**后端续聊时没有用对话已绑定的 `connection_id` 兜底空的 `session_id`**，两个环节都没兜底，叠加导致的。

## 解决方案

后端兜底（这次修的）：续聊时如果前端没传 `session_id`，但这个对话本身在 DB 里绑定过 `connection_id`，就用它兜底：

```python
# chat.py
if req.conversation_id:
    conv_id = req.conversation_id
    conv = await repo_conversation.get_conversation(conv_id)
    ...
    # session_id 兜底：前端未传时，用该对话绑定的 connection_id 兜底，
    # 这样 require_connection/get_session 现有的 DB 自动重连机制才能接上
    if not deps.session_id and conv and conv.connection_id:
        deps.session_id = conv.connection_id
        logger.info("session_id 未传，回退到对话绑定的 connection_id=%s", conv.connection_id[:12])
```

这样一来，即使前端因为 `is_connected` 返回 `false` 没能恢复连接状态，只要用户仍在这个对话里继续操作，后端也能拿到正确的 `connection_id`，走通 `require_connection` 的 DB 兜底 → `get_session` 的自动重连，工具照常可见、照常能执行。

## 效果

- 服务重启后，用户无需重新点击"连接"，直接在原对话里继续说话，工具依然可见、能正常执行（连接由 `get_session` 自动从 DB 重连）
- 即使前端连接恢复 UI 一直显示"未连接"，只要对话本身绑定过机器，后续操作也不会因为 `session_id` 空字符串被误判成"从未连接过"

## 遗留问题（未在本次修复，需要后续跟进）

前端 `restoreConnectionUI` 恢复失败时只打日志，没有禁用 `chatInput`/`sendBtn`，导致用户可以在"看起来未连接"的状态下正常发消息——这本身是个 UX 上的隐患（用户预期应该是"未连接就不能发"，实际却能发且后端偷偷兜底成功）。目前靠本次的后端兜底掩盖了这个问题，但如果某个对话确实没有绑定过 `connection_id`（纯聊天场景），这条路径不会生效，用户体验仍然是"看起来能发，工具却不可用"。建议后续让 `is_connected` 检查失败时前端也给出更明确的提示，或者干脆让前端在检测失败时主动尝试重连一次。

## 与其他问题的关联

和 013 号问题（工具可见性判断标准不一致）同属"连接状态判断的兜底链路不完整"问题族：

| 问题 | 缺失的兜底环节 |
|------|------|
| [013](./013-tool-visibility-mismatch.md) | `require_connection` 只查内存，不查 DB，即使传了 `session_id` 也可能误隐藏 |
| **016（本问题）** | `require_connection` 有 DB 兜底了，但前提是要有 `session_id`；`session_id` 本身在传递链路上（前端恢复失败 → 传空字符串）就丢了 |

经验教训：修复"最后一步"的兜底逻辑（013 号修的 `require_connection`）不代表整条链路都健壮——`session_id` 从"用户点击连接"到"传进 `require_connection`"要经过前端状态恢复、HTTP 请求体、`deps` 构造多个环节，任何一个环节都可能把它变成空值，每个環節都要问一句"如果这个值是空的，有没有更权威的数据源可以兜底"。
