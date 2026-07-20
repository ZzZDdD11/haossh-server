# 生产环境踩坑实录

> 来源：本项目实战排查
> 版本：pydantic-ai 2.13.0

## 一、长命令超时：多层超时体系

### 问题现象

部署场景执行 `uv sync`、`apt install` 等长命令时，工具反复超时失败：

```
WARNING 命令执行失败 command=uv sync --frozen error=
ModelRetry: Timed out after 60.0 seconds.
UnexpectedModelBehavior: Tool 'execute_command' exceeded max retries count of 2
```

### 根因：三层超时叠加

| 层级 | 参数 | 位置 | 原值 | 问题 |
|------|------|------|------|------|
| SSH 命令级 | `timeout` | `execute_command` 函数参数 | 30s | 命令本身超时 |
| deps 上限 | `max_command_timeout` | `AgentDeps` | 60s | `min(timeout, max_command_timeout)` 截断 |
| Tool 框架级 | `timeout` | `Tool(timeout=...)` | 60s | pydantic-ai 框架超时 |

LLM 传 `timeout=120`，但 `min(120, 60)=60`，被 deps 上限截断到 60 秒。

### 修复：调整三层超时

```python
# deps.py: max_command_timeout 60 → 300
max_command_timeout: int = 300

# tools.py: execute_command 默认 timeout 30 → 120
async def execute_command(ctx, command: str, timeout: int = 120):

# tools.py: Tool 级别 timeout 60 → 300, max_retries 1 → 2
Tool(execute_command, max_retries=2, timeout=300.0)
```

### 隐藏坑：asyncio.TimeoutError 错误消息为空

`asyncio.TimeoutError` 的 `str()` 是空字符串，导致日志里 `error=` 后面空白：

```
WARNING 命令执行失败 command=uv sync error=    ← 空白！
```

**修复**：单独捕获 `asyncio.TimeoutError`，返回明确提示：

```python
except asyncio.TimeoutError:
    raise ModelRetry(
        f"命令执行超时（{timeout}秒）。长时间命令请改用 run_background 后台执行。"
    ) from None
except Exception as e:
    raise ModelRetry(f"命令执行失败: {e}") from e
```

---

## 二、后台执行 + 轮询：彻底解决长命令

### 问题

即使提高超时到 300 秒，仍有命令（`docker build`、大仓库 `git clone`）会超。而且同步阻塞期间 LLM 无法看到进度。

### 方案：两个新工具

| 工具 | 作用 | 超时 |
|------|------|------|
| `run_background(command)` | `nohup` 后台执行，返回 task_id | 10s（只是启动） |
| `check_task(task_id)` | `ps -p` 检查状态 + `tail` 读日志 | 10s |

### 实现原理

```bash
# run_background 执行的命令：
nohup bash -c 'uv sync' > /tmp/haossh_bg_xxx.log 2>&1 & echo $!
#                                                        ↑ 返回 PID

# check_task 执行的命令：
ps -p $PID && echo '__RUNNING__' || echo '__DONE__'; tail -30 /tmp/haossh_bg_xxx.log
```

命令在服务器后台运行（nohup），不受 SSH 超时或断开影响。

### LLM 自动选择工具：两层保障

**第一层：docstring 划清边界**

```python
# execute_command docstring:
"长时间命令（uv sync、apt install...）请改用 run_background 后台执行。"

# run_background docstring:
"适用于可能超过 30 秒的命令：uv sync、apt install、docker build、git clone 等。"
```

**第二层：超时自我纠正**

`execute_command` 超时时返回明确提示，LLM 看到后自动切换到 `run_background`：

```python
raise ModelRetry(
    f"命令执行超时（{timeout}秒）。长时间命令请改用 run_background 后台执行，再用 check_task 轮询。"
)
```

参考：Ansible 的 `async` + `poll` 模式。

---

## 三、trim_history 裁剪策略：46 条裁到 5 条

### 问题现象

agent 在同一对话里"失忆"——用户说"继续"，agent 回复"我们的对话好像还没有开始过"。

日志排查：

```
续聊 conversation_id=5ecdbb8949c0 历史消息数=43    ← DB 有 43 条
trim_history 输入: 46 条
trim_history 裁剪: 46 -> 5 条                       ← 裁到只剩 5 条！
```

### 根因：对齐 user-prompt 太激进

原始裁剪逻辑：

```python
MAX_HISTORY_MESSAGES = 40

trimmed = history[-40:]  # 取最后 40 条
for i, msg in enumerate(trimmed):
    if isinstance(msg, ModelRequest):
        for part in msg.parts:
            if getattr(part, "part_kind", None) == "user-prompt":
                return trimmed[i:]  # 从第一个 user-prompt 开始
```

问题：工具调用多的场景，最后 40 条里大部分是 `ModelResponse(tool-call)` + `ModelRequest(tool-return)` 配对。第一个 `user-prompt` 可能在第 35 个位置，裁完只剩 5 条。

```
消息 [0-34]:  tool-call → tool-return → tool-call → tool-return → ...（35条工具配对）
消息 [35]:    ModelRequest(user-prompt="继续")  ← 第一个 user-prompt
消息 [36-39]: 后续消息

对齐后: trimmed[35:] = 只有 5 条
```

### 修复：两个调整

| 参数 | 修复前 | 修复后 | 原因 |
|------|--------|--------|------|
| `MAX_HISTORY_MESSAGES` | 40 | **100** | 工具调用多，每轮用户对话可能 10+ 条 |
| 对齐目标 | `user-prompt` | **`ModelResponse`** | 保证工具配对完整，不丢太多消息 |

```python
MAX_HISTORY_MESSAGES = 100

trimmed = history[-100:]
for i, msg in enumerate(trimmed):
    if isinstance(msg, ModelResponse):
        return trimmed[i:]  # 从第一个 ModelResponse 开始
```

为什么对齐 `ModelResponse` 更好：
- 工具配对是 `[ModelResponse(tool-call), ModelRequest(tool-return)]`
- 从 `ModelResponse` 开始保证配对完整，不会出现孤立的 `tool-return`
- 不需要找 `user-prompt`，避免工具调用多时裁掉太多

---

## 四、request_limit 默认 50 太低

### 问题

部署等复杂任务需要多轮工具调用，默认 `request_limit=50` 很快耗尽：

```
The next request would exceed the request_limit of 50.
```

### 修复

在 `agent.iter()` 时传 `UsageLimits`：

```python
from pydantic_ai.usage import UsageLimits

async with agent.iter(
    req.message,
    deps=deps,
    message_history=history,
    usage_limits=UsageLimits(request_limit=200),
) as run:
```

| 场景 | 建议 request_limit |
|------|-------------------|
| 日常问答 | 50（默认） |
| 运维操作 | 100 |
| 部署等复杂任务 | 200 |

---

## 五、SSH 编码错误

### 问题

服务器命令输出包含非 UTF-8 字符（如 GBK 编码的中文），asyncssh 解码失败：

```
'utf-8' codec can't decode byte 0xe9 in position 0: invalid continuation byte
```

### 影响

- SSH 连接可能断开
- 命令执行失败但错误信息不明确

### 临时处理

在命令中强制 UTF-8 输出：

```bash
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 <command>
```

---

## 六、排查方法论

### 日志先行

```python
# 1. 确认数据加载正确
logger.info("续聊 conversation_id=%s 历史消息数=%d", conv_id, len(history))

# 2. 确认裁剪行为
logger.info("trim_history 输入: %d 条", len(history))
logger.info("trim_history 裁剪: %d -> %d 条", len(history), len(result))
```

### DB 验证

直接从 DB 反序列化消息，确认内容正确：

```python
msgs = await repo_conversation.get_messages(conv_id)
for i, m in enumerate(msgs):
    print(f'[{i}] {type(m).__name__}: {m.parts[0].content[:60]}')
```

### 配置 logging

Python logging 默认 WARNING，INFO 不输出。必须在应用入口配置：

```python
# main.py 最顶部
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
```

---

## 七、经验总结

| 踩坑 | 根因 | 修复 | 教训 |
|------|------|------|------|
| 长命令超时 | 三层超时叠加，deps 上限 60s 截断 | 全部提到 300s | 梳理所有超时层级 |
| error 为空 | `asyncio.TimeoutError` 无消息 | 单独捕获加明确提示 | 异常处理要分类 |
| 长命令反复失败 | 同步阻塞 + 超时杀命令 | 后台执行 + 轮询 | 参考 Ansible async/poll |
| agent 失忆 | trim_history 对齐 user-prompt 裁到 5 条 | 改对齐 ModelResponse + 提到 100 | 工具调用多的场景消息数膨胀快 |
| request_limit 超限 | 默认 50 太低 | 提到 200 | 复杂任务需要更多轮次 |
| LLM 选错工具 | docstring 边界模糊 | 划清边界 + 超时纠正 | 两层保障：预防 + 纠正 |
