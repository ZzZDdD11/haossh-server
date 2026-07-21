"""关键事件保护器：标记含错误的工具结果，防止被 TokenBudgetTrimmer 裁掉。

策略：
1. 扫描历史轮的 ToolReturnPart
2. 含错误关键词的 content 前加 "[!KEEP] " 标记
3. TokenBudgetTrimmer 裁剪时检测标记，即使超预算也保留
4. 最多保留 N 条重要消息，防止过多

为什么用文本标记：
- 处理器间天然解耦，不需要共享状态
- pydantic 模型可能 frozen，不能加属性
- 标记是 content 的一部分，序列化/反序列化不会丢
"""

import logging

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

from haossh.agent.context.base import HistoryProcessor
from haossh.agent.context.deduplicator import _find_current_turn_start

logger = logging.getLogger(__name__)

# 错误关键词（与 Compactor 一致）
_ERROR_KEYWORDS = [
    "error", "failed", "exception", "refused", "denied",
    "timeout", "no such", "permission", "fatal", "panic",
    "traceback", "cannot", "could not", "unable to",
]

# 标记前缀
_KEEP_MARKER = "[!KEEP] "

# 最多保留多少条重要消息
MAX_KEEP = 5


def _is_error_content(content: str) -> bool:
    """判断 content 是否含错误信息。"""
    if not isinstance(content, str):
        return False
    lower = content.lower()
    return any(kw in lower for kw in _ERROR_KEYWORDS)


def has_keep_marker(msg: ModelMessage) -> bool:
    """检查消息是否含 [!KEEP] 标记（供 TokenBudgetTrimmer 调用）。"""
    if isinstance(msg, ModelRequest):
        for part in msg.parts:
            if getattr(part, "part_kind", None) == "tool-return":
                content = getattr(part, "content", "")
                if isinstance(content, str) and content.startswith(_KEEP_MARKER):
                    return True
    return False


class PriorityProtector(HistoryProcessor):
    """关键事件保护器：标记含错误的工具结果，防止被裁掉。"""

    name = "priority_protector"

    def __init__(self, max_keep: int = MAX_KEEP):
        """
        Args:
            max_keep: 最多标记多少条重要消息，防止过多挤占预算
        """
        self.max_keep = max_keep

    async def process(self, history: list[ModelMessage]) -> list[ModelMessage]:
        if len(history) <= 1:
            return history

        # 1. 找当前轮分界线
        current_turn_start = _find_current_turn_start(history)

        # 2. 从后往前扫描历史轮，标记含错误的 tool_return
        #    从后往前是为了优先保留最近的错误（max_keep 限制下丢最旧的）
        marked_count = 0
        for i in range(current_turn_start - 1, -1, -1):
            if marked_count >= self.max_keep:
                break

            msg = history[i]
            if not isinstance(msg, ModelRequest):
                continue

            for j, part in enumerate(msg.parts):
                if marked_count >= self.max_keep:
                    break
                if getattr(part, "part_kind", None) != "tool-return":
                    continue

                content = getattr(part, "content", "")
                if not isinstance(content, str):
                    continue
                # 已经标记过的不重复标记
                if content.startswith(_KEEP_MARKER):
                    marked_count += 1
                    continue
                # 含错误才标记
                if _is_error_content(content):
                    msg.parts[j] = ToolReturnPart(
                        tool_name=part.tool_name,
                        content=_KEEP_MARKER + content,
                        tool_call_id=part.tool_call_id,
                    )
                    marked_count += 1

        if marked_count:
            logger.info("标记了 %d 条重要消息（含错误）", marked_count)

        return history
