"""审计日志写入。

铁律：审计写入失败绝不影响业务主流程——捕获所有异常，只记 warning。
"""

import logging

from haossh.db import session_maker
from haossh.db.models import AuditLog

logger = logging.getLogger(__name__)


async def audit(
    tenant_id: str,
    actor_type: str,            # "user" | "ai"
    actor_id: str,              # AI 操作时也填发起用户 user_id
    action: str,                # "ai.exec" / "user.exec" / "file.read" ...
    resource: str | None = None,
    result: str = "success",    # "success" | "denied" | "error"
    detail: str | None = None,
    connection_id: str | None = None,
    conversation_id: str | None = None,
    source_ip: str | None = None,
) -> None:
    """异步写一条审计日志。

    设计要点：
    - fire-and-forget：try/except 兜底，失败只 warning，绝不抛异常
    - 不存敏感数据：resource 只存命令/路径，detail 只存输出摘要
    - 防超长：resource 截断 2000 字符，detail 截断 500 字符
    """
    try:
        async with session_maker() as session:
            session.add(AuditLog(
                tenant_id=tenant_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                resource=resource[:2000] if resource else None,
                result=result,
                detail=detail[:500] if detail else None,
                connection_id=connection_id,
                conversation_id=conversation_id,
                source_ip=source_ip,
            ))
            await session.commit()
    except Exception as e:
        logger.warning(
            "审计日志写入失败 action=%s resource=%s error=%s",
            action, resource, e,
        )
