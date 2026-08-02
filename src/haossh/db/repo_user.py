"""认证相关（Tenant + User）的数据访问层（repo）。

职责：纯数据持久化，不关心密码哈希、JWT 怎么生成——这些属于 haossh.auth 包。
Tenant 目前没有独立的操作场景（只在注册时随 User 一起创建），因此不单独拆
repo_tenant.py，与 repo_connection.py / repo_conversation.py 保持同样的
"repo 层只做持久化，业务逻辑在调用方组装好对象再传入"的风格。
"""

from sqlmodel import select

from haossh.db import session_maker
from haossh.db.models import Tenant, User


async def create_tenant_and_user(tenant: Tenant, user: User) -> tuple[Tenant, User]:
    """注册：原子性地创建 Tenant + User。

    两条 insert 在同一个 session/事务里完成，同进同退——不会出现
    "Tenant 建成功但 User 没建成"的孤儿租户。
    """
    async with session_maker() as session:
        session.add(tenant)
        session.add(user)
        await session.commit()
        await session.refresh(tenant)
        await session.refresh(user)
        return tenant, user


async def get_user_by_email(email: str) -> User | None:
    """按邮箱查用户，登录时用来取出密码哈希做校验。

    调用方需自行统一大小写（email.lower()）后再传入，repo 层不做归一化。
    """
    async with session_maker() as session:
        stmt = select(User).where(User.email == email)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_user_by_id(user_id: str) -> User | None:
    """按主键查用户，/auth/me 时用 JWT 里的 user_id 反查用户信息。"""
    async with session_maker() as session:
        return await session.get(User, user_id)


async def get_tenant_by_id(tenant_id: str) -> Tenant | None:
    """按主键查租户，登录/查当前用户时用来带出 tenant_name。"""
    async with session_maker() as session:
        return await session.get(Tenant, tenant_id)
