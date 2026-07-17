# haossh-server: Java → Python 迁移方案

> 设计文档 | 2026-07-17

## 一、背景

将 haossh-server（AI 驱动的 SSH 运维平台）从 Java/Spring Boot/Google ADK 迁移到 Python/FastAPI/LangGraph。用户希望借此过程积累 Python AI Agent 开发实力。

### 现有规模

- 186 个 Java 文件，7 个 Maven 模块
- DDD 六边形架构，策略链 ReAct Agent 编排
- Google ADK + Spring AI 做 LLM 调用
- JSch 做 SSH 连接管理
- MyBatis + MySQL 持久化

---

## 二、目标技术栈

| 层 | Java → Python |
|---|---|
| Web 框架 | Spring Boot → **FastAPI** |
| Agent 编排 | Google ADK + xfg-wrench → **LangGraph** |
| LLM 调用 | Spring AI → **LangChain** (初期) / **Pydantic AI** (后期) |
| SSH | JSch → **asyncssh** |
| 数据库 | MyBatis → **SQLAlchemy 2.0 + asyncmy** |
| 数据校验 | Lombok + Validation → **Pydantic v2** |
| MCP | 自建 MCP Factory → **mcp 官方 Python SDK** |
| 配置 | YAML + Spring Profiles → **YAML + Pydantic Settings** |
| 日志 | SLF4J + Logback → **structlog** |
| 包管理 | Maven → **uv** |

---

## 三、Python 项目结构

```
haossh-server/
├── pyproject.toml              # uv 依赖管理
├── Dockerfile
├── docker-compose.yml
│
├── src/
│   ├── haossh/
│   │   ├── main.py             # FastAPI 入口, lifespan, CORS
│   │   ├── config.py           # Pydantic Settings
│   │   │
│   │   ├── api/                # 表示层
│   │   │   ├── deps.py         # FastAPI 依赖注入
│   │   │   ├── routes/
│   │   │   │   ├── agent.py        # SSE 流式聊天
│   │   │   │   ├── ssh_terminal.py
│   │   │   │   ├── ssh_connection.py
│   │   │   │   └── ssh_file.py
│   │   │   └── schemas/            # Pydantic DTOs
│   │   │
│   │   ├── agent/              # Agent 编排层
│   │   │   ├── graph.py            # LangGraph ReAct 状态图
│   │   │   ├── state.py            # AgentState TypedDict
│   │   │   ├── nodes/
│   │   │   │   ├── prepare.py      # 初始化上下文、绑定终端
│   │   │   │   ├── llm_call.py     # LLM 调用 + 意图识别 + Prompt 富化
│   │   │   │   ├── tool_exec.py    # 工具执行 + 里程碑记录
│   │   │   │   └── finalize.py     # SSE done 事件 + 清理
│   │   │   ├── tools/
│   │   │   │   ├── ssh_execute.py  # SSH 命令执行工具
│   │   │   │   ├── ssh_check.py    # SSH 会话检查工具
│   │   │   │   └── list_commands.py
│   │   │   ├── intent/
│   │   │   │   ├── rule.py         # 规则分类器（< 1ms）
│   │   │   │   ├── llm.py          # LLM 分类器
│   │   │   │   └── tracker.py      # ContextTracker
│   │   │   ├── context/
│   │   │   │   ├── providers/      # TerminalState, Task, Milestone, ToolResult
│   │   │   │   └── reducers/       # Priority, SlidingWindow, Hybrid
│   │   │   ├── prompt/
│   │   │   │   ├── builder.py      # DynamicPromptBuilder
│   │   │   │   └── milestone.py    # MilestoneTracker
│   │   │   └── skills/
│   │   │       └── loader.py
│   │   │
│   │   ├── ssh/                # SSH 基础设施
│   │   │   ├── session.py          # asyncssh 连接池
│   │   │   ├── terminal.py         # PTY 终端会话 CRUD
│   │   │   ├── file.py             # SFTP 文件操作
│   │   │   └── security.py         # AES-256-GCM 密码加解密
│   │   │
│   │   ├── mcp/                # MCP 集成
│   │   │   ├── client.py           # MCP Client (SSE/Stdio)
│   │   │   └── ssh_server.py       # SSH MCP Server
│   │   │
│   │   ├── models/             # SQLAlchemy ORM
│   │   │   ├── base.py             # DeclarativeBase
│   │   │   ├── chat_session.py
│   │   │   ├── chat_message.py
│   │   │   ├── chat_milestone.py
│   │   │   ├── ssh_connection.py
│   │   │   └── ssh_connection_config.py
│   │   │
│   │   └── db/                 # 数据库工具
│   │       ├── session.py          # async session factory
│   │       └── repository.py       # 通用 CRUD Repository
│   │
│   ├── resources/              # 配置文件（直接复用 Java 版 YAML）
│   │   ├── agents/
│   │   │   ├── ssh-agent.yml
│   │   │   └── skills/             # 技能书（直接复用）
│   │   └── application-dev.yml
│   │
│   └── tests/
│       ├── test_ssh_session.py
│       ├── test_agent_graph.py
│       └── test_api.py
│
└── sql/
    └── haossh.sql                  # 直接复用
```

---

## 四、LangGraph 替代 ADK + 策略链

### 4.1 Java 现有 ReAct 节点链

```
RootNode（初始化上下文、加载消息历史、绑定终端会话）
  └→ AiCallNode（核心：意图识别 → 上下文裁剪 → Prompt 富化 → ADK Runner → SSE）
        ├→ [有工具] ToolCallNode → 回到 AiCallNode（循环）
        └→ [无工具] LoopDecisionNode（终止判断）
              ├→ [继续] 回到 AiCallNode
              └→ [终止] UserFeedbackNode（done 事件, 清理 ThreadLocal）
```

### 4.2 LangGraph 映射

5 个 Java 策略链节点压缩为 4 个 LangGraph 节点 + 条件边：

```
START → prepare → llm_call → [有 tool_calls] tool_exec → llm_call（循环）
                            → [无 tool_calls] finalize → END
```

| LangGraph Node | 吸收的 Java 节点 | 职责 |
|---|---|---|
| `prepare` | RootNode | 初始化上下文, 加载消息历史, 绑定终端会话 ID |
| `llm_call` | AiCallNode + LoopDecisionNode | 意图识别 → 上下文裁剪 → Prompt 富化 → LLM 调用 → 路由决策 |
| `tool_exec` | ToolCallNode | 执行 SSH 命令, 记录里程碑, 保存消息历史 |
| `finalize` | UserFeedbackNode | 发送 SSE done 事件, 清理上下文 |

### 4.3 AgentState

```python
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    session_id: str
    terminal_session_id: str | None
    step: int                   # 当前步数
    max_steps: int              # 默认 50
    tool_call_count: int        # 全局上限 200
    intent: str | None          # DIAGNOSE/CONFIGURE/DEPLOY/MONITOR/...
    stop_reason: str | None     # max_steps / error / done
    recent_commands: list[str]  # Prompt 注入用
```

### 4.4 关键简化

1. **ADK stateDelta → LangChain ToolMessage**：Java 版从 ADK 事件流的 `stateDelta` 检测工具执行，Python 版直接通过 `ToolMessage` 传递，更直观
2. **ThreadLocal → asyncio context**：Java 用 `InheritableThreadLocal` 传终端会话 ID，Python 直接用 `contextvars.ContextVar`
3. **策略树 → 条件边**：`AbstractMultiThreadStrategyRouter` 退化为 `add_conditional_edges`，代码量减少 80%
4. **Agent 注册链简化**：Java 的 6 节点策略链（RootNode → AiApiNode → ChatModelNode → AgentNode → AgentWorkflowNode → RunnerNode）退化为一个工厂函数，从 YAML 直接构建
5. **Thread.sleep 轮询 → asyncio 原生异步**：SSH 命令执行不再阻塞线程

---

## 五、asyncssh 替代 JSch

### 5.1 核心变更

| 概念 | JSch (Java) | asyncssh (Python) |
|---|---|---|
| 建立连接 | `jsch.getSession() + session.connect()` | `await asyncssh.connect()` |
| Shell 通道 | `session.openChannel("shell")` | PTY via `conn.create_process()` |
| 命令执行 | `out.write(cmd + "\n")` + Thread.sleep 轮询 | `await conn.run(cmd)` 直接返回 |
| SFTP | `ChannelSftp` | `await conn.start_sftp_client()` |
| 连接池 | `ConcurrentHashMap` | `dict[str, SSHClientConnection]` |

### 5.2 最大受益点：命令执行

```python
# Java (当前): ~70 行 Thread.sleep 轮询 + 提示符检测
# Python (目标):
async def execute_command(conn, cmd: str) -> str:
    result = await conn.run(cmd, timeout=30)
    return result.stdout or result.stderr
```

### 5.3 复杂度削减估算

| 文件 | Java 行数 | Python 预估 | 缩减 |
|---|---|---|---|
| TerminalSessionPort (SSH 终端) | 365 | ~100 | 3.6x |
| SshSessionPort (SSH 会话) | 86 | ~50 | 1.7x |
| SshFilePort (SFTP 文件) | 410 | ~200 | 2x |
| ReAct nodes (5 文件) | ~500 | ~150 | 3.3x |
| MySpringAI adapter | 63 | 0 (消除) | -- |
| Armory chain (12 文件) | ~400 | ~80 | 5x |
| PasswordEncryptor | 120 | ~20 | 6x |
| **总计 (186 文件)** | **~10,000** | **~4,000-5,000** | **2-2.5x** |

---

## 六、数据库 Schema（6 表，直接复用）

| 表 | 说明 |
|---|---|
| `chat_session` | 会话元数据（id, agent_id, user_id, title, message_count） |
| `chat_message` | 对话消息（session_id, role, content, tool_name, priority, token_count） |
| `chat_milestone` | 关键事件（session_id, type, content） |
| `ssh_connection` | SSH 连接（host, port, username, AES-256-GCM 加密的 password, auth_type, status） |
| `ssh_connection_config` | 连接高级配置 |
| `ssh_session_log` | SSH 会话日志 |

无外键约束，关系由应用层保证。SQLAlchemy 用 `relationship()` 做 ORM 级关联。

---

## 七、API 兼容性清单（33 个端点，保持不变）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/query_ai_agent_config_list` | 查询 agent 配置列表 |
| POST/GET | `/api/v1/create_session` | 创建会话 |
| POST | `/api/v1/chat` | 同步对话 |
| POST | `/api/v1/chat_stream` | **SSE 流式对话（核心）** |
| POST | `/api/v1/ssh/terminal/open` | 打开终端 |
| POST | `/api/v1/ssh/terminal/exec` | 执行命令 |
| POST | `/api/v1/ssh/terminal/write` | 终端输入 |
| GET | `/api/v1/ssh/terminal/read` | 读取终端输出 |
| POST | `/api/v1/ssh/terminal/resize` | 调整大小 |
| POST | `/api/v1/ssh/terminal/close` | 关闭终端 |
| POST | `/api/v1/ssh/create_connection` | 创建连接 |
| POST | `/api/v1/ssh/update_connection` | 更新连接 |
| POST | `/api/v1/ssh/delete_connection` | 删除连接 |
| GET | `/api/v1/ssh/get_connection` | 查询连接 |
| GET | `/api/v1/ssh/connection_list` | 连接列表 |
| POST | `/api/v1/ssh/connect` | 建立连接 |
| POST | `/api/v1/ssh/disconnect` | 断开连接 |
| GET | `/api/v1/ssh/file/tree` | 文件树 |
| GET | `/api/v1/ssh/file/content` | 文件内容 |
| GET | `/api/v1/ssh/file/content-chunk` | 分块读取 |
| POST | `/api/v1/ssh/file/create-file` | 创建文件 |
| POST | `/api/v1/ssh/file/create-directory` | 创建目录 |
| POST | `/api/v1/ssh/file/rename` | 重命名 |
| POST | `/api/v1/ssh/file/delete` | 删除 |
| POST | `/api/v1/ssh/file/save-content` | 保存内容 |
| POST | `/api/v1/ssh/file/upload` | 上传文件 |
| GET | `/api/v1/ssh/file/download` | 下载文件 |
| POST | `/api/v1/ssh/agent/bind_terminal` | 绑定终端 |
| POST | `/api/v1/ssh/agent/unbind_terminal` | 解绑终端 |
| GET | `/api/v1/ssh/agent/query_binding` | 查询绑定 |

响应格式保持 `{"code": "0000", "info": "成功", "data": [...]}`。

---

## 八、配置管理

保留现有两层 AI API 配置：
- **Agent AI API**：ssh-agent.yml 中，agent 调用 LLM 时使用
- **意图识别 AI API**：application-dev.yml 中，`intent-ai-api` 独立配置

Pydantic Settings 支持环境变量覆盖（对应 Spring 宽松绑定）：

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HAOSSH_")
    agent_ai: AgentAIApiConfig
    intent_ai: IntentAIApiConfig
    database_url: str = "mysql+asyncmy://root:12345678@127.0.0.1:3306/haossh"
```

Docker Compose 环境变量示例：
```
HAOSSH_AGENT_AI__BASE_URL=https://apis.itedus.cn
HAOSSH_AGENT_AI__API_KEY=sk-xxx
```

---

## 九、分阶段迁移计划

### Phase 0：项目骨架（1 天）
- pyproject.toml、FastAPI 入口、健康检查端点
- Pydantic Settings 配置加载、目录结构搭建
- Dockerfile（python:3.12-slim）

### Phase 1：SSH 层（2 天）
**目标：能连 SSH、执行命令、SFTP 传文件，不依赖 Agent**

- asyncssh 连接池 + PTY 终端会话 CRUD + SFTP 文件操作
- AES-256-GCM 密码加解密
- REST API 全部 SSH 端点
- 测试：Docker sshd 容器集成测试

### Phase 2：Agent 核心（3 天）
**目标：能聊天、能调 SSH 工具、有 ReAct 循环**

- LangGraph ReAct 状态图（prepare → llm_call → tool_exec → finalize）
- SSH 命令执行工具（LangChain Tool）
- LLM 配置从 YAML 读取，支持 OpenAI/DeepSeek 兼容 API
- SSE 流式输出 `/api/v1/chat_stream`
- 测试：连接测试服务器，对话"看看服务器状态"

### Phase 3：上下文与意图（2 天）
**目标：带记忆的多轮对话，意图感知**

- 规则 + LLM 两层意图分类器
- Provider-Reducer 上下文管道（4 providers + 3 reducers）
- 动态 Prompt 构建 + 里程碑追踪
- SQLAlchemy 异步 Repository，消息历史持久化
- 测试：多轮对话验证上下文裁剪

### Phase 4：MCP 与技能书（1 天）
**目标：外部工具集成，Agent Skills 系统**

- MCP Client (SSE/Stdio) via 官方 Python SDK
- SSH MCP Server
- Skills 加载器（复用现有 skills 目录）
- 测试：接入百度搜索 MCP 验证工具链

### Phase 5：完善与上线（1 天）
- 错误处理、结构化日志、Docker Compose 联调
- 与 Java 版功能对齐检查清单、README 更新

---

## 十、关键风险与缓解

| 风险 | 缓解 |
|------|------|
| asyncssh PTY 行为与 JSch 不同 | Phase 1 用 Docker sshd 充分测试 |
| LangGraph 状态管理与 ADK 事件流差异 | 先画状态图确认逻辑再写代码 |
| SSE 事件格式不兼容前端 | 保持 `ReActEventDTO` JSON 格式完全一致 |
| MCP Python SDK API 与 Java 版差异 | 逐个 transport 实现并测试 |
| 多 Agent workflow（sequential/parallel） | V1 只支持单 Agent ReAct（ssh-agent.yml 实际只用了单 Agent），多 Agent 后续用 subgraph |

---

## 十一、不迁移的内容

- YAML agent 配置文件 → 直接复制复用
- SQL schema → 直接复用
- Agent Skills 脚本（shell/python）→ 直接复制复用
- 前端客户端 → 不变，API 保持兼容
