# 部署任务中 Agent 认知混淆（缺少工作区锚点）

> 日期：2026-07-22
> 状态：方案设计完成，待实现
> 影响范围：多项目/多任务场景下的部署与文件操作

## 现象

实际部署 `HaoEnglishTeacher` 项目时，观察到两个问题：

1. **已给出的信息被忽略**：用户第一条消息就带了仓库 URL，agent 仍反问"请问您要部署哪个仓库？"
2. **认错项目**：排查 `laravel.sql` 时，agent 声称当前项目是 `haossh-server`（同一台服务器上另一个此前部署过的项目），而不是正在处理的 `HaoEnglishTeacher`。

## 根因

**日志（log）与状态（state）被混淆**。Agent 唯一的记忆载体是消息历史 + milestones，两者本质都是"事件时间序列"，没有一个独立、持久、跨轮次不变的"当前事实"结构。每一轮决策都靠模型重新从原始文本里推理"现在情况是什么"——信息稀疏时能猜对，一旦环境里出现相似但无关的信号（同机器上的另一个项目残留文件），就会被误导。

具体拆解：

- **问题1**：URL 是消息里已给出的结构化实体，但 prompt 只有软性文字指令（"不要问多余问题"），没有强制的"先提取已知信息"步骤，模型套用了通用流程模板去反问。
- **问题2**：`execute_command` 每次 `conn.run()` 都开全新 SSH channel，**不共享 shell 状态**——`cd` 只在当次调用生效，下一次工具调用又回到登录目录。没有 workspace 兜底时，模型全局搜索文件撞见了无关项目的残留，且没有机制告诉它"这和当前任务无关"。

## 我们如何借鉴开源项目

调研 OpenHands、SWE-agent、Devin/Aider 后提炼出三个可复用模式：

| 项目 | 做法 | 对应我们的改造点 |
|---|---|---|
| OpenHands | 每个会话（`sid`）绑定一个独立 Runtime 实例，内部维持**持久 Bash Shell**，cwd 状态由客户端显式追踪 | 修复 `exec_command` 的 shell 无状态化 |
| SWE-agent (ACI) | 每次工具执行后，**强制在结果里回显当前状态**（如 `Current directory: xxx`），而非只塞进 system prompt 头部 | workspace 贴着 `execute_command` 结果强制回显，不是软提示 |
| Devin / Aider | workspace 是会话生命周期内的锚点，只有显式动作（新会话、`/add`）才变更，不被动污染 | workspace 更新只认"新 git clone" 或用户显式换项目 |

核心结论：**能确定性获取、应该保持不变的信息，一旦交给模型逐轮自由推理，就会退化成不可靠的猜测**——这与之前 [008](./008-milestone-hybrid-mode.md) 号问题（error/solution 不该靠模型判断）是同一条原则的复现。

## 解决方案（软隔离，非物理容器）

不采用 Docker 级硬隔离——运维 agent 的职责本身是全机器范围（查系统日志、改系统配置），物理隔离会束缚合法操作。改用**软钉住 + 强制回显**：

1. **建立**：规则识别 `git clone <url> <path>` 或用户消息中的部署路径，写入 `Conversation.workspace_path`（落库，跨刷新保持，同 milestone 的持久化方式）
2. **保持**：`execute_command` 自动拼接 `cd {workspace_path} &&` 前缀（应用层模拟持久 shell，不改动 SSH 连接层，改动面小）
3. **可见**：工具结果强制带 `[workspace=xxx]` 标签紧跟输出，而非只写入 system prompt 顶部（对照 007/008 号经验：docstring/软提示不可靠，贴近决策点的强制标记才有效）
4. **变更**：只有识别到新的 `git clone`（不同 URL）或用户显式说"换项目"才更新，避免被一次无关的 `find` 结果污染
5. **越界不拦截**：跳出 workspace 的操作（`systemctl`、`/etc` 等）正常执行，但记一条 milestone 留痕，不做物理拦截

## 后续

方案已确认，尚未编码实现。实现时预计涉及：`db/models.py`（`Conversation.workspace_path` 字段）、`agent/tools.py`（`execute_command` 前缀拼接 + 结果标签）、规则提取逻辑（复用 `_auto_record_milestone` 同款"规则捕获而非模型判断"模式）。
