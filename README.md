# haossh-server · AI 驱动的 SSH 智能运维平台

把 SSH 操作与 AI 操作统一收敛到服务端，更有效地控制企业风险（避免客户端误执行危险命令、泄露核心服务器信息）。

> 本仓库为 Python 重写版本（分支 `main-python`），由原 Java/Spring Boot 版本迁移而来。

## 技术栈

- **语言**：Python ≥ 3.11
- **Web 框架**：FastAPI + uvicorn
- **SSH**：asyncssh（异步）
- **AI Agent**：pydantic-ai（默认接入 DeepSeek）+ langgraph
- **加密**：cryptography（AES-256-GCM）
- **配置**：pydantic-settings
- **依赖管理**：uv

## 目录结构

```
src/haossh/
├── main.py              FastAPI 入口，挂载 5 个路由
├── config.py            pydantic-settings 全局配置（HAOSSH_ 前缀）
├── api/
│   ├── routes/          chat / ssh_connection / ssh_terminal / ssh_file / terminal_binding
│   └── schemas/         对应的 Pydantic 请求模型
├── ssh/
│   ├── session.py       SSH 连接池 + 心跳检测
│   ├── terminal.py      PTY 交互式终端（create/read/write/resize/close/exec）
│   ├── file.py          SFTP 文件操作（list/read/write/upload/download/...）
│   └── security.py      AES-256-GCM 密码加解密
└── agent/
    ├── agent.py         pydantic-ai Agent（DeepSeek + OpenAIChatModel）
    ├── tools.py         Agent 工具定义
    └── prompts/         系统提示词（资深 SRE）
```

## 已实现能力

| 模块 | 说明 |
|------|------|
| SSH 连接管理 | CRUD（内存存储）+ 连接 / 断开 / 心跳检测 |
| PTY 交互式终端 | 自定义 `SSHClientSession` 缓存远端输出，前端轮询消费 |
| 单命令执行 | 非交互式命令执行（不走 PTY） |
| SFTP 文件管理 | 浏览 / 读写 / 重命名 / 删除 / 上传 / 下载 |
| 密码加密 | AES-256-GCM，密钥通过环境变量注入 |
| AI 对话 | SSE 流式响应 |

## 环境变量

通过 `.env` 文件或环境变量配置（前缀 `HAOSSH_`）：

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `HAOSSH_AGENT_MODEL_NAME` | Agent 模型名 | `deepseek-v4-flash` |
| `HAOSSH_AGENT_BASE_URL` | 模型 API 地址 | `https://api.deepseek.com` |
| `HAOSSH_AGENT_API_KEY` | 模型 API Key | 空 |
| `HAOSSH_SECRET_KEY` | AES 加密密钥（base64 编码的 32 字节） | 未设置时使用默认值（仅开发环境） |

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置环境变量

```bash
cp .env.example .env  # 按需创建并填入 API Key
```

### 3. 启动服务

```bash
uv run uvicorn haossh.main:app --host 0.0.0.0 --port 8091 --reload
```

### 4. 健康检查

```bash
curl http://localhost:8091/api/v1/health
```

## API 概览

所有路由前缀：`/api/v1`

| 路由 | 功能 |
|------|------|
| `GET  /health` | 健康检查 |
| `POST /chat_stream` | AI 对话（SSE 流式） |
| `POST /ssh/create_connection` | 创建 SSH 连接记录 |
| `POST /ssh/connect` | 建立 SSH 连接 |
| `POST /ssh/disconnect` | 断开 SSH 连接 |
| `GET  /ssh/connection_list` | 连接列表 |
| `POST /ssh/terminal/open` | 打开 PTY 终端 |
| `POST /ssh/terminal/write` | 写入终端 |
| `GET  /ssh/terminal/read` | 读取终端输出 |
| `POST /ssh/terminal/resize` | 调整终端大小 |
| `POST /ssh/terminal/close` | 关闭终端 |
| `POST /ssh/terminal/exec` | 执行单条命令 |
| `GET  /ssh/file/tree` | 目录浏览 |
| `GET  /ssh/file/content` | 读取文件内容 |
| `POST /ssh/file/upload` | 上传文件 |
| `GET  /ssh/file/download` | 下载文件 |

## 后续规划

参见 `docs/intent-recognition-enhancement-design.md`：

- **Phase 1**：动态 Prompt 构建
- **Phase 2**：上下文记忆管理（Provider-Reducer 管道）
- **Phase 3**：意图识别（规则 + LLM 两层分类器）
- **Phase 4**：意图增强（信号提取 → 服务器上下文搜索）
- **Phase 5**：会话持久化（MySQL + Redis）

当前数据存储仍为内存 dict，计划在 Phase 3 迁移到数据库。

## 部署

`docs/dev-ops/` 下提供 docker-compose 编排文件，可用于服务编排与部署。
