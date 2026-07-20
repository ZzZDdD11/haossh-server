# 02 · 工具系统与依赖注入

> 本项目核心已用部分，对照 `src/haossh/agent/` 理解

## 一、依赖注入（Dependencies）

### 为什么需要依赖注入

工具函数需要"运行时上下文"（如本项目的 session_id），但 LLM 不该知道、也不该传这些参数。依赖注入解决这个矛盾。

### 三步使用

```python
# ① 定义依赖类型
@dataclass
class AgentDeps:
    session_id: str
    allow_sudo: bool = True

# ② 声明 Agent 的 deps_type
agent = Agent(model=..., deps_type=AgentDeps)

# ③ 调用时传入 deps
async with agent.iter(msg, deps=AgentDeps(session_id="xxx")) as run:
    ...
```

### 工具函数怎么拿依赖

通过 `RunContext[Deps]` 第一个参数：

```python
async def execute_command(ctx: RunContext[AgentDeps], command: str) -> str:
    cid = ctx.deps.session_id   # ← 从 ctx.deps 取
    ...
```

`RunContext` 是框架自动注入的，**不暴露给 LLM**（LLM 只看到 `command` 参数）。

### 本项目实践

- 定义：`src/haossh/agent/deps.py` → `AgentDeps`
- 注入：`src/haossh/api/routes/chat.py` → `agent.iter(msg, deps=AgentDeps(...))`
- 取用：`src/haossh/agent/tools.py` → `ctx.deps.session_id`

### deps 的本质

**请求级局部变量**，不是全局状态。每次 `agent.iter` 调用独立，多用户并发互不干扰。LLM 无法影响 deps（防 prompt injection 越权）。

## 二、函数工具（Function Tools）

### 三种注册方式

```python
# 方式 A：装饰器（带 ctx）
@agent.tool
async def execute_command(ctx: RunContext[AgentDeps], command: str) -> str:
    ...

# 方式 B：装饰器（不带 ctx，纯函数）
@agent.tool_plain
def get_time() -> str:
    ...

# 方式 C：构造时传入（本项目用，避免循环依赖）
from pydantic_ai import Tool
agent = Agent(model=..., tools=[Tool(execute_command, prepare=...)])
```

### 本项目选方式 C 的原因

```
agent.py ──→ tools.py ──→ deps.py
              (不反向依赖 agent.py，无循环)
```

装饰器方式需要 `tools.py` import `agent` 实例，会和 `agent.py` import `tools` 形成循环。

### Tool 类关键参数

```python
Tool(
    function,
    name="execute_command",      # 工具名
    description="执行远程命令",   # 描述（默认用 docstring）
    max_retries=1,               # 失败重试
    timeout=60.0,                # 执行超时
    prepare=require_connection,  # 动态控制可见性
    requires_approval=False,     # 是否需人工审批
    sequential=False,            # 是否串行
)
```

### 工具函数签名规则

```python
async def tool_name(
    ctx: RunContext[Deps],   # 第一个参数，框架自动注入，不暴露给 LLM
    command: str,            # 业务参数，暴露给 LLM（生成 JSON Schema）
    timeout: int = 30,
) -> str:                    # 返回值给 LLM
    """docstring 作为工具描述传给 LLM"""
```

- 第一个参数是 `RunContext` → 框架识别为"带 ctx"工具，不放进 LLM schema
- 其余参数 → 生成 JSON Schema 发给 LLM
- docstring → 作为工具描述
- 参数描述从 docstring 的 Args 段提取

### 返回值规则

| 返回类型 | 行为 |
|---------|------|
| `str` | LLM 直接看到文本（最常用） |
| `dict` / BaseModel | 序列化后喂给 LLM |
| 抛 `ModelRetry` | 告诉 LLM 失败，可重试（配合 max_retries） |
| `None` | LLM 收到空字符串 |

### 本项目实践

`src/haossh/agent/tools.py` 5 个工具：
- `execute_command` → terminal.exec_command
- `read_file` → sftp.read_chunk + get_size
- `write_file` → sftp.save_content
- `list_directory` → sftp.list_dir
- `get_environment` → terminal.exec_command ×5（并行）

## 三、prepare 钩子（动态工具可见性）

### 解决什么问题

工具列表发给 LLM 前，过滤/修改工具。比"工具函数内检查"更省 token、更精准。

### 签名

```python
async def prepare_func(
    ctx: RunContext[Deps],
    tool_def: ToolDefinition,
) -> ToolDefinition | None:
    ...
```

| 返回值 | 含义 |
|--------|------|
| `tool_def` | 工具可用 |
| 修改后的 `tool_def` | 可用，用新定义 |
| `None` | 工具隐藏，LLM 看不到 |

### 调用时机

每次模型请求前（多轮工具调用中每轮都跑）。

### 本项目实践

`require_connection` 钩子：SSH 未连接时隐藏所有工具。

```python
async def require_connection(ctx, tool_def):
    from haossh.ssh.session import ssh_sessions
    conn = ssh_sessions.get(ctx.deps.session_id)
    if conn is not None and not conn.is_closed():
        return tool_def   # 连上了 → 可见
    return None           # 没连 → 隐藏
```

### 事前过滤 vs 事后拦截

| | prepare 钩子 | 工具函数内检查 |
|---|---|---|
| 时机 | LLM 看到工具前 | LLM 已调用后 |
| 效果 | LLM 根本看不到 | LLM 调了才发现不行 |
| 浪费 | 0 token | 浪费一轮交互 |

## 四、工具系统组件

| 组件 | 作用 | 本项目 |
|------|------|--------|
| Function Tools | 基础函数工具 | ✅ |
| Toolsets | 工具集管理（组合/过滤/重命名） | ❌ 工具少不需要 |
| Deferred Tools | 延迟加载工具 | ❌ |
| Native Tools | 模型原生工具（搜索/代码执行） | ❌ |
| Common Tools | 通用工具集（DuckDuckGo 等） | ❌ |
| 人在循环审批 | 工具执行前需审批 | ❌ 讨论过 |

## 五、RunContext 的属性

工具函数第一个参数 `ctx` 的常用属性：

| 属性 | 用途 |
|------|------|
| `ctx.deps` | ⭐ 依赖对象（拿 session_id 等） |
| `ctx.tool_name` | 当前工具名（审计） |
| `ctx.retry` | 当前第几次重试（0=首次） |
| `ctx.last_attempt` | 是否最后一次重试机会 |
| `ctx.agent` | 当前 Agent 实例 |
| `ctx.run_id` | 本次 run 的 ID |
| `ctx.conversation_id` | 对话 ID（跨多轮） |
| `ctx.usage_limits` | token/请求次数限制 |
