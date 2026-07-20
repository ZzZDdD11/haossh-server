"""对话上下文管理。

通过 ProcessHistory capability 注入 Agent，框架在传 LLM 前自动调用。
不用在 chat.py 手动裁剪——时机由框架保证，不会切断工具配对。
"""

import logging

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)

# 保留最近 N 条消息。工具调用多的场景每轮用户对话可能产生 10+ 条
# （user → thinking → tool_call → tool_return → ... → final_text），N 需要够大
MAX_HISTORY_MESSAGES = 100


def trim_history(history: list[ModelMessage]) -> list[ModelMessage]:
    """滑动窗口裁剪：只保留最近 N 条，对齐到 ModelResponse 边界。

    为什么对齐 ModelResponse 而非 user-prompt：
    工具配对是 [ModelResponse(tool-call), ModelRequest(tool-return)]。
    从 ModelResponse 开始可保证配对完整，不会出现孤立的 tool-return。
    对齐 user-prompt 太激进——工具调用多时会把大部分历史裁掉。
    """
    logger.info("trim_history 输入: %d 条", len(history))
    if len(history) <= MAX_HISTORY_MESSAGES:
        logger.info("trim_history 无需裁剪，返回 %d 条", len(history))
        return history

    trimmed = history[-MAX_HISTORY_MESSAGES:]
    # 找第一个 ModelResponse 作为起点（保证 tool-call → tool-return 配对完整）
    for i, msg in enumerate(trimmed):
        if isinstance(msg, ModelResponse):
            logger.info("trim_history 裁剪: %d -> %d 条", len(history), len(trimmed) - i)
            return trimmed[i:]
    logger.info("trim_history 裁剪(未找到ModelResponse边界): %d -> %d 条", len(history), len(trimmed))
    return trimmed
