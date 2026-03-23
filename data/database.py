"""
Database connection management.

Provides async engine and session factory for PostgreSQL via asyncpg.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_raw_url = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://sentinel:sentinel_dev@localhost:5432/sentinel",
)

# Railway provides postgresql:// but asyncpg needs postgresql+asyncpg://
DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get an async database session.

    Usage: async with get_session() as session:
    """
    async with async_session_factory() as session:
        yield session
