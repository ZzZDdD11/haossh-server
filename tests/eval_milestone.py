"""MilestoneTracker 实验测试。

验证 LLM 能否准确判断什么该记录为里程碑、event_type 分类是否准确。

运行: uv run python tests/eval_milestone.py
"""

import asyncio
import json
import logging

from pydantic_ai.messages import ModelResponse

from haossh.agent import agent
from haossh.agent.deps import AgentDeps

logging.basicConfig(level=logging.WARNING)

# 不需要 SSH 连接——record_milestone 不依赖连接


def _extract_milestone_calls(messages: list) -> list[dict]:
    """从 all_messages 提取 record_milestone 工具调用。"""
    calls = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if getattr(part, "part_kind", None) == "tool-call":
                    if getattr(part, "tool_name", None) == "record_milestone":
                        args = part.args
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {"raw": args}
                        calls.append(args)
    return calls


def _extract_other_tools(messages: list) -> list[str]:
    """提取除 record_milestone 外的其他工具调用名。"""
    tools = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if getattr(part, "part_kind", None) == "tool-call":
                    name = getattr(part, "tool_name", "")
                    if name and name != "record_milestone":
                        tools.append(name)
    return tools


async def run_case(name: str, user_message: str, expected_type: str | None = None, should_call: bool = True):
    """跑单个测试用例。"""
    print(f"\n{'='*60}")
    print(f"用例: {name}")
    print(f"输入: {user_message}")
    print(f"{'='*60}")

    # 创建测试对话（record_milestone 需要 conversation_id）
    from haossh.db.models import Conversation
    from haossh.db import repo_conversation
    conv_id = f"eval-{name}"
    conv = Conversation(id=conv_id, user_id="eval")
    await repo_conversation.create_conversation(conv)

    deps = AgentDeps(session_id="", conversation_id=conv_id, allow_sudo=False)
    try:
        result = await agent.run(user_message, deps=deps)
        messages = result.all_messages()
    except Exception as e:
        print(f"  [ERROR] agent 运行失败: {e}")
        return False

    milestone_calls = _extract_milestone_calls(messages)
    other_tools = _extract_other_tools(messages)

    print(f"  里程碑调用: {milestone_calls}")
    print(f"  其他工具: {other_tools}")

    # 判断结果
    if should_call:
        if not milestone_calls:
            print(f"  [FAIL] 期望调 record_milestone 但没调")
            return False
        if expected_type:
            types = [c.get("event_type", "") for c in milestone_calls]
            if expected_type not in types:
                print(f"  [FAIL] 期望 event_type={expected_type}，实际={types}")
                return False
        print(f"  [PASS] 正确调了 record_milestone")
        return True
    else:
        if milestone_calls:
            print(f"  [FAIL] 不期望调 record_milestone 但调了: {milestone_calls}")
            return False
        print(f"  [PASS] 正确没调 record_milestone")
        return True


async def main():
    # 初始化 DB
    from haossh.db import init_db
    await init_db()

    print("=" * 60)
    print("MilestoneTracker 实验测试")
    print("验证 LLM 能否准确判断什么该记录为里程碑")
    print("=" * 60)

    cases = [
        # error 现由规则层自动记录（execute_command 失败时触发），模型不应手动调
        ("用户报告错误-模型不记", "刚才我在服务器上执行 uv sync 报错了，说端口 8080 被占用", None, False),
        ("用户改需求", "不对，我不要查磁盘了，帮我查下内存使用情况", "decision", True),
        ("任务完成", "好的，nginx 已经启动成功了，搞定了", "done", True),
        ("普通提问不记录", "df -h 这个命令是什么意思？", None, False),
    ]

    results = []
    for name, msg, expected_type, should_call in cases:
        passed = await run_case(name, msg, expected_type, should_call)
        results.append((name, passed))

    print(f"\n{'='*60}")
    print("汇总:")
    for name, passed in results:
        print(f"  {'✅' if passed else '❌'} {name}")
    total = sum(1 for _, p in results if p)
    print(f"\n{total}/{len(results)} 通过")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
