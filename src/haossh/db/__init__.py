"""数据库引擎与会话管理。

SQLModel + SQLAlchemy 异步引擎。启动时调用 init_db() 建表。
切换数据库只需改 config.database_url：
- SQLite:  sqlite+aiosqlite:///./haossh.db
- Postgres: postgresql+asyncpg://user:pwd@host:5432/dbname
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from haossh.config import settings

logger = logging.getLogger(__name__)

# 异步引擎：echo=False 关闭 SQL 日志（调试时可改 True）
engine = create_async_engine(settings.database_url, echo=False)

# 异步会话工厂：每次请求/任务用 async with session_maker() as session
session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """启动时建表（已存在的表不重建）。在 main.py 的 lifespan 里调用。"""
    # 导入模型，确保 SQLModel.metadata 注册了所有表
    from haossh.db import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("数据库初始化完成 url=%s", settings.database_url)
