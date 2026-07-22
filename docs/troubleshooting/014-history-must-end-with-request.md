# 部署跑十几分钟后报错：Processed history must end with a `ModelRequest`

> 日期：2026-07-22
> 状态：已解决
> 影响范围：所有触发 token 裁剪（`TokenBudgetTrimmer`）的长对话/长部署任务

## 现象

部署类任务运行到十几分钟、历史消息变长后，agent 突然报错终止：

```
Processed history must end with a `ModelRequest`
```

任务越复杂（工具调用越多、单条工具输出越大），越容易触发；短对话完全不会遇到。

## 根因

pydantic-ai 硬性要求：`ProcessHistory` 处理完的历史，最后一条必须是 `ModelRequest`（否则框架无法把下一轮的
assistant 回复接续上去）。

而 `TokenBudgetTrimmer.process` 在按 token 预算从后往前累加时，是把**整个 history（含最后一条）**都当作普通消息参与裁剪：

```python
# 修复前
result: list[ModelMessage] = []
used = 0
for i in range(len(history) - 1, -1, -1):
    msg = history[i]
    msg_tokens = _estimate_tokens(msg)
    ...
    if used + msg_tokens > self.token_budget:
        ... # 非重要消息直接跳过，不放进 result
```

当最后一条消息本身很大（比如一次性贴了很长的日志/配置文件作为新的 user-prompt），且此时预算已经被前面的历史用满，
**最后一条也会被当成"超预算的普通消息"直接跳过**——裁剪结果里根本没有它。如果它恰好又不带 `[!KEEP]` 标记，
就会被整条丢弃，导致处理后的历史结尾不再是 `ModelRequest`（甚至可能整个 result 是空的，或者结尾是孤立的
`ModelResponse`）。

另外开头对齐逻辑也有一个协同问题：

```python
# 修复前
while result and not isinstance(result[0], ModelResponse):
    ...
    result.pop(0)
```

如果 `result` 只剩 1 条（就是那条本该保留的最后消息），且它不是 `ModelResponse`，这个循环会把它也 pop 掉，
让 `result` 变成空列表——最后连"至少保留最后一条"这个兜底都没有。

## 解决方案

两处修复，`token_trimmer.py` + `context/__init__.py` 双重保险：

**1. `TokenBudgetTrimmer`：最后一条强制保留，不参与预算裁剪**

```python
# 最后一条是当前轮要回复的请求，无论多大都必须保留
last_msg = history[-1]
result: list[ModelMessage] = [last_msg]
used = _estimate_tokens(last_msg)

# 只从倒数第二条开始往前按预算裁剪
for i in range(len(history) - 2, -1, -1):
    ...
```

开头对齐循环也加上"不能把唯一剩下的最后一条弹出"的保护：

```python
while len(result) > 1 and not isinstance(result[0], ModelResponse):
    if has_keep_marker(result[0]):
        break
    result.pop(0)
```

**2. `trim_history` 管道出口：统一兜底对齐**

不管管道里哪个处理器（`Compactor`/`Deduplicator`/`Summarizer`/`PriorityProtector`/`TokenBudgetTrimmer`）
未来怎么改，都可能意外破坏"结尾必须是 `ModelRequest`"的约束，所以在管道出口再加一层统一保护：

```python
async def trim_history(history: list[ModelMessage]) -> list[ModelMessage]:
    result = await _pipeline.run(history)

    # 结尾对齐：从末尾去掉非 ModelRequest 的消息
    while result and not isinstance(result[-1], ModelRequest):
        result.pop()

    # 极端兜底：全被去光了，回退到原始 history 的最后一条 ModelRequest
    if not result:
        for msg in reversed(history):
            if isinstance(msg, ModelRequest):
                return [msg]
        return history

    return result
```

## 效果

新增 `tests/test_trim_history_tail.py` 覆盖三个场景，全部通过：

```
测试1: 正常长历史，结尾应为 ModelRequest              -> [PASS]
测试2: 最后一条超大 ModelRequest（超预算）必须保留    -> [PASS]
测试3: 管道产出结尾是 ModelResponse，兜底应去掉它      -> [PASS]
```

长部署任务不再在历史变长后中途报错终止。

## 与其他问题的关联

和 001 号问题（`trim_history` 裁到只剩 5 条导致失忆）同属"历史裁剪破坏消息序列结构完整性"的问题族，
只是这次破坏的是 pydantic-ai 的硬性协议约束（结尾必须是 `ModelRequest`），而不是丢内容。
经验教训：**任何裁剪/压缩逻辑都必须显式区分"参与裁剪的候选消息"和"结构性约束锚点消息"（如最后一条、
配对的 tool-call/tool-return），后者不能被普通裁剪规则一视同仁地处理**，并在管道最终出口做一次
"协议合法性"兜底校验，不要依赖每个处理器都自觉遵守。
