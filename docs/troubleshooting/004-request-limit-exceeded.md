# request_limit 超限（部署复杂任务 50 轮不够）

> 日期：2026-07-20
> 状态：已解决
> 影响范围：部署等多步骤任务

## 现象

部署 haossh-server 到一半，agent 停止响应：

```
The next request would exceed the request_limit of 50.
```

## 根因

pydantic-ai 默认 `request_limit=50`。部署场景一次任务可能涉及：
- 探测环境（2-3 轮）
- git clone（3-5 轮，含重试）
- uv sync（5-10 轮，含后台轮询）
- 配置文件（3-5 轮）
- 启动服务（3-5 轮）
- 排障（10-20 轮）

合计 30-50 轮，很容易超限。

## 解决方案

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

## 效果

部署等复杂任务不再中途超限。200 轮足够覆盖完整部署流程 + 排障。

| 场景 | 建议 request_limit |
|------|-------------------|
| 日常问答 | 50（默认） |
| 运维操作 | 100 |
| 部署等复杂任务 | 200 |
