from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from praxis_core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_async_engine_from_settings(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=5,
        pool_recycle=1800,
    )


def get_sessionmaker(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = create_async_engine_from_settings(settings)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


def reset_sessionmaker() -> None:
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def session_scope(
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    sm = sessionmaker or get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
