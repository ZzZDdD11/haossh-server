"""按 token 预算裁剪器：从最新消息往前累加 token，达到预算就停。

比按条数裁剪更精准——同样 100 条消息，短消息可能才 2000 token（浪费空间），
长输出可能 50000 token（超模型限制）。按 token 裁剪确保上下文大小可控。
"""

import logging

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelResponse

from haossh.agent.context.base import HistoryProcessor
from haossh.agent.context.priority_protector import has_keep_marker

logger = logging.getLogger(__name__)

# 复用 TypeAdapter 序列化消息（与 repo_conversation 一致）
_message_adapter = TypeAdapter(ModelMessage)


def _estimate_tokens(msg: ModelMessage) -> int:
    """粗略估算消息的 token 数。

    英文约 4 字符 = 1 token，中文约 2 字符 = 1 token。
    取折中值 3 字符 = 1 token，用序列化后的 JSON 长度估算。
    """
    text = _message_adapter.dump_json(msg).decode("utf-8")
    return max(1, len(text) // 3)


class TokenBudgetTrimmer(HistoryProcessor):
    """按 token 预算裁剪：从最新消息往前累加，达到预算就停。

    对齐 ModelResponse 边界，保证工具配对完整：
    [ModelResponse(tool-call), ModelRequest(tool-return)] 不会被切断。
    """

    name = "token_trimmer"

    def __init__(self, token_budget: int = 12000):
        """
        Args:
            token_budget: 历史消息的 token 预算。
                          deepseek 64K 窗口，扣除 system prompt + 输出空间，历史留 12K。
        """
        self.token_budget = token_budget

    async def process(self, history: list[ModelMessage]) -> list[ModelMessage]:
        if not history:
            return history

        # 从后往前累加 token
        result: list[ModelMessage] = []
        used = 0
        kept_important = 0
        max_important_override = 5  # 最多超预算保留5条重要消息

        for i in range(len(history) - 1, -1, -1):
            msg = history[i]
            msg_tokens = _estimate_tokens(msg)
            is_important = has_keep_marker(msg)

            if used + msg_tokens > self.token_budget:
                if is_important and kept_important < max_important_override:
                    # 重要消息，超预算也保留
                    kept_important += 1
                    result.insert(0, msg)
                    used += msg_tokens
                # else: 非重要且超预算，跳过继续往前找重要的
            else:
                result.insert(0, msg)
                used += msg_tokens

        # 对齐：如果结果第一条是 ModelRequest（可能孤立 tool-return），
        # 往后跳过直到第一个 ModelResponse
        # 但 [!KEEP] 标记的重要消息不 pop
        while result and not isinstance(result[0], ModelResponse):
            if has_keep_marker(result[0]):
                break  # 重要消息保留，即使孤立
            result.pop(0)

        if len(result) < len(history):
            logger.info(
                "token 裁剪: %d -> %d 条, ~%d/%d token",
                len(history), len(result), used, self.token_budget,
            )

        return result if result else history  # 至少保留全部（极端情况）
