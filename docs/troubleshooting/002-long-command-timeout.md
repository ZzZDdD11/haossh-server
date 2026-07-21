# 长命令超时（多层超时叠加 + error 为空）

> 日期：2026-07-20
> 状态：已解决
> 影响范围：部署等长命令场景

## 现象

部署时执行 `uv sync`，工具反复超时失败：

```
WARNING 命令执行失败 command=uv sync error=    ← error 为空！
ModelRetry: Timed out after 60.0 seconds.
UnexpectedModelBehavior: Tool 'execute_command' exceeded max retries
```

## 根因

### 1. 三层超时叠加

| 层级 | 参数 | 原值 | 问题 |
|------|------|------|------|
| SSH 命令级 | `execute_command(timeout=)` | 30s | 命令本身超时 |
| deps 上限 | `max_command_timeout` | 60s | `min(timeout, 60)` 截断 |
| Tool 框架级 | `Tool(timeout=)` | 60s | pydantic-ai 框架超时 |

LLM 传 `timeout=120`，但 `min(120, 60)=60`，被截断。

### 2. error 为空

`asyncio.TimeoutError` 的 `str()` 是空字符串，日志里 `error=` 后面空白，不利于排查。

## 解决方案

### 超时调整

```python
# deps.py: 60 → 300
max_command_timeout: int = 300

# tools.py: 默认 30 → 120
async def execute_command(ctx, command, timeout: int = 120):

# tools.py: Tool 60 → 300, retries 1 → 2
Tool(execute_command, max_retries=2, timeout=300.0)
```

### 超时错误明确化

```python
except asyncio.TimeoutError:
    raise ModelRetry(
        f"命令执行超时（{timeout}秒）。长时间命令请改用 run_background 后台执行。"
    ) from None
```

### 后台执行方案（彻底解决）

新增 `run_background` + `check_task` 工具，长命令在服务器后台运行（nohup），不受超时影响。

## 效果

- 短命令：正常执行（timeout 够用）
- 长命令：LLM 自动选 `run_background` 后台执行 + `check_task` 轮询
- 超时时 LLM 收到明确提示，自动切换策略
