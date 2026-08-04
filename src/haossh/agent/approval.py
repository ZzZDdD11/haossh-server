"""AI 操作审批模块。

当 AI 要执行变更类操作（write_file/run_background/mutate 命令）时，先创建一个
审批请求，工具函数 await event.wait() 暂停，前端收到 SSE approval_request 事件
后弹窗，用户确认/拒绝后 POST /chat/approve 触发 event.set()，工具继续执行。

核心机制：
- asyncio.Event 实现工具执行中途暂停（等价于 Claude Code 的 input() 阻塞）
- 审批状态存在全局字典，跨 SSE 请求和 approve 请求共享
- 一次性消费：resolve 后 event.set()，工具唤醒后 cleanup 清理
- 有过期时间，超时自动失效
"""

import asyncio
import json
import logging
import time
import uuid

from haossh.ssh.security import classify_command_risk

logger = logging.getLogger(__name__)

# 全局待审批字典：approval_id -> {event, action, resource, approved, expires_at}
_pending_approvals: dict[str, dict] = {}


def create_approval(action: str, resource: str, ttl: int = 120) -> str:
    """创建一个待审批请求，返回 approval_id。

    Args:
        action: 操作类型，如 "write_file" / "execute_command" / "run_background"
        resource: 操作对象，如文件路径或命令全文
        ttl: 过期秒数，默认 120 秒

    Returns:
        approval_id，8 位 hex
    """
    approval_id = uuid.uuid4().hex[:8]
    _pending_approvals[approval_id] = {
        "event": asyncio.Event(),
        "action": action,
        "resource": resource,
        "approved": False,
        "expires_at": time.time() + ttl,
    }
    logger.info("审批请求已创建 approval_id=%s action=%s resource=%s", approval_id, action, resource[:80])
    return approval_id


def get_approval(approval_id: str) -> dict | None:
    """获取审批请求。不存在或已过期返回 None。"""
    item = _pending_approvals.get(approval_id)
    if item is None:
        return None
    if time.time() >= item["expires_at"]:
        _pending_approvals.pop(approval_id, None)
        return None
    return item


def resolve_approval(approval_id: str, approved: bool) -> bool:
    """用户确认/拒绝审批。返回是否成功（审批不存在/已处理返回 False）。

    Args:
        approval_id: create_approval 返回的 ID
        approved: True=确认执行，False=拒绝
    """
    item = _pending_approvals.get(approval_id)
    if item is None or item["event"].is_set():
        return False
    item["approved"] = approved
    item["event"].set()
    logger.info("审批已处理 approval_id=%s approved=%s", approval_id, approved)
    return True


def cleanup_approval(approval_id: str) -> None:
    """清理已完成的审批（工具函数唤醒后调用）。"""
    _pending_approvals.pop(approval_id, None)


def check_tool_approval(tool_name: str, args) -> dict | None:
    """判断工具调用是否需要审批。

    在 SSE 生成器收到 function_tool_call 事件时调用（工具执行前）。
    需要审批返回 {action, resource}，不需要返回 None。

    Args:
        tool_name: 工具名，如 "write_file" / "execute_command"
        args: 工具参数，可能是 dict 或 JSON 字符串
    """
    # args 可能是 str（JSON）或 dict，统一成 dict
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    if tool_name == "write_file":
        return {"action": "write_file", "resource": args.get("path", "")}

    if tool_name == "run_background":
        return {"action": "run_background", "resource": args.get("command", "")}

    if tool_name == "execute_command":
        command = args.get("command", "")
        if classify_command_risk(command) == "mutate":
            return {"action": "execute_command", "resource": command}

    # read_file / list_directory / get_environment / check_task / record_milestone → 自主执行
    return None
