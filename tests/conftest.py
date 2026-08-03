"""Общие фикстуры тестов S1: in-memory БД на общем Base.metadata."""

from types import SimpleNamespace

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


def patch_llm_transport(monkeypatch, create):
    """Подменяет HTTP-транспорт LLM и даёт конфигу ключ-заглушку.

    Один шов на все тесты домена питания. Раньше подменялся ленивый синглтон
    `_get_client()`, но после ADR-0018 клиент выбирается ПОД ЦЕЛЬ (первичная /
    резервная), поэтому шов сместился на `_client_for(target)` — фейк отвечает
    за любую цель, и тест не зависит от того, какая из них сработала.

    Ключ-заглушка нужна потому, что путь до транспорта теперь проходит через
    `default_targets()`: без ключа он честно бросит `LLMNotConfiguredError`
    ещё до вызова. Живых запросов при этом нет — `create` фейковый (GAP-6).
    """
    import app.contexts.nutrition.services.llm_service as llm_mod
    from pydantic import SecretStr

    from app.config import settings

    monkeypatch.setattr(settings, "llm_api_key", SecretStr("test-key"))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(llm_mod, "_client_for", lambda target: client)
    return client
