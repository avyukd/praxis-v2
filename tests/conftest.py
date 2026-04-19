from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from praxis_core.db.models import Base


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _has_postgres() -> bool:
    """Best-effort check that a Postgres instance is reachable."""
    test_url = os.environ.get("PRAXIS_TEST_DATABASE_URL")
    if not test_url:
        return False
    return True


@pytest_asyncio.fixture
async def db_session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    """Provides an isolated Postgres schema per test.

    Requires PRAXIS_TEST_DATABASE_URL to be set to a reachable Postgres.
    Skips if not available.
    """
    if not _has_postgres():
        pytest.skip("PRAXIS_TEST_DATABASE_URL not set; skipping DB tests")

    url = os.environ["PRAXIS_TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_pre_ping=True, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text

        await conn.execute(
            text(
                "INSERT INTO rate_limit_state (id, status) VALUES (1, 'clear') ON CONFLICT DO NOTHING"
            )
        )

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir(parents=True, exist_ok=True)
    return v
