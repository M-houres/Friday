"""数据库初始化 —— PostgreSQL + Redis 连接管理和建表"""

import logging

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.config import settings
from src.db_schema import SCHEMA_STATEMENTS

logger = logging.getLogger(__name__)
BASELINE_ALEMBIC_VERSION = "20260515_000001"

engine = create_async_engine(settings.database_url, pool_size=20, max_overflow=10, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
redis_client: aioredis.Redis | None = None


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def get_redis() -> aioredis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return redis_client


async def check_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False


async def check_redis() -> bool:
    try:
        r = await get_redis()
        await r.ping()
        return True
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        return False


async def init_db():
    """初始化数据库表

    仅在冷启动且未做任何 Alembic 标记时执行基线 bootstrap。
    常规演进应通过 Alembic 迁移完成，而不是在每次应用启动时重放全部 DDL。
    """
    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        version_table = await conn.execute(text("SELECT to_regclass('public.alembic_version')"))
        version_table_exists = bool(version_table.scalar())
        if version_table_exists:
            version_row = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            if version_row.scalar():
                logger.info("Alembic version detected; skipping bootstrap schema replay")
                return

        logger.info("Alembic version missing; bootstrapping baseline schema")
        for statement in SCHEMA_STATEMENTS:
            try:
                await conn.execute(text(statement))
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.warning(f"Schema statement warning: {e}")
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL PRIMARY KEY
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO alembic_version (version_num)
                SELECT :version_num
                WHERE NOT EXISTS (SELECT 1 FROM alembic_version)
                """
            ),
            {"version_num": BASELINE_ALEMBIC_VERSION},
        )
    logger.info("Database schema initialized")


async def close_db():
    """关闭数据库连接"""
    global redis_client
    await engine.dispose()
    if redis_client:
        await redis_client.aclose()
        redis_client = None
    logger.info("Database connections closed")
