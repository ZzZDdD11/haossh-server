# MilestoneTracker 召回率低（LLM 不主动记录隐含事件）

> 日期：2026-07-22
> 状态：已解决
> 影响范围：关键事件记忆系统（MilestoneTracker）

## 现象

007 号实验（纯 LLM 工具调用方案）跑 4 个用例，只过 2/4：

```
用例: 用户报告错误        [FAIL] 期望调 record_milestone 但没调
用例: 用户改需求          [FAIL] 期望调 record_milestone 但没调
用例: 任务完成            [PASS]
用例: 普通提问不记录      [PASS]
```

LLM 只在**明显的关键节点**（用户说"搞定了"）主动记录，但在**隐含的关键事件**（用户转述"刚才报错了"、用户说"不对，换一个方向"）上不会主动调工具。

## 我们如何发现

先复盘运维场景的状态点是否固定：

| 阶段 | 状态点 | 信号来源 |
|---|---|---|
| 开始 | task_start | 用户首条消息 |
| 阻塞 | error | 命令 exit code / 异常 |
| 恢复 | solution | error 之后下一次成功 |
| 偏移 | decision | 用户消息语义（需模型判断） |
| 结束 | done | 任务是否真的完成（需模型判断） |

关键结论：**error / solution / task_start 三类事件其实有结构化信号可以直接拿到（exit code、异常、首条消息），根本不需要交给 LLM 重新判断一遍**。007 号实验召回低的两个用例里，"用户报告错误"本质就是 error 类事件的自然语言表述——而错误信息本来就该在 `execute_command` 执行时被直接捕获，不该指望 LLM 在事后对话里"回忆"起来主动记录。

之前把所有状态点都交给 LLM，是套用了"通用 agent"的思路（什么场景都能用但召回不稳定），忽略了运维场景操作结果本身是结构化的这一特殊性。

## 根因

方案设计上的错配：**把"能规则化的事件"也交给了模型判断**。

- LLM 的判断标准是"这件事值得我主动说一句吗"，而不是"这个事件符合 error 分类吗"
- 对日常对话中的常见场景（用户转述问题、改需求），LLM 倾向于直接响应新指令，而不会额外调工具记录
- LLM 在"判断什么不该记"上表现好（不误检），但在"判断什么该记"上不如规则（漏检）——这与 007 号实验的对比结论一致

## 解决方案

**混合模式**：能规则化的事件用代码兜底，只把需要语义判断的两类留给模型。

1. **规则层自动记录**（新增 `tools.py::_auto_record_milestone`）：
   - `execute_command` / `run_background` 的 `exit_status != 0` 或抛异常 → 自动记 `error`
   - 失败后下一次 `execute_command` 成功 → 自动记 `solution`（靠 `AgentDeps.last_command_failed` 单轮内跟踪状态）
   - 新建对话时首条消息 → 自动记 `task_start`（`chat.py` 路由层）

2. **模型层收窄为两类**：`record_milestone` 工具的 `event_type` 参数类型从 `str` 改成 `Literal["decision", "done"]`：
   ```python
   async def record_milestone(
       ctx: RunContext[AgentDeps],
       event_type: Literal["decision", "done"],
       content: str,
   ) -> str: ...
   ```
   这不是靠 docstring 约束（docstring 说明不保证模型遵守），而是让 pydantic-ai 把 `Literal` 编译进工具的 JSON Schema（`enum` 字段），模型在 function calling 时**根本看不到** `error`/`solution`/`task_start` 这些选项，从 schema 层面杜绝误调和重复记录。

3. 更新测试用例：原"用户报告错误"用例的期望从"应调 record_milestone(error)"改为"不应调用"（因为该场景现在由规则层在命令执行时自动记录，模型无需重复记）。

## 效果

重跑 `tests/eval_milestone.py`，4/4 全过：

```
用例: 用户报告错误-模型不记   [PASS] 正确没调 record_milestone（规则层已在命令失败时记录）
用例: 用户改需求             [PASS] record_milestone(decision)
用例: 任务完成               [PASS] record_milestone(done)
用例: 普通提问不记录         [PASS] 正确没调 record_milestone
```

验证了 `Literal` 枚举约束确实生效：模型面对"报错了"这类描述时，因 schema 里没有 `error` 选项，没有强行凑一个不存在的类型，而是正确地不调用。

## 与 007 号实验结论的对应

007 号实验提出的三个后续方向里，选择了第三个：

> 3. 或混合方案：LLM 工具调用 + 规则兜底（LLM 漏记的，规则补记）

且进一步用 `Literal` 类型把方案边界固化到 schema 层，而不是仅靠 prompt 文字约定，避免"规则记了一遍、模型又记一遍"的重复记录问题。

## 后续可继续优化的点

- `decision` 目前完全靠模型判断，若后续发现召回仍不稳定，可以补充关键词预筛（如检测"不对/改/换"触发规则层提示，但最终分类仍由模型确认）作为辅助信号，而非替代
- `_auto_record_milestone` 目前用 try/except 兜底，规则层写入失败只记 warning 日志，不阻塞主流程——需关注这类静默失败是否需要额外可观测性
