"""Общие фикстуры тестов S1: in-memory БД на общем Base.metadata."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401 — регистрирует доменные таблицы в Base.metadata
import app.kernel.models  # noqa: F401 — регистрирует kernel-таблицы
import tests.shareable_harness  # noqa: F401 — регистрирует синтетическую test_gifts
from app.db.base import Base


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
