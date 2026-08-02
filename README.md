# haossh-server · AI 驱动的 SSH 智能运维平台

`haossh` 是一个面向运维场景的 Web 控制台：把 SSH 连接、远程命令、SFTP 文件操作、Web 终端和 AI Agent 收敛到服务端统一管理，并通过多租户认证保证不同组织之间的数据隔离。

> 本仓库为 Python/FastAPI 版本（分支 `main-python`）。

## 核心亮点

| 能力 | 说明 |
|---|---|
| 多租户认证 | 注册即创建组织（Tenant），用户登录后通过 httpOnly Cookie 保存 JWT 登录态 |
| 数据隔离 | `SSHConnection` / `Conversation` 均带 `tenant_id`，repo 层强制按租户查询，避免越权访问 |
| AI 运维助手 | 基于 `pydantic-ai` 接入 DeepSeek，支持 SSE 流式输出和工具调用 |
| SSH 连接管理 | 连接记录持久化到 SQLite，敏感凭据使用 AES-GCM 加密存储 |
| WebSocket 终端 | 前端基于 `xterm.js`，后端基于 PTY，支持实时输入输出和窗口 resize |
| SFTP 文件能力 | 浏览、读取、保存、上传、下载、重命名、删除远程文件 |
| 前端控制台 | 登录/注册页 + 运维风格 NOC 控制台 + 可拖拽终端面板 |

## 技术栈

- **语言**：Python ≥ 3.11
- **Web 框架**：FastAPI + uvicorn
- **数据库**：SQLModel + SQLite（异步 `aiosqlite`）
- **认证**：argon2-cffi（密码哈希）+ PyJWT（JWT）+ httpOnly Cookie
- **SSH**：asyncssh
- **AI Agent**：pydantic-ai（默认 DeepSeek）+ langgraph
- **前端**：原生 HTML/CSS/JS + xterm.js
- **加密**：cryptography AES-GCM
- **依赖管理**：uv

## 目录结构

```text
src/haossh/
├── main.py                    FastAPI 入口，注册路由、中间件、静态资源
├── config.py                  pydantic-settings 全局配置（HAOSSH_ 前缀）
├── auth/
│   ├── security.py            密码哈希、JWT 签发/校验、Cookie 常量
│   └── middleware.py          HTTP JWT 认证中间件
├── api/
│   ├── routes/                auth / chat / conversation / ssh_*
│   └── schemas/               请求 DTO
├── db/
│   ├── models.py              Tenant / User / SSHConnection / Conversation 等模型
│   ├── repo_user.py           用户与租户数据访问
│   ├── repo_connection.py     SSH 连接数据访问（含租户归属校验）
│   └── repo_conversation.py   对话数据访问（含租户归属校验）
├── ssh/
│   ├── session.py             SSH 连接池 + 自动重连
│   ├── terminal.py            PTY 终端 create/read/write/resize/close/exec
│   ├── file.py                SFTP 文件操作
│   └── security.py            SSH 凭据 AES-GCM 加解密
└── agent/
    ├── agent.py               pydantic-ai Agent
    ├── tools.py               Agent 工具定义
    └── prompts/               运维 Agent 系统提示词

src/resources/static/
├── login.html / login.js      登录/注册页
├── index.html / app.js        主控制台
├── style.css                  NOC 运维风格 UI
└── vendor/xterm/              xterm.js 与 fit addon
```

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

至少需要填写：

```env
HAOSSH_AGENT_API_KEY=你的模型 API Key
HAOSSH_JWT_SECRET=一个足够长的随机字符串
HAOSSH_SECRET_KEY=base64 编码的 32 字节随机密钥
```

生成密钥示例：

```bash
# JWT 签名密钥
python -c "import secrets; print(secrets.token_urlsafe(48))"

# AES-GCM 密钥（base64 编码 32 字节）
python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

### 3. 启动服务

```bash
uv run uvicorn haossh.main:app --host 0.0.0.0 --port 8091 --reload
```

### 4. 打开页面

```text
http://127.0.0.1:8091/
```

未登录会自动跳转到：

```text
http://127.0.0.1:8091/login.html
```

健康检查：

```bash
curl http://127.0.0.1:8091/api/v1/health
```

## API 概览

所有 API 前缀：`/api/v1`

| 路由 | 功能 |
|---|---|
| `GET /health` | 健康检查 |
| `POST /auth/register` | 注册：创建 Tenant + User，并写入 httpOnly Cookie |
| `POST /auth/login` | 登录：校验密码并写入 httpOnly Cookie |
| `POST /auth/logout` | 登出：清除 Cookie |
| `GET /auth/me` | 查询当前登录用户 |
| `POST /chat_stream` | AI 对话，SSE 流式响应 |
| `GET /conversation/list` | 当前租户的对话列表 |
| `GET /conversation/{id}/messages` | 查询对话消息 |
| `POST /conversation/{id}/terminate` | 手动终止对话 |
| `POST /ssh/create_connection` | 创建 SSH 连接记录 |
| `POST /ssh/connect` | 建立 SSH 连接 |
| `POST /ssh/disconnect` | 断开 SSH 连接 |
| `GET /ssh/connection_list` | 当前租户的连接列表 |
| `GET /ssh/get_connection` | 查询连接详情 |
| `POST /ssh/terminal/open` | 打开 PTY 终端（REST 兼容接口） |
| `POST /ssh/terminal/write` | 写入终端（REST 兼容接口） |
| `GET /ssh/terminal/read` | 读取终端输出（REST 兼容接口） |
| `POST /ssh/terminal/resize` | 调整终端大小（REST 兼容接口） |
| `POST /ssh/terminal/close` | 关闭终端（REST 兼容接口） |
| `WS /ssh/terminal/ws?connectionId=...` | WebSocket 实时终端 |
| `POST /ssh/terminal/exec` | 执行单条命令 |
| `GET /ssh/file/tree` | 目录浏览 |
| `GET /ssh/file/content` | 读取文件内容 |
| `POST /ssh/file/save-content` | 保存文件内容 |
| `POST /ssh/file/upload` | 上传文件 |
| `GET /ssh/file/download` | 下载文件 |

## WebSocket 终端协议

浏览器连接：

```text
WS /api/v1/ssh/terminal/ws?connectionId=<connectionId>
```

WebSocket 握手不会经过 HTTP 中间件，因此该接口内部会手动读取 Cookie 并校验 JWT，同时校验 `connectionId` 属于当前租户。

前端发给后端使用 JSON：

```json
{ "type": "input", "data": "ls\r" }
{ "type": "resize", "cols": 120, "rows": 35 }
```

后端推给前端仍是原始 PTY 输出文本，前端直接交给 `xterm.js` 渲染。

## 多租户与安全设计

- `Tenant` 是组织边界，注册时自动创建。
- `User` 归属唯一 `tenant_id`，MVP 不做邀请成员。
- `SSHConnection` / `Conversation` 都存 `tenant_id` 和 `user_id`。
- 路由层从 `request.state` 读取身份，不再信任前端传 `userId`。
- repo 层提供 `get_owned(...)` / `list_by_tenant(...)` 这类强制归属校验的方法。
- 不属于当前租户的数据统一返回"不存在/无权访问"，避免信息泄露。
- 用户密码只存 argon2 哈希，不可逆。
- SSH 密码/私钥使用 AES-GCM 加密后落库。
- JWT 存在 httpOnly Cookie，降低 XSS 直接窃取 token 的风险。
- WebSocket 入口单独做 JWT + 连接归属校验，避免绕过 HTTP 中间件。

## 开发与验证

运行测试：

```bash
uv run pytest
```

常用手动验证：

```bash
# 注册
curl -i -c /tmp/haossh.cookie \
  -X POST http://127.0.0.1:8091/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"demopass123","orgName":"Demo团队"}'

# 登录态
curl -b /tmp/haossh.cookie http://127.0.0.1:8091/api/v1/auth/me

# 当前租户连接列表
curl -b /tmp/haossh.cookie http://127.0.0.1:8091/api/v1/ssh/connection_list
```

## 后续可扩展方向

- 审计日志：记录用户、租户、连接、命令、结果和时间。
- 危险命令策略：先做静态规则拦截，再扩展审批流。
- RBAC：当前只保留 `role` 字段，尚未启用静态权限守卫。
- 数据库迁移：从 SQLite 切换到 PostgreSQL，并引入迁移工具。
- 部署：补充 Dockerfile / docker-compose，便于演示和交付。
