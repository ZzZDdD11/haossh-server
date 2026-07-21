"""滑动窗口裁剪器：按条数裁剪，对齐 ModelResponse 边界。"""

from pydantic_ai.messages import ModelMessage, ModelResponse

from haossh.agent.context.base import HistoryProcessor

# 保留最近 N 条消息。工具调用多的场景每轮用户对话可能产生 10+ 条
# （user → thinking → tool_call → tool_return → ... → final_text），N 需要够大
MAX_HISTORY_MESSAGES = 100


class Trimmer(HistoryProcessor):
    """滑动窗口裁剪：只保留最近 N 条，对齐到 ModelResponse 边界。

    为什么对齐 ModelResponse 而非 user-prompt：
    工具配对是 [ModelResponse(tool-call), ModelRequest(tool-return)]。
    从 ModelResponse 开始可保证配对完整，不会出现孤立的 tool-return。
    对齐 user-prompt 太激进——工具调用多时会把大部分历史裁掉。
    """

    name = "trimmer"

    def __init__(self, max_messages: int = MAX_HISTORY_MESSAGES):
        self.max_messages = max_messages

    async def process(self, history: list[ModelMessage]) -> list[ModelMessage]:
        if len(history) <= self.max_messages:
            return history

        trimmed = history[-self.max_messages:]
        # 找第一个 ModelResponse 作为起点（保证 tool-call → tool-return 配对完整）
        for i, msg in enumerate(trimmed):
            if isinstance(msg, ModelResponse):
                return trimmed[i:]
        return trimmed
