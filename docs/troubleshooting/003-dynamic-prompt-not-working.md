# 动态 system prompt 不生效（dynamic=False）

> 日期：2026-07-20
> 状态：已解决
> 影响范围：权限控制、连接状态感知

## 现象

用 `@agent.system_prompt` 注册了连接状态检测函数：

```python
@agent.system_prompt
async def connection_status(ctx: RunContext[AgentDeps]) -> str:
    connected = await session.is_connected(ctx.deps.session_id)
    if connected:
        return "SSH 已连接，可以执行命令"
    return "未连接 SSH，只能提供建议"
```

- 第一次对话（未连接）：函数被调用，返回"未连接" ✓
- 连接 SSH 后发消息：函数**没有被调用**，LLM 仍看到"未连接"
- 断开后再发：函数还是没被调用

## 根因

`@agent.system_prompt` 装饰器有 `dynamic` 参数，**默认 `False`**：

```python
def system_prompt(self, func=None, /, *, dynamic: bool = False):
```

- `dynamic=False`：函数只调用一次，结果存入消息历史，后续轮次复用不重新调用
- `dynamic=True`：每次 LLM 请求都重新调用，即使有 `message_history` 也重新评估

源码验证（`_system_prompt.py`）：
```python
if runner.dynamic:
    parts.append(SystemPromptPart(prompt, dynamic_ref=...))  # 带 ref，后续重新评估
elif prompt:
    parts.append(SystemPromptPart(prompt))  # 不带 ref，只调用一次
```

## 解决方案

加 `dynamic=True`：

```python
@agent.system_prompt(dynamic=True)
async def connection_status(ctx: RunContext[AgentDeps]) -> str:
    ...
```

## 效果

每次 LLM 请求都重新查 SSH 状态：
- 连接中 → prompt 说"已连接" → agent 执行命令
- 断开后 → prompt 说"未连接" → agent 给建议不执行
- 运行中途断开 → 下一轮请求自动更新 prompt
