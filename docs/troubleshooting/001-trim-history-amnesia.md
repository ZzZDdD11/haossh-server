# agent 对话失忆（trim_history 裁剪到只剩 5 条）

> 日期：2026-07-20
> 状态：已解决
> 影响范围：多轮对话记忆

## 现象

用户在同一对话里发"继续"，agent 回复"我们的对话好像还没有开始过"——完全失忆。

日志排查：
```
续聊 conversation_id=5ecdbb8949c0 历史消息数=43    ← DB 有 43 条
trim_history 输入: 46 条
trim_history 裁剪: 46 -> 5 条                       ← 裁到只剩 5 条！
```

## 根因

`trim_history` 的对齐策略有问题：

```python
MAX_HISTORY_MESSAGES = 40

trimmed = history[-40:]  # 取最后 40 条
for i, msg in enumerate(trimmed):
    if isinstance(msg, ModelRequest):
        for part in msg.parts:
            if getattr(part, "part_kind", None) == "user-prompt":
                return trimmed[i:]  # 从第一个 user-prompt 开始
```

工具调用多的场景，最后 40 条里大部分是 `ModelResponse(tool-call)` + `ModelRequest(tool-return)` 配对。第一个 `user-prompt` 在第 35 个位置，裁完只剩 5 条。

## 解决方案

两个调整：

1. `MAX_HISTORY_MESSAGES` 从 40 提到 100
2. 对齐目标从 `user-prompt` 改为 `ModelResponse`（保证工具配对完整，不丢太多消息）

```python
MAX_HISTORY_MESSAGES = 100

trimmed = history[-100:]
for i, msg in enumerate(trimmed):
    if isinstance(msg, ModelResponse):
        return trimmed[i:]
```

## 效果

46 条消息不再被裁剪（< 100 不触发）。agent 能看到完整历史，不再失忆。
