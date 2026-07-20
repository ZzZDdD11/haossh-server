# 01 · 框架总览

## 定位

Pydantic AI 是 **Python GenAI Agent 框架**，帮助快速构建生产级生成式 AI 应用。

设计灵感来自 **FastAPI**——把"FastAPI 的开发体验"带到 AI Agent 开发。由 Pydantic 团队官方打造。

> Pydantic 验证是 OpenAI SDK、Google ADK、Anthropic SDK、LangChain 等的底层验证层。
> 框架强调"为什么用衍生品，不直接用源头？"

## 两大组成

| 组成 | 作用 |
|------|------|
| **Pydantic AI Core** | Agent 循环 + 可组合能力系统（核心框架） |
| **Pydantic AI Harness** | 官方现成能力库（代码执行/文件/Shell/子Agent/记忆/护栏） |

## 分层架构

```
┌─────────────────────────────────────┐
│         Pydantic AI Harness          │  官方能力库
│  (代码执行/文件系统/Shell/子Agent/     │
│   规划/记忆/护栏)                     │
├─────────────────────────────────────┤
│           Pydantic AI Core           │  核心框架
│  (Agent循环/Capabilities/Tools/      │
│   依赖注入/输出验证/流式输出)          │
├─────────────────────────────────────┤
│        Models & Providers            │  模型层
│  (OpenAI/Anthropic/Google/DeepSeek)  │
├─────────────────────────────────────┤
│      Pydantic Validation             │  基础验证层
└─────────────────────────────────────┘
```

## 核心概念

### 1. Agent（智能体）

中心抽象，配置模型、指令、依赖类型、输出类型：

```python
from pydantic_ai import Agent

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    instructions='Be concise, reply with one sentence.',
)
```

Agent 是泛型的：`Agent[DepsType, OutputType]`，实现依赖和输出的类型安全。

**本项目**：`src/haossh/agent/agent.py`，用 DeepSeek + tools + deps_type=AgentDeps

### 2. Dependencies（依赖注入）

通过 `deps_type` 注入数据、连接和逻辑，使 Agent 行为可定制，特别适用于单元测试：

```python
@dataclass
class SupportDependencies:
    customer_id: int
    db: DatabaseConn

agent = Agent('openai:gpt-5.2', deps_type=SupportDependencies)
```

依赖通过 `RunContext` 在指令和工具函数中传递，类型错误可被静态检查捕获。

**本项目**：`src/haossh/agent/deps.py` 的 `AgentDeps`（session_id/terminal_session_id/allow_sudo/max_command_timeout）

### 3. Output（结构化输出）

用 Pydantic Model 约束 Agent 返回的结构化数据，自动生成 JSON Schema 指导 LLM 输出：

```python
class SupportOutput(BaseModel):
    support_advice: str = Field(description='Advice returned to the customer')
    block_card: bool = Field(description="Whether to block the customer's card")
    risk: int = Field(description='Risk level of query', ge=0, le=10)
```

**本项目**：当前 `output_type=str`（纯文本），未用结构化输出

### 4. Capabilities（能力系统）

可组合的能力单元，把工具、钩子、指令、模型设置打包成可复用单元：

```python
from pydantic_ai.capabilities import Thinking, WebSearch

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[Thinking(), WebSearch(local='duckduckgo')],
)
```

**本项目**：未用，当前用 Tool 注册方式

### 5. Hooks（钩子）

在 Agent 执行流程特定节点插入自定义逻辑。

**本项目**：用了 prepare 钩子（require_connection），控制工具可见性

### 6. Agent Specs

支持 YAML/JSON 定义 Agent，无需编写代码。

## 关键设计原则

| 原则 | 说明 |
|------|------|
| 类型安全优先 | IDE 和 AI 编码助手获充分上下文，错误从运行时提前到编写时 |
| 可组合性 | 通过 Capabilities 系统实现模块化组合 |
| 模型无关 | 统一模型接口，支持切换 LLM 提供商 |
| 可观测性内建 | 与 Pydantic Logfire 深度集成 |

## 关键特性一览

| 特性 | 说明 |
|------|------|
| 由 Pydantic 团队构建 | 验证层的源头 |
| 模型无关 | 支持几乎所有主流模型 |
| 无缝可观测性 | Logfire 集成，OTel 标准 |
| 完全类型安全 | 泛型设计 |
| 强大评估系统 | Pydantic Evals 系统化测试 |
| 可扩展设计 | 内置/Harness/第三方/YAML 定义 |
| MCP 和 UI 集成 | Model Context Protocol + UI 事件流 |
| 人在循环审批 | 工具调用可标记需审批 |
| 持久化执行 | 跨 API 故障/重启保持进度 |
| 流式输出 | 持续流式结构化输出 |
| 图支持 | 类型提示定义图，处理复杂控制流 |

## 与其他框架的区别

| 维度 | Pydantic AI | 其他框架 |
|------|------------|---------|
| 基础验证 | Pydantic 原生（源头） | 使用 Pydantic（衍生） |
| 类型安全 | 完全类型安全，泛型设计 | 程度不一 |
| 设计哲学 | FastAPI 式人体工学 | 各有不同 |
| 可观测性 | 内建 Logfire | 通常需额外配置 |
| 能力系统 | 可组合 Capabilities | 工具/插件机制 |
| 模型支持 | 广泛，开箱即用 | 程度不一 |
| 评估系统 | 内建 Pydantic Evals | 通常需第三方 |
| 持久化执行 | 内建支持 | 通常需额外集成 |

## Hello World

```python
from pydantic_ai import Agent

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    instructions='Be concise, reply with one sentence.',
)
result = agent.run_sync('Where does "hello world" come from?')
print(result.output)
```

## 测试模型

内置 `test` 模型，无需 API Key 离线运行：
```python
agent = Agent('test')  # 返回预设响应，用于测试
```
