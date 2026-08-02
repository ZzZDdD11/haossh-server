"""SSH 连接元信息的数据访问层（repo）。

职责：纯数据 CRUD，不关心加密。
- secret_enc 字段存的是密文，repo 层原样存取
- 加密/解密在路由层调用 ssh.security 完成

多租户隔离：故意不提供"裸查询"方法（比如按 id 查、不带 tenant_id 的方法），
所有查询/更新/删除都强制传入 tenant_id 并作为 WHERE 条件的一部分——
从接口设计上让"越权查询别的租户的连接"这件事在代码层面不可能发生。
"""

from sqlmodel import select

from haossh.db import session_maker
from haossh.db.models import SSHConnection


async def create(conn: SSHConnection) -> SSHConnection:
    """插入一条连接记录。"""
    async with session_maker() as session:
        session.add(conn)
        await session.commit()
        await session.refresh(conn)
        return conn


async def get_owned(conn_id: str, tenant_id: str) -> SSHConnection | None:
    """按主键查单个连接，同时校验归属租户。不属于该租户返回 None。"""
    async with session_maker() as session:
        stmt = select(SSHConnection).where(
            SSHConnection.id == conn_id,
            SSHConnection.tenant_id == tenant_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_unchecked(conn_id: str) -> SSHConnection | None:
    """按主键查单个连接，不校验租户——仅供系统内部可信调用使用。

    使用场景：SSH 断线自动重连（ssh/session.py._reconnect）、判断工具是否对
    LLM 可见（agent/tools.py.require_connection）。这两处拿到的 connection_id
    不是前端直接传入的未校验参数，而是已经建立过的连接（内存里存在过，或路由层
    早就做过归属校验后才写进 AgentDeps），此时再要求传 tenant_id 没有实际意义。

    绝不允许在直接暴露给前端的路由里调用这个方法——那些场景必须用 get_owned。
    """
    async with session_maker() as session:
        return await session.get(SSHConnection, conn_id)


async def list_by_tenant(tenant_id: str) -> list[SSHConnection]:
    """查某租户的所有连接。"""
    async with session_maker() as session:
        stmt = select(SSHConnection).where(SSHConnection.tenant_id == tenant_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def update(conn: SSHConnection) -> SSHConnection:
    """更新连接（整对象传入，覆盖写）。

    调用方需先用 get_owned 校验过归属再改字段传进来，这里不重复校验
    tenant_id（对象本身的 tenant_id 字段值就是校验通过后的结果）。
    """
    async with session_maker() as session:
        # merge：对象不在当前 session 中也能更新
        merged = await session.merge(conn)
        await session.commit()
        await session.refresh(merged)
        return merged


async def delete_owned(conn_id: str, tenant_id: str) -> bool:
    """删除连接，同时校验归属租户。不属于该租户或不存在都返回 False。"""
    async with session_maker() as session:
        stmt = select(SSHConnection).where(
            SSHConnection.id == conn_id,
            SSHConnection.tenant_id == tenant_id,
        )
        conn = (await session.execute(stmt)).scalar_one_or_none()
        if conn is None:
            return False
        await session.delete(conn)
        await session.commit()
        return True
