# 工作区锚点落地：观测真实 cwd，而非声明式设置

> 日期：2026-07-22
> 状态：已解决
> 影响范围：多项目场景下 agent 的任务范围认知（009 号方案的落地）

## 背景

009 号方案确认了"工作区锚点"能防止 agent 把无关项目的残留信息当成当前任务上下文。方案讨论过程中最初设想过两种建立方式：正则捕获 `git clone` 命令、或加一个 `set_workspace` 工具让模型显式声明。

## 方案演进（为什么最终都放弃了）

1. **`workspace_repo_url` 字段**：想用它判断"是不是同一个项目重试"，但发现直接比较解析后的绝对路径就够幂等，加这层 URL 反而假设了"工作区一定来自 git 项目"，不成立（用户可能直接指定一个已存在的目录）
2. **`set_workspace` 工具**：让模型显式声明工作区，本质上又是一次"要不要调用"的自由判断——重复了 008 号 milestone 已经踩过的坑（漏调/误调不可控）

## 最终方案：直接观测，不声明

10 号问题里已经实现了持久 shell，且已经在用 sentinel 标记解析 exit code。**在同一个标记里顺手带上 `$PWD`，成本几乎为零**：命令执行后，shell 真实所在的目录就是事实，不需要模型做任何声明性动作。

- `workspace_path` 直接等于 shell 观测到的真实 cwd，变化时才写库（天然幂等）
- 不需要判断"是否新项目"，不需要正则匹配 `git clone`，不需要额外的工具
- shell 崩溃重建时，用已知的 `workspace_path` 自动 `cd` 回去，避免模型重新导航

## 解决方案要点

1. **`PersistentShell.run()` 标记里追加 `$PWD`**，返回值从 `(stdout, stderr, exit_status)` 扩展为 `(stdout, stderr, exit_status, cwd)`
2. **`get_shell()` 新增 `known_workspace` 参数**：仅在重建时使用，自动 `cd` 恢复到历史工作区
3. **`execute_command` 观测 cwd 变化才写库**（`repo_conversation.update_workspace`），并在结果里贴 `[workspace=xxx]` 标签（贴近决策点，呼应 SWE-agent 强制回显经验）
4. **动态 system prompt 兜底**（`workspace_status`）：万一某轮没调用工具，模型仍能在决策前看到当前工作区，防止把无关路径误认成当前任务范围
5. **续聊时从 DB 载入历史 workspace_path** 注入 `AgentDeps`，供 shell 重建时恢复用

## 效果

`tests/test_persistent_shell.py` 新增测试6，验证核心行为：

```
测试6: 工作区观测 + 崩溃重建后自动恢复 cwd
  cd /var 后观测 cwd='/var'
  重建后 pwd -> '/var\n' cwd='/var'
  [PASS] 工作区观测 + 崩溃后自动恢复成功
```

全部 6 项验证通过（含 10 号问题遗留的 5 项）。

## 与之前几次改造的共同模式

这是同一条原则第三次复现：**能确定性获取的信息，不要交给模型自由判断/声明，直接从结构化信号里取**。
- 008 号：error/solution 从 exit code 直接判定，不靠模型判断
- 010 号：cd 状态从持久 shell 直接保持，不靠模型每次手动拼前缀
- 011 号（本次）：workspace 从真实 cwd 直接观测，不靠模型调用工具声明

## 后续

009 号方案里"越界不拦截但记 milestone"这一条本次未实现（判断"是否操作了 workspace 外资源"成本高、易误报），保持观察，暂不做。
