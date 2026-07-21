"""重复命令去重器：同一命令多次执行，旧结果替换成提示。

策略：
1. 以最后一个 user-prompt 为分界线，只处理历史轮
2. 按 command 字符串分组，找重复的 tool_call
3. 保留最后一次的 tool_return，旧的替换成"（重复命令，最新结果见下方）"
4. 不删消息（保持工具配对完整），只替换 content

为什么只替换不删除：
[ModelResponse(tool_call), ModelRequest(tool_return)] 是配对的。
删掉 ModelRequest 会留下孤立的 tool_call，LLM 会困惑。
替换 content 保持配对完整，只是旧结果不再占空间。
"""

import logging

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
)

from haossh.agent.context.base import HistoryProcessor

logger = logging.getLogger(__name__)


def _find_current_turn_start(history: list[ModelMessage], fallback_keep_recent: int = 6) -> int:
    """找最后一个 user-prompt 的位置，找不到则用 fallback。"""
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if getattr(part, "part_kind", None) == "user-prompt":
                    return i
    return max(0, len(history) - fallback_keep_recent)


class Deduplicator(HistoryProcessor):
    """重复命令去重：同一命令多次执行，旧结果替换成提示。"""

    name = "deduplicator"

    async def process(self, history: list[ModelMessage]) -> list[ModelMessage]:
        if len(history) <= 1:
            return history

        # 1. 找当前轮分界线
        current_turn_start = _find_current_turn_start(history)

        # 2. 收集历史轮的 tool_call：command → [(msg_index, part_index)]
        #    同时记录 tool_return 的位置：tool_call_id → (msg_index, part_index)
        command_to_calls: dict[str, list[tuple[int, int]]] = {}  # command → [(msg_idx, part_idx)]
        call_id_to_return: dict[str, tuple[int, int]] = {}  # tool_call_id → (msg_idx, part_idx)

        for i in range(current_turn_start):
            msg = history[i]
            if isinstance(msg, ModelResponse):
                # 找 tool_call part，提取 command 参数
                for j, part in enumerate(msg.parts):
                    if getattr(part, "part_kind", None) == "tool-call":
                        command = self._extract_command(part)
                        if command:
                            command_to_calls.setdefault(command, []).append((i, j))
            elif isinstance(msg, ModelRequest):
                # 找 tool_return part，记录 tool_call_id → 位置
                for j, part in enumerate(msg.parts):
                    if getattr(part, "part_kind", None) == "tool-return":
                        call_id = getattr(part, "tool_call_id", None)
                        if call_id:
                            call_id_to_return[call_id] = (i, j)

        # 3. 找重复的 command（出现 2 次以上）
        dedup_count = 0
        for command, call_positions in command_to_calls.items():
            if len(call_positions) < 2:
                continue  # 没重复

            # 保留最后一次（最新），旧的 tool_return 替换成提示
            # call_positions 按顺序排列，最后一个是最新
            for msg_idx, part_idx in call_positions[:-1]:
                # 找这个 tool_call 对应的 tool_return
                # 需要从 ModelResponse 的 part 里拿 tool_call_id
                msg = history[msg_idx]
                part = msg.parts[part_idx]
                call_id = getattr(part, "tool_call_id", None)

                if call_id and call_id in call_id_to_return:
                    ret_msg_idx, ret_part_idx = call_id_to_return[call_id]
                    ret_msg = history[ret_msg_idx]
                    ret_part = ret_msg.parts[ret_part_idx]

                    # 只替换字符串类型的 content
                    if isinstance(ret_part.content, str) and "重复命令" not in ret_part.content:
                        ret_msg.parts[ret_part_idx] = ToolReturnPart(
                            tool_name=ret_part.tool_name,
                            content="（重复命令，最新结果见下方）",
                            tool_call_id=ret_part.tool_call_id,
                        )
                        dedup_count += 1

        if dedup_count:
            logger.info("去重了 %d 条重复工具结果", dedup_count)

        return history

    def _extract_command(self, part) -> str | None:
        """从 ToolCallPart 提取 command 参数。

        args 可能是 dict 或 JSON 字符串，command 在 args.command 里。
        """
        import json

        args = part.args
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                return None
        if isinstance(args, dict):
            return args.get("command")
        return None
