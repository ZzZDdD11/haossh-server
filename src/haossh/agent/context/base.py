"""上下文处理器基类与管道。

设计：
- HistoryProcessor 是抽象基类，定义 process(history) -> history 接口
- 每个子类实现一种策略（裁剪/压缩/去重/摘要）
- HistoryPipeline 按顺序串联多个处理器，统一打日志
- 对外暴露一个 trim_history 函数给 ProcessHistory 使用
"""

import logging
from abc import ABC, abstractmethod

from pydantic_ai.messages import ModelMessage

logger = logging.getLogger(__name__)


class HistoryProcessor(ABC):
    """上下文处理器基类。

    所有处理器输入消息历史列表，输出处理后的列表。
    子类实现具体策略（压缩/裁剪/去重/摘要）。
    """

    name: str = "base"

    @abstractmethod
    async def process(self, history: list[ModelMessage]) -> list[ModelMessage]:
        """处理消息历史，返回处理后的列表。"""
        ...


class HistoryPipeline:
    """处理器管道：按顺序依次执行多个处理器。

    每个处理器执行后打印日志，方便排查。
    """

    def __init__(self, processors: list[HistoryProcessor]):
        self.processors = processors

    async def run(self, history: list[ModelMessage]) -> list[ModelMessage]:
        for p in self.processors:
            before = len(history)
            history = await p.process(history)
            logger.info("[%s] %d -> %d 条", p.name, before, len(history))
        return history
