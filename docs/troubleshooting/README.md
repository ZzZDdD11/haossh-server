# 问题排查记录

> 本目录记录项目开发过程中遇到的问题、排查过程和解决方案。
> 每个问题一个文件，编号命名，便于追溯。

## 记录格式

每个文件遵循以下结构：

```markdown
# 问题标题

> 日期：YYYY-MM-DD
> 状态：已解决 / 排查中
> 影响范围：简述影响

## 现象
看到了什么（日志、报错、行为异常）

## 根因
为什么会出现（源码级分析）

## 解决方案
怎么修的（代码改动 + 配置调整）

## 效果
修复后的验证结果
```

## 问题索引

| 编号 | 问题 | 日期 | 状态 |
|------|------|------|------|
| [001](./001-trim-history-amnesia.md) | agent 对话失忆（trim_history 裁剪到只剩5条） | 2026-07-20 | 已解决 |
| [002](./002-long-command-timeout.md) | 长命令超时（多层超时叠加 + error 为空） | 2026-07-20 | 已解决 |
| [003](./003-dynamic-prompt-not-working.md) | 动态 system prompt 不生效（dynamic=False） | 2026-07-20 | 已解决 |
| [004](./004-request-limit-exceeded.md) | request_limit 超限（部署复杂任务50轮不够） | 2026-07-20 | 已解决 |
| [005](./005-ssh-connection-drop.md) | SSH 连接断开导致工具失败（open failed） | 2026-07-20 | 已解决 |
| [006](./006-ssh-channel-open-error.md) | SSH 连接半开状态导致工具失败（ChannelOpenError） | 2026-07-21 | 已解决 |
