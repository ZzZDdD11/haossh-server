# Pi Agent 框架上下文管理源码解读

> 源码位置：`/Users/hazezhang/code/pi`
> 整理时间：2026-07-21
> 关注包：`packages/agent`（agent-core 运行时）、`packages/coding-agent`（会话与压缩实现）

---

## 目录

1. [全貌：上下文流水线的三层架构](#一全貌上下文流水线的三层架构)
2. [持久层：SessionManager 与 JSONL 树](#二持久层sessionmanager-与-jsonl-树)
3. [会话层：从 leaf 回溯构建上下文](#三会话层从-leaf-回溯构建上下文)
4. [运行时层：transformContext → convertToLlm → LLM](#四运行时层transformcontext--converttollm--llm)
5. [Compaction：自动压缩的完整算法](#五compaction自动压缩的完整算法)
6. [Branch Summarization：分支切换的上下文继承](#六branch-summarization分支切换的上下文继承)
7. [Token 估算：双层精度策略](#七token-估算双层精度策略)
8. [Overflow Recovery：上下文溢出的应急处理](#八overflow-recovery上下文溢出的应急处理)
9. [Extension 钩子：让外部接管压缩逻辑](#九extension-钩子让外部接管压缩逻辑)
10. [设计哲学提炼](#十设计哲学提炼)

---

## 一、全貌：上下文流水线的三层架构

Pi 把上下文管理切成了三层正交的职责，每层只关心自己的问题：

```
┌──────────────────────────────────────────────────────────────────────┐
│  持久层 (SessionManager + JSONL)                                      │
│  - 树形存储：append-only, id/parentId                                 │
│  - 文件：~/.pi/agent/sessions/--<cwd>--/<ts>_<uuid>.jsonl             │
│  - 解决：会话怎么存？怎么分支？怎么恢复？                              │
├────────────────────────────────────────────────────────────────────────┤
│  会话层 (buildContextEntries / buildSessionContext)                   │
│  - 从当前 leaf 回溯到 root                                           │
│  - 应用 compaction：跳过被总结的旧消息，插入 CompactionEntry          │
│  - 解决：当前这条分支"有效"的上下文是什么？                            │
├────────────────────────────────────────────────────────────────────────┤
│  运行时层 (agent loop)                                                │
│  - AgentMessage[] → transformContext → convertToLlm → Message[]       │
│  - 解决：哪些消息能进 LLM？怎么裁剪/转换？                            │
└──────────────────────────────────────────────────────────────────────┘
```

**关键文件一览**：

| 文件 | 职责 |
|---|---|
| `packages/coding-agent/src/core/session-manager.ts` | 持久层 + 会话层主体 |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 自动压缩算法 |
| `packages/coding-agent/src/core/compaction/branch-summarization.ts` | 分支摘要 |
| `packages/coding-agent/src/core/compaction/utils.ts` | 序列化、文件跟踪 |
| `packages/agent/src/agent-loop.ts` | 运行时层（transformContext 调用点） |
| `packages/agent/src/types.ts` | AgentMessage、AgentContext 类型定义 |
| `packages/coding-agent/src/core/agent-session.ts` | 把三者串起来的"会话运行时" |

---

## 二、持久层：SessionManager 与 JSONL 树

### 2.1 存储格式：单文件 JSONL + 树

会话文件是 JSONL，每行一个 entry。每个 entry（除 header）都有 `id` 和 `parentId`：

```typescript
// packages/coding-agent/src/core/session-manager.ts:177
interface SessionEntryBase {
  type: string;
  id: string;           // 8-char hex ID
  parentId: string | null;  // null 表示根
  timestamp: string;    // ISO timestamp
}
```

**为什么是树而不是线性数组？**

这是 Pi 最关键的设计决策之一：**分支不需要新建文件**。

```
[user] ─ [assistant] ─ [user] ─ [assistant] ─┬─ [user]   ← 当前 leaf
                                            │
                                            └─ [branch_summary] ─ [user]  ← 备选分支
```

切换分支只是移动 `leafId` 指针，所有历史 entry 永不修改。SessionManager 的注释把这个原则说得很清楚：

```typescript
// packages/coding-agent/src/core/session-manager.ts:1296
/**
 * Get all session entries (excludes header). Returns a shallow copy.
 * The session is append-only: use appendXXX() to add entries, branch() to
 * change the leaf pointer. Entries cannot be modified or deleted.
 */
```

**Append-only + 移动 leaf 指针 = 零拷贝分支。** 这是 Pi 实现"探索式编程"（`/tree` 随时回到任意点重试）的成本极低的原因。

### 2.2 Entry 类型全景

```typescript
// packages/coding-agent/src/core/session-manager.ts:144
type SessionEntry =
  | SessionMessageEntry       // 普通消息（包裹 AgentMessage）
  | ThinkingLevelChangeEntry  // 思考等级切换
  | ModelChangeEntry          // 模型切换
  | CompactionEntry           // 压缩点 ⭐
  | BranchSummaryEntry        // 分支摘要 ⭐
  | CustomEntry               // 扩展私有状态（不进 LLM 上下文）
  | CustomMessageEntry        // 扩展注入消息（进 LLM 上下文）
  | LabelEntry                // 用户书签
  | SessionInfoEntry;         // 会话元数据（如显示名）
```

**关键区分：`CustomEntry` vs `CustomMessageEntry`**

```typescript
// packages/coding-agent/src/core/session-manager.ts:99, 123
/**
 * CustomEntry: Does NOT participate in LLM context.
 * 扩展用它存自己的私有状态（如 artifact index），reload 时扫描重建。
 */
interface CustomEntry<T = unknown> { type: "custom"; customType: string; data?: T; }

/**
 * CustomMessageEntry: DOES participate in LLM context.
 * 扩展用它往 LLM 上下文里注入消息，content 会被转成 user message。
 */
interface CustomMessageEntry<T = unknown> {
  type: "custom_message"; customType: string;
  content: string | (TextContent | ImageContent)[];
  details?: T;   // 注意：details 不进 LLM
  display: boolean;
}
```

这个区分体现了 Pi 的"边界清晰"原则：**扩展状态和扩展消息是两件不同的事，必须用不同的 entry 类型**。

### 2.3 版本迁移

```typescript
// v1 → v2: 线性 → 树（补 id/parentId）
// v2 → v3: hookMessage role → custom role（扩展统一）
```

SessionManager 加载时自动迁移，老会话透明升级。这把"历史兼容"的成本收敛在加载层，不让它污染业务逻辑。

---

## 三、会话层：从 leaf 回溯构建上下文

这是上下文管理的核心。`buildContextEntries()` 回答一个问题：**"当前这条分支，对 LLM 来说有效的是哪些 entry？"**

### 3.1 两步走：先回溯路径，再应用 compaction

```typescript
// packages/coding-agent/src/core/session-manager.ts:418
export function buildContextEntries(
  entries: SessionEntry[],
  leafId?: string | null,
  byId?: Map<string, SessionEntry>,
): SessionEntry[] {
  // 第一步：从 leaf 沿 parentId 走到 root，得到"路径"
  const path = buildSessionPath(entries, leafId, byId);
  
  // 第二步：找路径上最新的 compaction
  let compaction: CompactionEntry | null = null;
  for (const entry of path) {
    if (entry.type === "compaction") compaction = entry;
  }
  
  // 没有 compaction：路径即上下文
  if (!compaction) return path;
  
  // 有 compaction：裁剪掉被总结的旧消息
  const compactionIdx = path.findIndex(e => e.id === compaction.id);
  const contextEntries: SessionEntry[] = [compaction];  // compaction 本身保留
  let foundFirstKept = false;
  for (let i = 0; i < compactionIdx; i++) {
    if (path[i].id === compaction.firstKeptEntryId) foundFirstKept = true;
    if (foundFirstKept) contextEntries.push(path[i]);  // 保留 firstKeptEntryId 之后
  }
  contextEntries.push(...path.slice(compactionIdx + 1));  // 加上 compaction 之后的所有
  return contextEntries;
}
```

### 3.2 CompactionEntry 不是"删除"，是"标记"

**这是理解 Pi 上下文管理的钥匙**：压缩不是删除旧消息，而是追加一个 `CompactionEntry`，它带两个关键字段：

```typescript
// packages/coding-agent/src/core/session-manager.ts (CompactionEntry)
interface CompactionEntry<T = unknown> {
  type: "compaction";
  id: string;
  parentId: string;
  timestamp: number;
  summary: string;            // LLM 生成的结构化摘要
  firstKeptEntryId: string;   // 从哪个 entry 开始保留原文 ⭐
  tokensBefore: number;       // 压缩前的 token 数
  usage?: Usage;              // 生成摘要的 LLM 用量
  fromHook?: boolean;         // 是否扩展生成的（legacy 字段名）
  details?: T;                // 默认存 { readFiles, modifiedFiles }
}
```

`firstKeptEntryId` 的作用：`buildContextEntries()` 看到它后，**只保留这个 ID 之后的原文**，之前的全部跳过（被 summary 替代）。

```
原始路径: [u0] [a1] [t2] [u3] [a4] [t5] [u6] [a7] [t8]
                                      ↑
                          firstKeptEntryId = "u3"

compaction 后的 context:
[compaction_entry(summary)] [u3] [a4] [t5] [u6] [a7] [t8]
                            └── 保留原文 ────────────────┘
```

**核心洞察**：旧消息还在 JSONL 文件里，没被删除。它们只是不参与 LLM 上下文。这意味着：
- 压缩是**可逆的**（理论上可以撤销 compaction，重放原文）
- 压缩是**幂等的**（同一份 summary + firstKeptEntryId 反复加载结果一致）
- 压缩是**可审计的**（你能看到 tokensBefore、usage 等元数据）

### 3.3 buildSessionContext：把 entries 转成 LLM 看到的 messages

```typescript
// packages/coding-agent/src/core/session-manager.ts:461
export function buildSessionContext(
  entries: SessionEntry[],
  leafId?: string | null,
  byId?: Map<string, SessionEntry>,
): SessionContext {
  const path = buildSessionPath(entries, leafId, byId);
  const { thinkingLevel, model } = getSessionContextSettings(path);  // 从路径提取最新设置
  const messages = buildContextEntries(entries, leafId, byId)
    .flatMap(sessionEntryToContextMessages);  // 不同 entry 类型 → 不同 AgentMessage
  return { messages, thinkingLevel, model };
}
```

`sessionEntryToContextMessages` 是 entry → AgentMessage 的转换器：

```typescript
// packages/coding-agent/src/core/session-manager.ts:383
export function sessionEntryToContextMessages(entry: SessionEntry): AgentMessage[] {
  if (entry.type === "message") return [entry.message];
  if (entry.type === "custom_message")
    return [createCustomMessage(...)];
  if (entry.type === "branch_summary" && entry.summary)
    return [createBranchSummaryMessage(entry.summary, ...)];
  if (entry.type === "compaction")
    return [createCompactionSummaryMessage(entry.summary, entry.tokensBefore, ...)];
  return [];  // label, custom, model_change 等都不进上下文
}
```

注意：`compaction` entry 在这里被转成 `compactionSummary` 消息类型。LLM 看到的是一个"压缩摘要"消息，而不是知道有 compaction 这个概念。

---

## 四、运行时层：transformContext → convertToLlm → LLM

### 4.1 agent loop 的两步转换

```typescript
// packages/agent/src/agent-loop.ts:286
async function streamAssistantMessage(...) {
  // 第一步：可选的 context 变换（AgentMessage[] → AgentMessage[]）
  let messages = context.messages;
  if (config.transformContext) {
    messages = await config.transformContext(messages, signal);
  }
  
  // 第二步：转换为 LLM 兼容格式（AgentMessage[] → Message[]）
  const llmMessages = await config.convertToLlm(messages);
  
  // 第三步：构造 LLM context
  const llmContext: Context = {
    systemPrompt: context.systemPrompt,
    messages: llmMessages,
    tools: context.tools,
  };
  
  const response = await streamFunction(config.model, llmContext, ...);
  ...
}
```

### 4.2 AgentMessage vs LLM Message 的分离

```typescript
// packages/agent/src/types.ts:319
/**
 * AgentMessage: LLM 标准消息 + 自定义消息的并集。
 * 应用可通过 declaration merging 扩展。
 */
export type AgentMessage = Message | CustomAgentMessages[keyof CustomAgentMessages];
```

**为什么分离？** 因为 agent 内部状态可以包含很多 LLM 不需要的东西（UI 通知、artifact 引用、bash 执行记录），但 LLM 只认 `user`/`assistant`/`toolResult`。

`convertToLlm` 就是这个"过滤器"：

```typescript
convertToLlm: (messages: AgentMessage[]) => Message[] | Promise<Message[]>;
```

`transformContext` 则是"更早一步"的钩子，用于**在 convertToLlm 之前**做 AgentMessage 级别的变换（裁剪、注入外部上下文）。Pi 在这里留了一个口子，但 coding-agent 的实际压缩走的是另一条路（见下节）。

### 4.3 coding-agent 的上下文同步策略

**注意**：`pi-coding-agent` 没有用 `transformContext` 来做压缩。它的策略是：

```typescript
// packages/coding-agent/src/core/agent-session.ts:2136 (压缩完成后)
this.sessionManager.appendCompaction(summary, firstKeptEntryId, tokensBefore, ...);
const sessionContext = this.sessionManager.buildSessionContext();  // 重建上下文
this.agent.state.messages = sessionContext.messages;               // 直接替换 agent 状态
```

也就是说：**compaction 是显式的、写入 session 文件的操作**。压缩完成后，重新 `buildSessionContext` 得到新的 messages 数组，整体替换 `agent.state.messages`。

这种选择背后的逻辑：**compaction 是一个有副作用的、需要持久化的、需要用户可见（事件 + 事件流）的操作**，不应该藏在 `transformContext` 这种每轮都跑的钩子里。

`transformContext` 留给更轻量的、瞬时的变换（比如临时注入外部上下文）。

---

## 五、Compaction：自动压缩的完整算法

`packages/coding-agent/src/core/compaction/compaction.ts` 是上下文管理的核心算法文件。

### 5.1 触发条件

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:235
export function shouldCompact(
  contextTokens: number,
  contextWindow: number,
  settings: CompactionSettings,
): boolean {
  if (!settings.enabled) return false;
  return contextTokens > contextWindow - settings.reserveTokens;
}
```

默认配置：

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:132
export const DEFAULT_COMPACTION_SETTINGS: CompactionSettings = {
  enabled: true,
  reserveTokens: 16384,    // 给 LLM 响应预留的 token
  keepRecentTokens: 20000, // 保留最近多少 token 不压缩
};
```

触发时机有三种（在 `agent-session.ts` 的 `_maybeCompact` 中）：
- **threshold**：contextTokens 超过 window - reserve
- **overflow**：LLM 返回 context overflow 错误
- **manual**：用户执行 `/compact`

### 5.2 找切点（cut point）

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:403
export function findCutPoint(
  entries: SessionEntry[],
  startIndex: number,
  endIndex: number,
  keepRecentTokens: number,
): CutPointResult {
  const cutPoints = findValidCutPoints(entries, startIndex, endIndex);
  
  // 从最新往回走，累计 token 直到达到 keepRecentTokens
  let accumulatedTokens = 0;
  let cutIndex = cutPoints[0];
  for (let i = endIndex - 1; i >= startIndex; i--) {
    accumulatedTokens += estimateTokens(...);
    if (accumulatedTokens >= keepRecentTokens) {
      // 找到第一个有效切点
      cutIndex = cutPoints.find(c => c >= i);
      break;
    }
  }
  
  // 判断是否是 split turn
  const startsTurn = isTurnStartEntry(entries[cutIndex]);
  const turnStartIndex = startsTurn ? -1 : findTurnStartIndex(...);
  return {
    firstKeptEntryIndex: cutIndex,
    turnStartIndex,
    isSplitTurn: !startsTurn && turnStartIndex !== -1,
  };
}
```

**切点规则**（关键约束）：

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:308
function isCutPointMessage(message: AgentMessage): boolean {
  switch (message.role) {
    case "user":        // ✅
    case "assistant":   // ✅
    case "bashExecution": // ✅
    case "custom":      // ✅
    case "branchSummary":     // ✅
    case "compactionSummary": // ✅
      return true;
    case "toolResult":  // ❌ 永远不能切在 toolResult 上
      return false;
  }
}
```

**为什么 toolResult 不能切？** 因为 toolResult 必须紧跟它的 toolCall。如果切点在 toolResult 上，它的 toolCall 就被丢进 summary 了，LLM 看到一个孤儿 toolResult 会困惑。

### 5.3 Split Turn：超大单轮的处理

如果一个 turn（user message + 后续 assistant/tool）本身就超过 `keepRecentTokens`，切点会落在 turn 中间的 assistant message 上。这就是 split turn。

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:820 (compact 函数内)
if (isSplitTurn && turnPrefixMessages.length > 0) {
  // 生成两个 summary：
  // 1. history summary: 之前的上下文（如果有）
  // 2. turn prefix summary: 这个超大 turn 的前半段
  let historyText = "No prior history.";
  if (messagesToSummarize.length > 0) {
    const historyResult = await generateSummaryWithUsage(
      messagesToSummarize, ..., previousSummary, ...  // 把旧 summary 喂进去
    );
    historyText = historyResult.text;
  }
  const turnPrefixResult = await generateTurnPrefixSummary(
    turnPrefixMessages, ..., TURN_PREFIX_SUMMARIZATION_PROMPT
  );
  // 合并成一个 summary
  summary = `${historyText}\n\n---\n\n**Turn Context (split turn):**\n\n${turnPrefixResult.text}`;
}
```

**设计巧妙之处**：split turn 不强求把整个 turn 都丢进 summary，而是把 turn 的前缀单独摘要，后缀（recent work）保留原文。这样最近的工具调用结果不会丢失精度。

### 5.4 迭代式摘要（重复 compaction）

第二次压缩时，第一次的 summary 不会丢，而是作为 `previousSummary` 喂给 LLM：

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:500 (UPDATE_SUMMARIZATION_PROMPT)
const UPDATE_SUMMARIZATION_PROMPT = `The messages above are NEW conversation messages 
to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
...`;
```

**关键边界**：summarize 的范围从上一次 compaction 的 `firstKeptEntryId` 开始，而不是从 compaction entry 本身开始。`prepareCompaction` 里有这段逻辑：

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:705
if (prevCompactionIndex >= 0) {
  const prevCompaction = pathEntries[prevCompactionIndex] as CompactionEntry;
  previousSummary = prevCompaction.summary;
  const firstKeptEntryIndex = pathEntries.findIndex(
    e => e.id === prevCompaction.firstKeptEntryId
  );
  // 从上次保留的边界开始，而不是 compaction 之后
  boundaryStart = firstKeptEntryIndex >= 0 ? firstKeptEntryIndex : prevCompactionIndex + 1;
}
```

这意味着**上次"幸存"的消息会再次进入这次摘要**，确保信息连续性。

### 5.5 结构化摘要格式

Pi 用固定的 markdown 结构，让摘要可被 LLM 稳定解析：

```markdown
## Goal
[用户在做什么]

## Constraints & Preferences
- [约束]

## Progress
### Done
- [x] [已完成]
### In Progress
- [ ] [进行中]
### Blocked
- [阻塞]

## Key Decisions
- **[决策]**: [理由]

## Next Steps
1. [下一步]

## Critical Context
- [关键信息]

<read-files>
path/to/file1.ts
</read-files>

<modified-files>
path/to/changed.ts
</modified-files>
```

### 5.6 序列化：防止 LLM 续写对话

摘要前，消息被序列化成纯文本：

```typescript
// packages/coding-agent/src/core/compaction/utils.ts:109
export function serializeConversation(messages: Message[]): string {
  const parts: string[] = [];
  for (const msg of messages) {
    if (msg.role === "user") {
      parts.push(`[User]: ${content}`);
    } else if (msg.role === "assistant") {
      if (thinkingParts.length > 0)
        parts.push(`[Assistant thinking]: ${thinkingParts.join("\n")}`);
      parts.push(`[Assistant]: ${contentText(msg.content)}`);
      parts.push(`[Assistant tool calls]: read(path="..."); bash(command="...")`);
    } else if (msg.role === "toolResult") {
      // ⭐ 工具结果截断到 2000 字符
      parts.push(`[Tool result]: ${truncateForSummary(content, 2000)}`);
    }
  }
  return parts.join("\n\n");
}
```

**为什么用 `[User]:` / `[Assistant]:` 前缀而不是用 message 数组？** 因为如果直接把 messages 喂给 LLM，它会以为这是要继续的对话。序列化成带标签的文本 + system prompt 明确说"不要继续对话，只输出摘要"，能有效阻止 LLM 续写。

```typescript
// packages/coding-agent/src/core/compaction/utils.ts:156
export const SUMMARIZATION_SYSTEM_PROMPT = `You are a context summarization assistant. 
Your task is to read a conversation between a user and an AI assistant, then produce 
a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. 
ONLY output the structured summary.`;
```

**工具结果截断**也很关键：`read` 和 `bash` 的输出可能极大，截断到 2000 字符能把摘要请求控制在合理 token 预算内。

### 5.7 累积式文件跟踪

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:42
function extractFileOperations(
  messages: AgentMessage[],
  entries: SessionEntry[],
  prevCompactionIndex: number,
): FileOperations {
  const fileOps = createFileOps();
  
  // 1. 从上一次 compaction 的 details 中继承
  if (prevCompactionIndex >= 0) {
    const prevCompaction = entries[prevCompactionIndex] as CompactionEntry;
    if (!prevCompaction.fromHook && prevCompaction.details) {
      const details = prevCompaction.details as CompactionDetails;
      for (const f of details.readFiles) fileOps.read.add(f);
      for (const f of details.modifiedFiles) fileOps.edited.add(f);
    }
  }
  
  // 2. 从本次要摘要的消息中提取
  for (const msg of messages) {
    extractFileOpsFromMessage(msg, fileOps);
  }
  return fileOps;
}
```

`extractFileOpsFromMessage` 从 assistant 消息的 toolCall 里提取 `read`/`write`/`edit` 调用的 `path` 参数。最终算出 `{ readFiles, modifiedFiles }`：

```typescript
// packages/coding-agent/src/core/compaction/utils.ts:62
export function computeFileLists(fileOps: FileOperations) {
  const modified = new Set([...fileOps.edited, ...fileOps.written]);
  const readOnly = [...fileOps.read].filter(f => !modified.has(f)).sort();
  const modifiedFiles = [...modified].sort();
  return { readFiles: readOnly, modifiedFiles };
}
```

注意去重逻辑：**只读列表会排除被修改过的文件**（避免一个文件同时出现在 read 和 modified 里）。这两个列表以 `<read-files>` / `<modified-files>` XML 标签附加到 summary 末尾，跨多次 compaction 累积。

---

## 六、Branch Summarization：分支切换的上下文继承

`/tree` 切换分支时，离开的分支可以被摘要，注入到新位置。这样切换分支不会丢失上下文。

### 6.1 算法

```
切换前:
       ┌─ B ─ C ─ D (旧 leaf, 被放弃)
A ─────┤
       └─ E ─ F (目标)

共同祖先: A
要摘要的: B, C, D

切换后:
       ┌─ B ─ C ─ D ─ [B,C,D 的 summary]
A ─────┤
       └─ E ─ F (新 leaf)
```

### 6.2 BranchSummaryEntry

```typescript
interface BranchSummaryEntry<T = unknown> {
  type: "branch_summary";
  id: string;
  parentId: string;
  timestamp: number;
  summary: string;
  fromId: string;   // 从哪个 entry 导航过来的
  usage?: Usage;
  details?: T;     // 同样是 { readFiles, modifiedFiles }
}
```

**和 CompactionEntry 的区别**：
- CompactionEntry 替换同分支的旧消息（`firstKeptEntryId`）
- BranchSummaryEntry 是跨分支注入的（`fromId` 指向被放弃的分支）

两者用同样的摘要格式、同样的文件跟踪逻辑、同样的 extension 钩子机制。

---

## 七、Token 估算：双层精度策略

Pi 不是简单地用 chars/4 估算所有 token，而是**分层**：

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:202
export function estimateContextTokens(messages: AgentMessage[]): ContextUsageEstimate {
  const usageInfo = getLastAssistantUsageInfo(messages);
  
  if (!usageInfo) {
    // 没有任何 assistant usage：全用 chars/4 估算
    let estimated = 0;
    for (const message of messages) estimated += estimateTokens(message);
    return { tokens: estimated, usageTokens: 0, trailingTokens: estimated, lastUsageIndex: null };
  }
  
  // 有 usage：用精确值 + chars/4 估算尾部
  const usageTokens = calculateContextTokens(usageInfo.usage);  // 精确
  let trailingTokens = 0;
  for (let i = usageInfo.index + 1; i < messages.length; i++) {
    trailingTokens += estimateTokens(messages[i]);  // 估算
  }
  return { tokens: usageTokens + trailingTokens, usageTokens, trailingTokens, ... };
}
```

**为什么这样？** 因为 LLM 返回的 `usage.totalTokens` 是**精确的**上下文 token 数（包含 system prompt、tools schema、所有消息）。但它只反映那次调用时的状态。之后的 user message 和 toolResult 没有精确值，只能估算。

所以 Pi 用"**最后一次 assistant usage 的精确值 + 之后消息的估算值**"组合。这比纯估算准，又不需要每次都调用 tokenizer。

### 7.1 跳过无效 usage

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:154
function getAssistantUsage(msg: AgentMessage): Usage | undefined {
  if (msg.role === "assistant" && "usage" in msg) {
    if (
      msg.stopReason !== "aborted" &&
      msg.stopReason !== "error" &&
      msg.usage &&
      calculateContextTokens(msg.usage) > 0
    ) {
      return msg.usage;
    }
  }
  return undefined;
}
```

aborted 和 error 的消息 usage 是无效的（可能是 0 或部分值），跳过它们避免误判。

### 7.2 估算函数（chars/4）

```typescript
// packages/coding-agent/src/core/compaction/compaction.ts:266
export function estimateTokens(message: AgentMessage): number {
  let chars = 0;
  switch (message.role) {
    case "user": chars = estimateTextAndImageContentChars(message.content); break;
    case "assistant":
      for (const block of assistant.content) {
        if (block.type === "text") chars += block.text.length;
        else if (block.type === "thinking") chars += block.thinking.length;
        else if (block.type === "toolCall")
          chars += block.name.length + JSON.stringify(block.arguments).length;
      }
      break;
    case "toolResult":
    case "custom":
      chars = estimateTextAndImageContentChars(message.content);
      break;
    case "bashExecution":
      chars = message.command.length + message.output.length;
      break;
    case "branchSummary":
    case "compactionSummary":
      chars = message.summary.length;
      break;
  }
  return Math.ceil(chars / 4);
}
```

图片用固定估算 `ESTIMATED_IMAGE_CHARS = 4800`（约 1200 token）。

注释里说"conservative (overestimates tokens)"——**宁可高估，提前触发压缩，也不要低估导致溢出**。这和 RFC 0031 的"可预测性优于功能"哲学一致。

---

## 八、Overflow Recovery：上下文溢出的应急处理

当 LLM 返回 context overflow 错误时，Pi 有专门的恢复流程：

```typescript
// packages/coding-agent/src/core/agent-session.ts:1964
if (sameModel && isContextOverflow(assistantMessage, contextWindow)) {
  const willRetry = assistantMessage.stopReason !== "stop";
  
  if (!willRetry) {
    // 响应已完成，不能 continue，只压缩不重试
    return await this._runAutoCompaction("overflow", false);
  }
  
  if (this._overflowRecoveryAttempted) {
    // 只重试一次，避免无限循环
    this._emit({
      type: "compaction_end",
      reason: "overflow",
      errorMessage: "Context overflow recovery failed after one compact-and-retry attempt. "
                 + "Try reducing context or switching to a larger-context model.",
    });
    return false;
  }
  
  this._overflowRecoveryAttempted = true;
  // 移除 error 消息（保留在 session 里用于历史，但不进上下文）
  const messages = this.agent.state.messages;
  if (messages.length > 0 && messages[messages.length - 1].role === "assistant") {
    this.agent.state.messages = messages.slice(0, -1);
  }
  // 压缩 + 重试
  return await this._runAutoCompaction("overflow", true);
}
```

**关键设计**：
1. **只重试一次**：`_overflowRecoveryAttempted` 标志位防止死循环
2. **区分 stopReason**：如果 LLM 已经 stop（响应完成），不能 `continue()`（最后一条是 assistant），只压缩
3. **移除 error 消息**：error 消息留在 session 文件用于审计，但不进下次的 LLM 上下文

### 8.1 防止陈旧 usage 触发压缩

```typescript
// packages/coding-agent/src/core/agent-session.ts:1953
const compactionEntry = getLatestCompactionEntry(this.sessionManager.getBranch());
const assistantIsFromBeforeCompaction =
  compactionEntry !== null &&
  assistantMessage.timestamp <= new Date(compactionEntry.timestamp).getTime();
if (assistantIsFromBeforeCompaction) {
  return false;  // 跳过压缩检查
}
```

如果一个 assistant 消息的时间戳早于最近的 compaction，说明它是压缩前的"陈旧"消息，不应该用它来触发新的压缩。这避免了压缩后第一条 prompt 又被旧 usage 误判为溢出。

### 8.2 跨模型切换的保护

```typescript
// packages/coding-agent/src/core/agent-session.ts:1947
const sameModel =
  this.model &&
  assistantMessage.provider === this.model.provider &&
  assistantMessage.model === this.model.id;
```

如果用户从 opus（小窗口）切到 codex（大窗口），opus 的 overflow 错误不应该触发 codex 的压缩。**每个模型的上下文窗口是独立的**。

---

## 九、Extension 钩子：让外部接管压缩逻辑

Pi 把 compaction 设计成可被扩展完全接管。两个核心事件：

### 9.1 `session_before_compact`

```typescript
pi.on("session_before_compact", async (event, ctx) => {
  const { preparation, branchEntries, customInstructions, reason, willRetry, signal } = event;
  
  // preparation 包含 pi 算好的所有数据：
  // - messagesToSummarize: 要摘要的消息
  // - turnPrefixMessages: split turn 前缀
  // - previousSummary: 上次 summary（用于迭代更新）
  // - fileOps: 提取好的文件操作
  // - tokensBefore: 压缩前 token
  // - firstKeptEntryId: 保留边界
  // - settings: 压缩配置
  
  // 取消压缩：
  return { cancel: true };
  
  // 用自己的模型生成 summary：
  const conversationText = serializeConversation(
    convertToLlm(preparation.messagesToSummarize)
  );
  const { summary, usage } = await myModel.summarize(conversationText);
  return {
    compaction: {
      summary,
      firstKeptEntryId: preparation.firstKeptEntryId,
      tokensBefore: preparation.tokensBefore,
      usage,  // 会被计入 session 总用量
      details: { /* 自定义数据 */ },
    }
  };
});
```

**设计亮点**：`preparation` 是 pi 已经算好的"半成品"，扩展可以选择：
- 直接用 pi 算的边界，只换 summary 生成方式（用自己的模型）
- 完全接管，返回自定义的 `firstKeptEntryId` 和 `details`
- 取消压缩

### 9.2 `session_before_tree`

类似机制，用于分支切换。`userWantsSummary` 表示用户是否选择摘要，扩展只在 `true` 时返回 summary 才有效。

### 9.3 `details` 字段的扩展空间

```typescript
interface CompactionEntry<T = unknown> {
  // ...
  details?: T;  // 实现特定的数据
}
```

默认实现存 `{ readFiles, modifiedFiles }`。扩展可以存任何 JSON 可序列化数据（如 ArtifactIndex、版本标记、结构化压缩的额外元数据）。`fromHook` 字段（legacy 名）标记是否扩展生成，pi 生成时会跳过从 `details` 继承文件列表。

---

## 十、设计哲学提炼

把源码读完后，Pi 上下文管理的设计哲学可以归结为七条：

### 1. Append-only，永不修改

JSONL 文件只追加。压缩、分支、模型切换都是追加新 entry，不删除旧的。这让所有操作都**可逆、可审计、可重放**。

### 2. Compaction 是标记，不是删除

`CompactionEntry` + `firstKeptEntryId` 的设计意味着压缩是一个"元数据操作"——告诉 `buildContextEntries` 哪些消息不进 LLM 上下文，但消息本身还在文件里。

### 3. 三层正交，各管一件事

- 持久层管"怎么存"
- 会话层管"当前分支的有效上下文是什么"
- 运行时层管"哪些消息进 LLM、怎么转换"

每层都不越界。压缩算法在会话层做决策，运行时层只看到最终的 messages 数组。

### 4. 切点规则保正确性

永不切在 `toolResult` 上（必须跟着 `toolCall`）。这是**正确性约束**，不是性能优化。Pi 宁可少切几个位置，也不要产生 LLM 看不懂的孤儿消息。

### 5. Split Turn 兼顾精度与截断

超大单轮不强制整体摘要，而是把前缀单独摘要、后缀保留原文。最近的工具调用结果保持精度，旧的部分被压缩。这是"**就近精度优先**"的设计。

### 6. Token 估算分层精度

有 LLM usage 用精确值，没有用 chars/4 估算。估算倾向高估（conservative），宁可提前压缩也不要溢出。这和"可预测性优于功能"一脉相承。

### 7. 扩展接管一切，但默认实现够用

`session_before_compact` / `session_before_tree` 让扩展完全接管压缩逻辑，甚至可以换模型。但默认实现已经覆盖了文件跟踪、迭代摘要、split turn 等所有复杂场景。**核心提供够用的默认，扩展机制留够口子**——这正是 Pi "核心极小"哲学的体现。

---

## 附：上下文管理的数据流全景

```
用户输入 prompt
    │
    ▼
agent-session.prompt()
    │
    ├─ sessionManager.appendMessage(userMessage)   ← 持久化
    │
    ├─ sessionManager.buildSessionContext()         ← 会话层
    │   │
    │   ├─ buildSessionPath(leaf → root)            ← 回溯路径
    │   ├─ 找最新 CompactionEntry
    │   ├─ buildContextEntries()                    ← 应用 compaction
    │   │   ├─ [compaction_entry]
    │   │   ├─ [firstKeptEntryId ... compaction)    ← 保留的原文
    │   │   └─ [compaction ... leaf]                ← 压缩后的新消息
    │   └─ flatMap(sessionEntryToContextMessages)   ← entry → AgentMessage
    │
    ├─ agent.state.messages = sessionContext.messages  ← 同步到运行时
    │
    └─ agent.prompt() / agent.continue()             ← 运行时层
        │
        ├─ [transformContext] (可选, AgentMessage → AgentMessage)
        ├─ convertToLlm (AgentMessage → Message)
        ├─ streamFunction(model, context)           ← 调用 LLM
        │
        ▼
    LLM 响应 + 工具调用
        │
        ├─ assistant message + toolResult messages 追加到 session
        │
        ├─ _maybeCompact(assistantMessage)          ← 检查是否需要压缩
        │   │
        │   ├─ shouldCompact(contextTokens, window, settings)
        │   │   └─ true → _runAutoCompaction("threshold")
        │   │       │
        │   │       ├─ emit session_before_compact (extension hook)
        │   │       ├─ prepareCompaction(pathEntries, settings)
        │   │       │   ├─ findCutPoint (累计 token, 找切点)
        │   │       │   ├─ extractFileOperations (累积文件跟踪)
        │   │       │   └─ previousSummary (迭代更新)
        │   │       ├─ compact(preparation, model, ...)
        │   │       │   ├─ generateSummaryWithUsage (history)
        │   │       │   ├─ generateTurnPrefixSummary (split turn, 如有)
        │   │       │   ├─ serializeConversation (防续写)
        │   │       │   └─ computeFileLists + formatFileOperations
        │   │       ├─ sessionManager.appendCompaction(...)  ← 追加 CompactionEntry
        │   │       ├─ sessionManager.buildSessionContext()  ← 重建上下文
        │   │       ├─ agent.state.messages = new messages   ← 同步
        │   │       └─ emit session_compact (extension hook)
        │   │
        │   └─ false → 继续
        │
        └─ 下一轮 turn (如有 tool calls) 或结束
```

## 附：关键源码索引

| 文件 | 行 | 内容 |
|---|---|---|
| `packages/agent/src/types.ts` | 319 | `AgentMessage` 联合类型定义 |
| `packages/agent/src/types.ts` | 144 | `AgentLoopConfig`（含 `transformContext`、`convertToLlm`） |
| `packages/agent/src/agent-loop.ts` | 286 | transformContext → convertToLlm 调用点 |
| `packages/coding-agent/src/core/session-manager.ts` | 144 | `SessionEntry` 联合类型 |
| `packages/coding-agent/src/core/session-manager.ts` | 383 | `sessionEntryToContextMessages` |
| `packages/coding-agent/src/core/session-manager.ts` | 418 | `buildContextEntries`（应用 compaction） |
| `packages/coding-agent/src/core/session-manager.ts` | 461 | `buildSessionContext`（组装最终上下文） |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 132 | 默认压缩配置 |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 202 | `estimateContextTokens`（双层精度） |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 235 | `shouldCompact` 触发条件 |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 308 | `isCutPointMessage`（切点规则） |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 403 | `findCutPoint`（找切点算法） |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 467 | `SUMMARIZATION_PROMPT`（结构化摘要格式） |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 500 | `UPDATE_SUMMARIZATION_PROMPT`（迭代更新） |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 687 | `prepareCompaction`（准备压缩数据） |
| `packages/coding-agent/src/core/compaction/compaction.ts` | 794 | `compact`（主压缩函数） |
| `packages/coding-agent/src/core/compaction/utils.ts` | 29 | `extractFileOpsFromMessage`（文件跟踪） |
| `packages/coding-agent/src/core/compaction/utils.ts` | 109 | `serializeConversation`（防续写序列化） |
| `packages/coding-agent/src/core/agent-session.ts` | 1938 | `_maybeCompact`（触发判定） |
| `packages/coding-agent/src/core/agent-session.ts` | 2028 | `_runAutoCompaction`（执行压缩） |
