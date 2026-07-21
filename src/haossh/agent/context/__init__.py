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

from pydantic_ai.messages import ModelMessage

from haossh.agent.context.base import HistoryPipeline
from haossh.agent.context.compactor import Compactor
from haossh.agent.context.deduplicator import Deduplicator
from haossh.agent.context.priority_protector import PriorityProtector
from haossh.agent.context.summarizer import Summarizer
from haossh.agent.context.token_trimmer import TokenBudgetTrimmer

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
    """管道入口函数，传给 ProcessHistory。"""
    return await _pipeline.run(history)
