"""工具输出压缩器：压缩历史消息中过长的工具返回结果。

策略：
1. 以最后一个 user-prompt 为分界线，之前的是历史轮，之后的是当前轮
2. 只压缩历史轮的 ToolReturnPart，当前轮保持完整
3. 日志分级：保留首尾行 + 错误行，省略进度行，标注省略行数
4. 短输出（< min_length）不压缩
"""

import logging
import re

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

from haossh.agent.context.base import HistoryProcessor

logger = logging.getLogger(__name__)

# 进度行关键词（小写匹配）：这些行对 LLM 决策无价值
_PROGRESS_KEYWORDS = [
    "downloading", "installing", "building", "resolved",
    "preparing", "cloning", "collecting", "using cached",
    "creating virtual", "unpacking", "setting up", "processing",
]

# 错误行关键词（小写匹配）：这些行必须保留
_ERROR_KEYWORDS = [
    "error", "failed", "exception", "refused", "denied",
    "timeout", "no such", "permission", "fatal", "panic",
    "traceback", "cannot", "could not", "unable to",
]

# 编译正则提升性能
_PROGRESS_RE = re.compile("|".join(re.escape(kw) for kw in _PROGRESS_KEYWORDS), re.IGNORECASE)
_ERROR_RE = re.compile("|".join(re.escape(kw) for kw in _ERROR_KEYWORDS), re.IGNORECASE)


def _is_progress_line(line: str) -> bool:
    """判断是否为进度行（可省略）。"""
    stripped = line.strip().lower()
    if not stripped:
        return False  # 空行不当进度行
    return bool(_PROGRESS_RE.search(stripped))


def _is_error_line(line: str) -> bool:
    """判断是否为错误行（必须保留）。"""
    stripped = line.strip().lower()
    if not stripped:
        return False
    return bool(_ERROR_RE.search(stripped))


def _compact_content(content: str, head: int = 3, tail: int = 3) -> str:
    """压缩工具输出：保留首尾行 + 错误行，省略进度行，标注省略数。

    Args:
        content: 原始工具输出文本
        head: 保留前几行
        tail: 保留后几行
    """
    lines = content.split("\n")

    # 分类
    error_lines = []      # 错误行（无论位置，全部保留）
    skipped_count = 0     # 被省略的进度行数
    kept_indices = set()  # 保留的行索引

    # 首尾行始终保留
    for i in list(range(min(head, len(lines)))) + list(range(max(0, len(lines) - tail), len(lines))):
        kept_indices.add(i)

    # 中间行：错误行保留，进度行省略，其他行保留
    for i, line in enumerate(lines):
        if i in kept_indices:
            continue
        if _is_error_line(line):
            kept_indices.add(i)
        elif _is_progress_line(line):
            skipped_count += 1
        else:
            kept_indices.add(i)  # 非进度非错误的行也保留

    # 如果没有省略任何行，不需要压缩
    if skipped_count == 0:
        return content

    # 重新组装
    result = []
    prev_index = -2  # 用于检测连续性
    for i in sorted(kept_indices):
        if i != prev_index + 1:
            # 不连续，插入省略标记
            if result and not result[-1].startswith("...省略"):
                result.append(f"...省略 {skipped_count} 行进度信息...")
        result.append(lines[i])
        prev_index = i

    # 如果省略标记还没加（所有保留行都在末尾）
    if skipped_count > 0 and not any("省略" in r for r in result):
        result.insert(0, f"...省略 {skipped_count} 行进度信息...")

    return "\n".join(result)


class Compactor(HistoryProcessor):
    """工具输出压缩器。

    压缩历史轮中的 ToolReturnPart，当前轮保持完整。
    以最后一个 user-prompt 为分界线判断当前轮 vs 历史轮。
    """

    name = "compactor"

    def __init__(
        self,
        min_length: int = 500,
        head_lines: int = 3,
        tail_lines: int = 3,
        fallback_keep_recent: int = 6,
    ):
        """
        Args:
            min_length: 工具输出超过此长度才压缩（字符数）
            head_lines: 保留前几行
            tail_lines: 保留后几行
            fallback_keep_recent: 找不到 user-prompt 时，保留最后 N 条不压缩
        """
        self.min_length = min_length
        self.head_lines = head_lines
        self.tail_lines = tail_lines
        self.fallback_keep_recent = fallback_keep_recent

    async def process(self, history: list[ModelMessage]) -> list[ModelMessage]:
        if len(history) <= 1:
            return history

        # 1. 找最后一个 user-prompt 的位置（当前轮起点）
        current_turn_start = self._find_current_turn_start(history)

        # 2. 压缩 current_turn_start 之前的 ToolReturnPart
        compact_count = 0
        for i in range(current_turn_start):
            msg = history[i]
            if not isinstance(msg, ModelRequest):
                continue
            for j, part in enumerate(msg.parts):
                if getattr(part, "part_kind", None) != "tool-return":
                    continue
                content = part.content
                if not isinstance(content, str) or len(content) < self.min_length:
                    continue
                # 压缩
                compacted = _compact_content(content, self.head_lines, self.tail_lines)
                if compacted != content:
                    # 用新对象替换（pydantic 模型可能 frozen）
                    msg.parts[j] = ToolReturnPart(
                        tool_name=part.tool_name,
                        content=compacted,
                        tool_call_id=part.tool_call_id,
                    )
                    compact_count += 1

        if compact_count:
            logger.info("压缩了 %d 条工具输出", compact_count)

        return history

    def _find_current_turn_start(self, history: list[ModelMessage]) -> int:
        """找最后一个 user-prompt 的位置，找不到则用 fallback。"""
        for i in range(len(history) - 1, -1, -1):
            msg = history[i]
            if isinstance(msg, ModelRequest):
                for part in msg.parts:
                    if getattr(part, "part_kind", None) == "user-prompt":
                        return i
        # 找不到 user-prompt，fallback：保留最后 N 条不压缩
        return max(0, len(history) - self.fallback_keep_recent)
