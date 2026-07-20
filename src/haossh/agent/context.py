"""对话上下文管理。

通过 ProcessHistory capability 注入 Agent，框架在传 LLM 前自动调用。
不用在 chat.py 手动裁剪——时机由框架保证，不会切断工具配对。
"""

import logging

from pydantic_ai.messages import ModelMessage, ModelRequest

logger = logging.getLogger(__name__)

# 保留最近 N 条消息（约 10 轮对话，每轮约 4 条：user/agent+toolcall/toolresult/final）
MAX_HISTORY_MESSAGES = 40


def trim_history(history: list[ModelMessage]) -> list[ModelMessage]:
    """滑动窗口裁剪：只保留最近 N 条，对齐到用户消息边界。

    为什么对齐用户消息边界：不能切断"工具调用→工具返回"配对，
    否则 LLM 会看到孤立的工具结果（没有对应调用）而困惑。
    每轮对话从用户消息开始，从用户消息边界切最安全。
    """
    if len(history) <= MAX_HISTORY_MESSAGES:
        return history

    trimmed = history[-MAX_HISTORY_MESSAGES:]
    # 找第一个含 UserPromptPart 的 ModelRequest 作为起点
    for i, msg in enumerate(trimmed):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if getattr(part, "part_kind", None) == "user-prompt":
                    logger.debug(
                        "裁剪历史: %d -> %d 条", len(history), len(trimmed) - i
                    )
                    return trimmed[i:]
    return trimmed
