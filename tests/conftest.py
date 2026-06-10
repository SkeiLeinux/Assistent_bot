"""Общие фикстуры тестов S1: in-memory БД на общем Base.metadata."""

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.kernel.models  # noqa: F401 — регистрирует kernel-таблицы
import app.contexts.lists.models  # noqa: F401 — Lists (lists/list_items/history/hidden_from)
import app.contexts.finance.models  # noqa: F401 — Finance (categories/budgets/actuals/income)
import app.contexts.nutrition.models  # noqa: F401 — Nutrition (shared_foods/food_entries/nutrition_goals)
import app.contexts.reminders.models  # noqa: F401 — Reminders (reminders, S3 wave B)
import app.analytics.schedule  # noqa: F401 — Analytics (digest_schedules, S3 wave B)
import tests.shareable_harness  # noqa: F401 — регистрирует синтетическую test_gifts
from app.db.base import Base


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # Как в проде (app/db/engine): форсим FK в SQLite, иначе ondelete=CASCADE
    # не срабатывает на Core-DELETE (clear-done и т.п.).
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
