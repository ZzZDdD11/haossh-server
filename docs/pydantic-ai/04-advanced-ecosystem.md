# 04 · 高级特性与生态

> 本项目多数未用，按需深入

## 一、结构化输出（Output）

### 作用

用 Pydantic Model 约束 Agent 返回的结构化数据，自动生成 JSON Schema 指导 LLM 输出并验证。

```python
class DiskInfo(BaseModel):
    total_gb: int = Field(description='总磁盘大小')
    used_gb: int = Field(description='已用大小')
    usage_percent: int = Field(description='使用率', ge=0, le=100)

agent = Agent('deepseek:...', output_type=DiskInfo)
result = await agent.run('查看磁盘情况')
print(result.output.total_gb)   # 直接拿到结构化对象
```

### 本项目现状

`output_type=str`（纯文本），Agent 返回自然语言。如果要做"磁盘使用率监控告警"这种需要结构化数据的场景，可改用结构化输出。

### 何时用

| 场景 | output_type |
|------|-------------|
| 聊天/运维建议 | `str`（本项目） |
| 提取结构化数据 | Pydantic Model |
| 多种可能输出 | 联合类型（Union） |

## 二、动态指令（@agent.instructions）

### 作用

指令可以是函数，用 deps 动态生成 system prompt。

```python
@agent.instructions
async def dynamic_instructions(ctx: RunContext[AgentDeps]) -> str:
    # 根据运行时上下文动态生成指令
    return f"当前操作的服务器: {ctx.deps.session_id}"
```

### 本项目现状

静态 system_prompt（读 `prompts/ssh_operator.md`）。

### 何时用

- 注入运行时环境信息（当前服务器 OS、用户、目录）
- 按权限/角色调整指令（admin vs guest）
- 按对话阶段调整指令（探测阶段 vs 执行阶段）

设计文档 Phase 1 的"动态 Prompt 构建"就是这个。

## 三、Capabilities 系统

### 作用

可组合的能力单元，把工具、钩子、指令、模型设置打包成可复用单元。

```python
from pydantic_ai.capabilities import Thinking, WebSearch

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[Thinking(), WebSearch(local='duckduckgo')],
)
```

### 与 Tool 的区别

| | Tool | Capability |
|---|---|---|
| 粒度 | 单个函数 | 一组工具+钩子+指令的打包 |
| 复用 | 手动组合 | 即插即用 |
| 适合 | 单一功能 | 复杂能力模块 |

### 本项目现状

用 Tool 注册方式。如果工具增多、需要分组管理，可考虑迁移到 Capabilities。

## 四、Pydantic AI Harness（官方能力库）

### 已有能力

| 能力 | 作用 | 本项目对应 |
|------|------|-----------|
| **Shell** | Shell 命令执行 | 我们的 execute_command |
| **FileSystem** | 文件系统访问 | 我们的 read_file/write_file/list_directory |
| **Memory** | 记忆系统 | 我们的 message_history |
| Code Mode | 代码执行 | — |
| Subagents | 子 Agent 编排 | — |
| Dynamic Workflow | 动态工作流 | — |
| Planning | 规划能力 | — |
| Guardrails | 护栏（安全控制） | 我们的危险命令拦截 |
| Runtime Authoring | 运行时编写 | — |
| Managed Prompt | 托管提示 | — |
| ACP | Agent 通信协议 | — |

### 启发

我们的 execute_command/read_file/write_file 等**和 Harness 的 Shell/FileSystem 高度重合**。可能重复造轮子。值得评估：
- Harness 的 Shell 是否含溢出处理/错误重试/安全控制
- 是否迁移到 Harness 减少自维护代码

## 五、模型与提供商

### 支持的提供商

| 提供商 | 模型 |
|--------|------|
| OpenAI | GPT 系列 |
| Anthropic | Claude |
| Google | Gemini |
| DeepSeek | DeepSeek（本项目用） |
| Bedrock | Amazon 托管 |
| Groq | 高速推理 |
| Ollama | 本地模型 |
| Cohere / Mistral / xAI / Hugging Face / OpenRouter ... | |

### 本项目配置

```python
agent = Agent(
    model=OpenAIChatModel(
        model_name=settings.agent_model_name,   # deepseek-v4-flash
        provider="deepseek",
    ),
)
```

DeepSeek 走 OpenAI 兼容接口。换模型只需改 `OpenAIChatModel` 参数。

### 自定义模型

未列出的提供商，可实现自定义 Model 接口。

### 测试模型

```python
agent = Agent('test')  # 无需 API Key，离线测试
```

## 六、工具审批（人在循环）

### 作用

标记特定工具调用需执行前审批。

```python
Tool(
    delete_database,
    requires_approval=True,   # LLM 调用时需人工确认
)
```

### 审批维度

- 基于工具参数（如 `rm -rf` 才需审批）
- 基于对话历史
- 基于用户偏好

### 本项目现状

讨论过未实现。当前用"危险命令正则拦截"做硬防护。如果要做"危险但非毁灭"命令的审批（如 `systemctl stop nginx`），可用此特性。

## 七、持久化执行（Durable Execution）

### 作用

跨 API 故障和应用重启保持进度。

### 集成

支持 Temporal、DBOS、Prefect、Restate、Kitaru、Apache Airflow 等。

### 本项目现状

未用。Phase 5 会话持久化可参考。

## 八、Pydantic Evals（评估系统）

### 作用

系统化测试和评估 Agent 性能。

### 能力

- 内置评估器
- LLM Judge（用 LLM 评判 LLM）
- 第三方集成
- 自定义评估器
- 在线评估
- Logfire 集成

### 本项目现状

未用。如果要评估"Agent 调工具的准确率""命令执行成功率"，可用 Evals。

## 九、Logfire（可观测性）

### 作用

与 Pydantic Logfire 紧密集成，OTel 标准，自动仪器化。

```python
import logfire
logfire.configure()
logfire.instrument_pydantic_ai()  # 自动仪器化所有 Agent
```

### 本项目现状

未用。生产部署时用于监控 Agent 运行、追踪工具调用、排查问题。

## 十、MCP 集成

### 作用

接入 MCP（Model Context Protocol）服务器，复用外部工具。

### 本项目现状

未用。设计文档 Phase 4+ 提到。

## 十一、图系统（Pydantic Graph）

### 作用

用类型提示定义图，处理复杂控制流。

### 能力

- Graph Builder
- 步骤、连接、决策、并行执行

### 本项目现状

未用。复杂多 Agent 编排时可能用。

## 十二、UI 事件流

### 作用

支持 AG-UI、Vercel AI 等 UI 事件流标准。

### 本项目现状

我们自定义了 SSE 事件流协议（chat.py），未用官方 UI 标准。如果要对接标准前端框架，可考虑。

## 十三、生态集成总览

| 类别 | 集成 |
|------|------|
| 调试监控 | Pydantic Logfire |
| 持久化执行 | Temporal/DBOS/Prefect/Restate/Kitaru/Airflow |
| UI 事件流 | AG-UI/Vercel AI |
| MCP | Client + Server |
| 评估 | Pydantic Evals |
| 图 | Pydantic Graph |

## 十四、推荐深入顺序

按对本项目的价值排序：

1. **Compaction + Overflowing Tool Output**（03 文档）—— 当前阶段上下文管理
2. **Harness 的 Shell/FileSystem** —— 评估是否替代自写工具
3. **动态指令** —— 设计文档 Phase 1
4. **结构化输出** —— 如需结构化数据返回
5. **工具审批** —— 危险命令控制
6. **Logfire** —— 生产可观测性
7. **Evals** —— Agent 质量评估
8. **持久化执行** —— Phase 5
