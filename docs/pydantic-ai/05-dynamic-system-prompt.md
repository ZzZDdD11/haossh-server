# 动态 System Prompt

> 来源：官方文档 + 本项目踩坑实践
> 版本：pydantic-ai 2.13.0

## 一、什么是动态 System Prompt

System Prompt 是发给 LLM 的"行为指令"（你是谁、怎么做事）。pydantic-ai 支持两种方式：

| 方式 | 何时确定 | 能否访问 deps | 适用场景 |
|------|---------|-------------|---------|
| **静态** `Agent(system_prompt="...")` | 创建 Agent 时 | 不能 | 身份、能力范围、安全红线等不变内容 |
| **动态** `@agent.system_prompt` | 每次 LLM 请求时 | 能（通过 `ctx.deps`） | 权限、连接状态等运行时变化内容 |

### 注册机制

```python
from pydantic_ai import Agent, RunContext

agent = Agent('model', deps_type=MyDeps)

@agent.system_prompt
def dynamic_prompt(ctx: RunContext[MyDeps]) -> str:
    # 每次构造 LLM 请求时调用
    if ctx.deps.allow_sudo:
        return "你可以使用 sudo"
    return "禁止使用 sudo"
```

装饰器只是把函数引用存进 agent 内部列表，**不执行函数体**。真正的调用发生在 `agent.run()` / `agent.iter()` 时。

### 拼接机制

静态 + 多个动态函数的返回值**自动拼接**成完整 system prompt：

```
ssh_operator.md 静态内容      ← Agent(system_prompt=...)
+ 权限指令                      ← @agent.system_prompt（读 deps.allow_sudo）
+ 连接状态指令                   ← @agent.system_prompt（查 SSH 状态）
= 最终发给 LLM 的 system prompt
```

---

## 二、`dynamic` 参数：最容易踩的坑

### 问题描述

`@agent.system_prompt` 装饰器有一个关键字参数 `dynamic`，**默认 `False`**：

```python
def system_prompt(
    self,
    func: SystemPromptFunc[AgentDepsT] | None = None,
    /,
    *,
    dynamic: bool = False,  # ← 默认 False！
)
```

两种模式的区别：

| `dynamic` | 行为 | 适用场景 |
|-----------|------|---------|
| `False`（默认） | 函数**只调用一次**，结果存入消息历史，后续轮次复用不重新调用 | 不随运行时变化的 prompt |
| `True` | **每次 LLM 请求都重新调用**，即使有 `message_history` 也重新评估 | 依赖运行时状态的 prompt（连接状态、权限等） |

### 源码验证

`_system_prompt.py` 的 `resolve_system_prompts` 函数：

```python
async def resolve_system_prompts(static_prompts, runners, run_context):
    parts = [SystemPromptPart(p) for p in static_prompts]
    for runner in runners:
        prompt = await runner.run(run_context)
        if runner.dynamic:
            # 动态：带 dynamic_ref，后续轮次会重新评估
            parts.append(SystemPromptPart(prompt or '', dynamic_ref=runner.function.__qualname__))
        elif prompt:
            # 静态：不带 dynamic_ref，只调用一次
            parts.append(SystemPromptPart(prompt))
    return parts
```

`_agent_graph.py` 的 `_reevaluate_dynamic_system_prompts` 方法只重新评估带 `dynamic_ref` 的部分：

```python
async def _reevaluate_dynamic_system_prompts(self, messages, run_context):
    if self.system_prompt_dynamic_functions:
        for msg in messages:
            if isinstance(msg, ModelRequest):
                for part in msg.parts:
                    if isinstance(part, SystemPromptPart) and part.dynamic_ref:
                        # 只有 dynamic_ref 的 part 才重新调用
                        runner = self.system_prompt_dynamic_functions.get(part.dynamic_ref)
                        updated = await runner.run(run_context)
                        part = SystemPromptPart(updated or '', dynamic_ref=part.dynamic_ref)
```

### 踩坑现象

本项目最初用 `@agent.system_prompt`（不带参数，默认 `dynamic=False`）注册了连接状态检测函数：

```python
@agent.system_prompt
async def connection_status(ctx: RunContext[AgentDeps]) -> str:
    connected = await session.is_connected(ctx.deps.session_id)
    if connected:
        return "SSH 已连接，可以执行命令"
    return "未连接 SSH，只能提供建议"
```

**现象**：
- 第一次对话（未连接）：函数被调用，返回"未连接" ✓
- 连接 SSH 后发消息：函数**没有被重新调用**，LLM 仍看到"未连接"的 prompt
- 但 agent 实际执行了命令（因为静态 prompt 里说"你有 execute_command 工具"）

**根因**：`dynamic=False` 时，函数结果存入第一轮的消息历史。后续轮次有 `message_history` 时，直接复用历史里的 system prompt，不重新调用函数。连接状态变化了，但 LLM 拿到的还是旧的 system prompt。

### 修复

加 `dynamic=True`：

```python
@agent.system_prompt(dynamic=True)
async def connection_status(ctx: RunContext[AgentDeps]) -> str:
    ...
```

修复后，每次 LLM 请求都会重新调用 `connection_status`，实时查 SSH 状态。

---

## 三、本项目实践

### 动态 Prompt 与上下文管理的区分

| | 动态 Prompt | 上下文管理 |
|---|---|---|
| 改的是什么 | **System Prompt**（指令层） | **message_history / 消息内容**（信息层） |
| 回答的问题 | "LLM **该怎么做事**" | "LLM **需要知道什么**" |
| 例子 | 权限限制、连接状态 | 环境信息、里程碑事件、意图检索结果 |

环境信息（OS/用户/主机）、里程碑事件、意图检索结果——这些是**信息**，属于上下文管理（注入到消息前缀或 Provider-Reducer 管道），不是动态 prompt 的范畴。

### 本项目的两个动态 Prompt

**1. 权限动态**（读 `ctx.deps.allow_sudo`）

```python
@agent.system_prompt(dynamic=True)
def sudo_instruction(ctx: RunContext[AgentDeps]) -> str:
    if ctx.deps.allow_sudo:
        return ""  # 静态 prompt 已有 sudo 指令
    return (
        "## ⚠️ 权限限制（覆盖上方 sudo 规则）\n"
        "当前会话禁止使用 sudo。上方「主动使用 sudo」的规则作废。\n"
        "权限不足时，报告用户需要什么权限，不要自行提权。"
    )
```

**2. 连接状态动态**（查 SSH 实时状态）

```python
@agent.system_prompt(dynamic=True)
async def connection_status(ctx: RunContext[AgentDeps]) -> str:
    from haossh.ssh import session
    connected = bool(ctx.deps.session_id) and await session.is_connected(ctx.deps.session_id)
    if connected:
        return "## 当前状态\nSSH 已连接，你可以直接调用工具执行命令。"
    return "## 当前状态\n未连接 SSH，你是运维顾问，只能提供建议，不能执行命令。"
```

### 运行机制

一次 `agent.iter()` 可能有多轮 LLM 请求（工具调用循环），每轮都重新生成 system prompt：

```
用户："查磁盘"
  │
  ├─ 第1轮 LLM 请求
  │    connection_status(ctx) → "SSH 已连接"     ← 查了一次 is_connected
  │    system prompt = 静态 + "SSH 已连接"
  │    LLM 返回: tool_call(execute_command, "df -h")
  │    执行工具 → 返回结果
  │
  ├─ 第2轮 LLM 请求（带上了工具结果）
  │    connection_status(ctx) → "SSH 已连接"     ← 又查了一次 is_connected
  │    system prompt = 静态 + "SSH 已连接"
  │    LLM 返回: "磁盘使用 50%..."
  │
  └─ 结束
```

如果第1轮和第2轮之间 SSH 断了，第2轮的 `connection_status` 会返回"未连接"，LLM 拿到的 system prompt 就变了，行为会随之调整。这是 `dynamic=True` 的核心价值。

---

## 四、排查经验

### 现象：动态 prompt 函数只在第一次调用

用日志验证函数是否被调用：

```python
@agent.system_prompt(dynamic=True)
async def connection_status(ctx: RunContext[AgentDeps]) -> str:
    connected = bool(ctx.deps.session_id) and await session.is_connected(ctx.deps.session_id)
    logger.info("[动态prompt] connection_status 被调用 session_id=%r connected=%s",
                ctx.deps.session_id, connected)
    ...
```

如果日志只在第一次对话出现，后续对话没有 → 检查是否漏了 `dynamic=True`。

### 日志配置

Python logging 默认级别是 WARNING，INFO 不会输出。需要在应用入口配置：

```python
# main.py 最顶部（在其他 import 之前）
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
```

### 日志输出到文件

uvicorn 启动时重定向：

```bash
uv run uvicorn haossh.main:app --port 8091 --reload > log/uvicorn.log 2>&1
```

`2>&1` 把 stderr（Python logging 默认输出）也重定向到文件。

---

## 五、小结

| 要点 | 说明 |
|------|------|
| `@agent.system_prompt` | 默认 `dynamic=False`，只调用一次 |
| `@agent.system_prompt(dynamic=True)` | 每次请求都重新调用，适合依赖运行时状态的 prompt |
| 什么时候用 dynamic | prompt 内容依赖 `ctx.deps` 的运行时值（权限、连接状态等） |
| 什么时候不用 dynamic | prompt 内容不随运行时变化（固定的补充指令） |
| 排查方法 | 加 `logger.info` 日志，确认函数是否被调用 |
