"""SSH 连接元信息的数据访问层（repo）。

职责：纯数据 CRUD，不关心加密。
- secret_enc 字段存的是密文，repo 层原样存取
- 加密/解密在路由层调用 ssh.security 完成

这样 repo 层职责单一，不耦合加密逻辑。
"""

from sqlalchemy.ext.asyncio import AsyncSession
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


async def get(conn_id: str) -> SSHConnection | None:
    """按主键查单个连接。"""
    async with session_maker() as session:
        return await session.get(SSHConnection, conn_id)


async def list_by_user(user_id: str) -> list[SSHConnection]:
    """查某用户的所有连接。"""
    async with session_maker() as session:
        stmt = select(SSHConnection).where(SSHConnection.user_id == user_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def update(conn: SSHConnection) -> SSHConnection:
    """更新连接（整对象传入，覆盖写）。"""
    async with session_maker() as session:
        # merge：对象不在当前 session 中也能更新
        merged = await session.merge(conn)
        await session.commit()
        await session.refresh(merged)
        return merged


async def delete(conn_id: str) -> bool:
    """删除连接，返回是否删到。"""
    async with session_maker() as session:
        conn = await session.get(SSHConnection, conn_id)
        if conn is None:
            return False
        await session.delete(conn)
        await session.commit()
        return True
