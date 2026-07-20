# 03 · 多轮对话与上下文管理

> 当前阶段重点。多轮记忆已实现，上下文管理待做。

## 一、多轮对话（message_history）

### 问题：Agent 的"金鱼记忆"

无状态调用时，Agent 不记得上一轮：
```
用户："看一下磁盘" → Agent 调 df -h → 回复 → 完事
用户："那内存呢"    → Agent 不知道"那"指什么，重新探测
```

### 框架提供的能力（直接调用）

| API | 作用 |
|-----|------|
| `agent.iter(message_history=历史)` | 传入历史，Agent 记得上文 |
| `run.all_messages()` | 取出累积消息（历史+本轮） |
| `run.new_messages()` | 只取本轮新增 |
| `run.all_messages_json()` | 序列化成 JSON（存库用） |
| `ModelMessagesTypeAdapter.validate_json()` | 从 JSON 反序列化（读库用） |
| `run.conversation_id` | 对话标识（跨多轮一致） |

### 本项目实现（`src/haossh/api/routes/chat.py`）

```python
# 全局存储（内存，按 session_id 隔离）
histories: dict[str, list] = {}

async def generator():
    history = histories.get(req.session_id, [])          # 取历史
    async with agent.iter(
        req.message, deps=deps, message_history=history  # 传入
    ) as run:
        ...  # 事件流处理
    histories[req.session_id] = run.all_messages()        # 存累积消息
```

### 关键细节

1. **with 块后存**：`all_messages()` 要在 `async with` 结束后调（run 完成才有完整消息）
2. **累积覆盖**：`all_messages()` 返回历史+本轮，直接覆盖存，不用手动追加
3. **session 隔离**：每个 session_id 一份历史，不同连接互不干扰
4. **内存存储**：重启丢失，Phase 5 落库解决

### 消息形态

`all_messages()` 返回 `list[ModelMessage]`，一轮对话大概：

```python
[
    ModelRequest(parts=[UserPromptPart("看一下磁盘")]),           # 用户消息
    ModelResponse(parts=[                                        # Agent 响应
        ThinkingPart("用户要看磁盘..."),
        ToolCallPart("execute_command", {"command": "df -h"}),
    ]),
    ModelRequest(parts=[                                         # 工具结果回喂
        ToolReturnPart("execute_command", "[exit=0]..."),
    ]),
    ModelResponse(parts=[TextPart("服务器磁盘...")]),             # 最终回复
]
```

## 二、当前问题：全量传递无管理

```python
histories[session_id] = run.all_messages()   # 全量累积
history = histories.get(session_id, [])       # 全量取出
agent.iter(msg, message_history=history)      # 全量传给 LLM
```

### 痛点

| 痛点 | 后果 |
|------|------|
| token 浪费 | 对话越长，每次请求 token 越多（费钱） |
| 超 context window | 历史超模型上限（如 64K），请求失败 |
| 噪音干扰 | 无关旧消息干扰 LLM 决策 |
| 无重点 | 所有消息平等，没"重要的留、不重要的丢" |

## 三、上下文管理的层次

| 层次 | 做法 | 复杂度 |
|------|------|--------|
| L1 滑动窗口 | 只保留最近 N 轮 | 极简 |
| L2 优先级裁剪 | 错误/关键消息优先保留 | 中 |
| L3 摘要压缩 | 旧消息压缩成摘要 | 高 |
| L4 里程碑 | 独立记录关键事件 | 中 |
| L5 上下文增强 | 补充运行时上下文（服务器状态等） | 高 |

## 四、框架的官方方案（待深挖）

文档在"多轮对话与消息历史"里明确提到两个**官方特性**：

### 1. Compaction（消息压缩）

框架自带的消息压缩能力。比手写 trim_history 更完善。

**待查**：Compaction 的具体 API 和接入方式（`HistoryProcessor` 类型已存在，但 `agent.iter`/`Agent.__init__` 的接入参数待确认）。

### 2. Overflowing Tool Output（工具输出溢出处理）

我们 `execute_command` 返回可能很长（`cat` 大文件、`dmesg` 全量日志）。框架有"工具输出溢出处理"——自动管理超长工具结果，防止撑爆 context。

**待查**：具体配置方式。

### 3. HistoryProcessor 类型

查证结果：`HistoryProcessor` 是个**函数类型别名**（不是类），定义了处理器签名：

```python
# 几种等价签名之一
Callable[[list[ModelMessage]], list[ModelMessage]]            # 同步
Callable[[list[ModelMessage]], Awaitable[list[ModelMessage]]]  # 异步
Callable[[RunContext[Deps], list[ModelMessage]], list[ModelMessage]]  # 带 ctx
```

作用：接收原始消息列表，返回处理后的（裁剪/压缩后的）。

**接入方式**：`agent.iter` 和 `Agent.__init__` 都没有 `history_processors` 参数，接入方式待进一步查证（可能通过 capabilities 或别的机制）。

## 五、当前建议

### 短期（自己实现 L1 滑动窗口）

不依赖框架接入，在传 message_history 前手动裁剪：

```python
def trim_history(history: list, max_messages: int = 40) -> list:
    """滑动窗口：只保留最近 N 条，对齐到用户消息边界。
    
    注意：不能切断"工具调用→工具返回"配对，
    否则 LLM 看到孤立工具结果会困惑。
    """
    if len(history) <= max_messages:
        return history
    trimmed = history[-max_messages:]
    for i, msg in enumerate(trimmed):
        if _is_user_message(msg):
            return trimmed[i:]
    return trimmed
```

### 中期（深挖官方 Compaction）

查清楚 Compaction 和 Overflowing Tool Output 的 API，用官方方案替代手写。

### 长期（Provider-Reducer 管道）

设计文档 `docs/intent-recognition-enhancement-design.md` Phase 2 的方案：
- Provider：收集各维度上下文（终端状态/里程碑/工具结果摘要）
- Reducer：token 预算内裁剪（PriorityReducer/SlidingWindowReducer/HybridReducer）
- MilestoneTracker：独立于消息裁剪的关键事件记忆

## 六、相关 API 速查

```python
# 传入历史
async with agent.iter(msg, deps=deps, message_history=history) as run:

# 取出消息
run.all_messages()          # 累积（历史+本轮）
run.new_messages()          # 仅本轮
run.all_messages_json()     # JSON 序列化（存库）
run.conversation_id         # 对话 ID

# 反序列化
from pydantic_ai.messages import ModelMessagesTypeAdapter
history = ModelMessagesTypeAdapter.validate_json(json_bytes)
```
