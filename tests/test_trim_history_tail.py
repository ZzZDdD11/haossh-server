"""验证 trim_history 结尾对齐：处理后的历史必须以 ModelRequest 结尾。

复现"部署十几分钟后报 Processed history must end with a ModelRequest"的场景：
1. 超长历史触发 token 裁剪
2. 最后一条超大的 user-prompt 也必须保留（不能因超预算被裁掉）
3. 无论中间处理器怎么变，最终结尾一定是 ModelRequest

运行: uv run python tests/test_trim_history_tail.py
"""

import asyncio

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from haossh.agent.context import trim_history


def _req(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _resp(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_call_resp(cid: str, cmd: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name="execute_command", args={"command": cmd}, tool_call_id=cid)])


def _tool_return_req(cid: str, out: str) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name="execute_command", content=out, tool_call_id=cid)])


async def test_normal_ends_with_request():
    print("=" * 60)
    print("测试1: 正常长历史，结尾应为 ModelRequest")
    print("=" * 60)
    history: list[ModelMessage] = []
    for i in range(30):
        history.append(_tool_call_resp(f"c{i}", f"echo {i}"))
        history.append(_tool_return_req(f"c{i}", f"output {i}" * 100))
    history.append(_req("最新的用户请求"))

    result = await trim_history(history)
    print(f"  {len(history)} -> {len(result)} 条，结尾类型={type(result[-1]).__name__}")
    assert isinstance(result[-1], ModelRequest), "结尾必须是 ModelRequest"
    print("  [PASS]\n")


async def test_huge_last_message_kept():
    print("=" * 60)
    print("测试2: 最后一条是超大 ModelRequest（超过整个 token 预算），必须保留")
    print("=" * 60)
    history: list[ModelMessage] = []
    for i in range(20):
        history.append(_resp(f"回复 {i}"))
        history.append(_req(f"请求 {i}"))
    # 最后一条超大（远超 12000 token 预算）
    huge = _req("超大内容" * 20000)
    history.append(huge)

    result = await trim_history(history)
    print(f"  {len(history)} -> {len(result)} 条，结尾类型={type(result[-1]).__name__}")
    assert isinstance(result[-1], ModelRequest), "结尾必须是 ModelRequest"
    assert result[-1] is huge, "超大的最后一条必须被保留"
    print("  [PASS]\n")


async def test_response_at_tail_stripped():
    print("=" * 60)
    print("测试3: 若管道产出结尾是 ModelResponse，兜底应去掉它")
    print("=" * 60)
    # 构造一个结尾就是 ModelResponse 的历史（模拟处理器破坏结尾）
    history: list[ModelMessage] = [
        _req("用户请求"),
        _resp("助手回复"),  # 结尾是 ModelResponse
    ]
    result = await trim_history(history)
    print(f"  {len(history)} -> {len(result)} 条，结尾类型={type(result[-1]).__name__}")
    assert isinstance(result[-1], ModelRequest), "结尾必须是 ModelRequest（末尾 ModelResponse 应被去掉）"
    print("  [PASS]\n")


async def main():
    await test_normal_ends_with_request()
    await test_huge_last_message_kept()
    await test_response_at_tail_stripped()
    print("=" * 60)
    print("全部通过 ✅")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
