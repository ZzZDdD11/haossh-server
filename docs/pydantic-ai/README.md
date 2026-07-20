# Pydantic AI 学习文档

> 来源：[官方文档](https://pydantic.dev/docs/ai/overview/) + 本项目实践
> 版本：pydantic-ai 2.13.0

## 文档结构

| 文件 | 主题 | 我们项目状态 |
|------|------|------------|
| [01-overview.md](./01-overview.md) | 框架总览：定位、架构、核心概念 | 基础认知 |
| [02-tools-deps.md](./02-tools-deps.md) | 工具系统与依赖注入 | ✅ 已用（核心） |
| [03-multi-turn-context.md](./03-multi-turn-context.md) | 多轮对话与上下文管理 | ⏳ 多轮已实现，上下文管理待做 |
| [04-advanced-ecosystem.md](./04-advanced-ecosystem.md) | 高级特性与生态 | ❌ 多数未用 |
| [05-dynamic-system-prompt.md](./05-dynamic-system-prompt.md) | 动态 System Prompt（含 `dynamic=True` 踩坑） | ✅ 已用（权限+连接状态） |

## 学习路径

```
01 总览（建立全景认知）
  ↓
02 工具与依赖（对照本项目 agent.py/tools.py/deps.py 理解）
  ↓
03 多轮与上下文（当前阶段重点，含官方 Compaction 方案）
  ↓
04 高级特性（按需深入：结构化输出/Capabilities/Harness/Evals）
  ↓
05 动态 System Prompt（dynamic=True 踩坑实录）
```

## 与本项目对照总表

| 文档概念 | 本项目文件 | 状态 |
|---------|-----------|------|
| Agent | `src/haossh/agent/agent.py` | ✅ |
| Dependencies + RunContext | `src/haossh/agent/deps.py` + `tools.py` | ✅ |
| Function Tools | `src/haossh/agent/tools.py`（5个工具） | ✅ |
| prepare 钩子 | `tools.py` require_connection | ✅ |
| 流式输出（事件流） | `src/haossh/api/routes/chat.py` agent.iter | ✅ |
| message_history 多轮 | `chat.py` histories dict | ✅ |
| 结构化输出 (Output) | output_type=str 纯文本 | ❌ |
| 动态指令 (@agent.instructions) | 静态 system_prompt | ❌ |
| Capabilities 系统 | 用 Tool 注册 | ❌ |
| Compaction（消息压缩） | 全量传递无管理 | ❌ 待做 |
| Overflowing Tool Output | 未处理 | ❌ |
| 工具审批 | 讨论过未实现 | ❌ |
| Harness 能力库 | 自己实现 | ❌ |
| Pydantic Evals | 未用 | ❌ |
| Logfire 可观测性 | 未用 | ❌ |

## 关键启发

1. **Compaction + Overflowing Tool Output** 是官方的上下文管理方案，比自己写 trim_history 靠谱——见 03 文档
2. **Harness 的 Shell/Memory/FileSystem** 能力和我们手写的高度重合，可能重复造轮子——见 04 文档
3. **Capabilities 系统** 是比 Tool 注册更模块化的组织方式——见 04 文档
