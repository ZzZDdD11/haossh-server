"""上下文管理管道。

通过 ProcessHistory capability 注入 Agent，框架在传 LLM 前自动调用。
管道按顺序执行多个处理器。

当前管道：
  Trimmer（条数裁剪）

后续将加入：
  Compactor（工具输出压缩）→ P0
  Deduplicator（重复命令去重）
  Summarizer（早期对话摘要）
  PriorityProtector（关键事件保护）
"""

import logging

from pydantic_ai.messages import ModelMessage, ModelRequest

from haossh.agent.context.base import HistoryPipeline
from haossh.agent.context.compactor import Compactor
from haossh.agent.context.deduplicator import Deduplicator
from haossh.agent.context.priority_protector import PriorityProtector
from haossh.agent.context.summarizer import Summarizer
from haossh.agent.context.token_trimmer import TokenBudgetTrimmer

logger = logging.getLogger(__name__)

# 组装管道（按执行顺序排列）
# 压缩 → 去重 → 摘要 → 标记重要 → token裁剪（跳过标记的）
_pipeline = HistoryPipeline([
    Compactor(),
    Deduplicator(),
    Summarizer(),
    PriorityProtector(),
    TokenBudgetTrimmer(),
])


async def trim_history(history: list[ModelMessage]) -> list[ModelMessage]:
    """管道入口函数，传给 ProcessHistory。

    末尾做一次结尾对齐兜底：pydantic-ai 要求处理后的历史必须以 ModelRequest 结尾
    （否则报 "Processed history must end with a `ModelRequest`"）。任何处理器都可能
    意外破坏这个约束，这里统一兜底——去掉末尾的非 ModelRequest 消息，直到以 ModelRequest 结尾。
    """
    result = await _pipeline.run(history)

    # 结尾对齐：从末尾去掉非 ModelRequest 的消息（孤立的 ModelResponse 会破坏约束）
    trimmed_tail = 0
    while result and not isinstance(result[-1], ModelRequest):
        result.pop()
        trimmed_tail += 1
    if trimmed_tail:
        logger.warning("结尾对齐：去掉了 %d 条末尾非 ModelRequest 消息", trimmed_tail)

    # 极端兜底：如果全被去光了（不该发生），返回原始 history 的最后一条 ModelRequest
    if not result:
        for msg in reversed(history):
            if isinstance(msg, ModelRequest):
                logger.warning("结尾对齐后为空，回退到原始 history 的最后一条 ModelRequest")
                return [msg]
        return history  # 连一条 ModelRequest 都没有，原样返回让框架报错

    return result
