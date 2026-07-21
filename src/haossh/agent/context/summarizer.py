"""早期对话摘要器：消息太多时，把最早的几条压缩成一条摘要。

策略：
1. 以最后一个 user-prompt 为分界线，只处理历史轮
2. 历史轮超过阈值时，把最早的 N 条压缩成摘要消息
3. 摘要用规则提取（不调 LLM）：用户消息 + 执行命令 + 结果状态
4. 摘要消息替换原始消息，减少条数

为什么用规则而非 LLM 摘要：
- 零成本、零延迟
- 运维场景的关键信息是结构化的（命令 + 退出码），规则提取够用
"""

import json
import logging

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart

from haossh.agent.context.base import HistoryProcessor
from haossh.agent.context.deduplicator import _find_current_turn_start

logger = logging.getLogger(__name__)


class Summarizer(HistoryProcessor):
    """早期对话摘要器：消息太多时压缩最早的几条。"""

    name = "summarizer"

    def __init__(
        self,
        min_history_to_summarize: int = 30,
        keep_recent: int = 15,
    ):
        """
        Args:
            min_history_to_summarize: 历史轮消息超过此数才触发摘要
            keep_recent: 保留最近 N 条不动，压缩更早的
        """
        self.min_history_to_summarize = min_history_to_summarize
        self.keep_recent = keep_recent

    async def process(self, history: list[ModelMessage]) -> list[ModelMessage]:
        if len(history) <= 1:
            return history

        # 1. 找当前轮分界线
        current_turn_start = _find_current_turn_start(history)

        # 2. 历史轮消息不够多，不摘要
        history_count = current_turn_start
        if history_count < self.min_history_to_summarize:
            return history

        # 3. 确定要压缩的范围：最早的 (history_count - keep_recent) 条
        summarize_count = history_count - self.keep_recent
        if summarize_count <= 0:
            return history

        old_msgs = history[:summarize_count]
        recent_msgs = history[summarize_count:]

        # 4. 生成摘要
        summary_text = self._build_summary(old_msgs)

        # 5. 用摘要消息替换原始消息
        summary_msg = ModelRequest(parts=[UserPromptPart(content=summary_text)])

        logger.info("摘要: %d 条 -> 1 条摘要 + %d 条近期", len(old_msgs), len(recent_msgs))
        return [summary_msg] + recent_msgs

    def _build_summary(self, msgs: list[ModelMessage]) -> str:
        """从消息列表提取关键信息，组装成摘要文本。"""
        lines = ["[早期对话摘要]"]

        for msg in msgs:
            if isinstance(msg, ModelRequest):
                for part in msg.parts:
                    pk = getattr(part, "part_kind", None)
                    if pk == "user-prompt":
                        content = getattr(part, "content", "")
                        if isinstance(content, str) and len(content) > 80:
                            content = content[:80] + "..."
                        lines.append(f"用户: {content}")
                    elif pk == "tool-return":
                        content = getattr(part, "content", "")
                        if isinstance(content, str):
                            # 提取退出码
                            if "[exit=0]" in content:
                                lines.append(f"  结果: 成功")
                            elif "[exit=" in content:
                                # 非零退出码，提取错误信息
                                lines.append(f"  结果: 失败")
                            elif "重复命令" in content:
                                pass  # 跳过去重标记
                            else:
                                lines.append(f"  结果: {content[:60]}")
            elif isinstance(msg, ModelResponse):
                for part in msg.parts:
                    pk = getattr(part, "part_kind", None)
                    if pk == "tool-call":
                        command = self._extract_command(part)
                        if command:
                            lines.append(f"执行: {command[:80]}")
                    elif pk == "text":
                        content = getattr(part, "content", "")
                        if isinstance(content, str) and len(content) > 60:
                            content = content[:60] + "..."
                        if content:
                            lines.append(f"回复: {content}")

        return "\n".join(lines)

    def _extract_command(self, part) -> str | None:
        """从 ToolCallPart 提取 command 参数。"""
        args = part.args
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                return None
        if isinstance(args, dict):
            return args.get("command")
        return None
