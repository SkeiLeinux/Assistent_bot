"""
db/database.py — подключение к базе данных и инициализация.

Здесь один раз создаётся engine и фабрика сессий (async_session_maker).
Все остальные части приложения получают сессию через get_session().

Про async в SQLAlchemy:
  Обычный SQLAlchemy блокирует поток пока ждёт ответа от базы.
  asyncio-версия (create_async_engine) не блокирует — бот может
  обрабатывать другие сообщения пока идёт запрос к БД.
"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.db.base import Base
from app.config import settings


# Engine — это "соединение" с базой данных.
# echo=True выводит КАЖДЫЙ SQL-запрос на INFO. Выключено в блоке 13 не ради
# чистоты, а как условие работоспособности наблюдаемости (ТЗ §9): строки
# `llm_call`/`llm_resolve` пишутся на том же INFO, и в потоке SQL их не видно —
# то есть наблюдаемость, ради которой блок 13 делался, была бы формальной.
# Включать обратно на время отладки запросов можно, но помнить: echo=True
# выставляет уровень на СВОЁМ логгере (`sqlalchemy.engine.Engine`), поэтому
# приглушение в app/main.py его не перебьёт — это два разных механизма.
engine = create_async_engine(
    settings.database_url,
    echo=False,
)


# SQLite по умолчанию НЕ форсит внешние ключи (PRAGMA foreign_keys=OFF), из-за чего
# ondelete=CASCADE не срабатывает на Core-DELETE (например, clear-done каскадит детей/
# историю/hidden_from). Включаем на каждом соединении. На Postgres FK форсятся всегда —
# pragma не нужен (и приведёт к ошибке), поэтому только для sqlite.
if engine.dialect.name == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# Фабрика сессий. Сессия = единица работы с базой (Unit of Work паттерн).
# expire_on_commit=False означает: после commit() объекты не "протухают"
# и мы можем читать их атрибуты без лишних запросов к БД.
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def create_tables():
    """
    Создаёт все таблицы при первом запуске.
    В продакшне таблицы создаются через Alembic-миграции,
    но для локальной разработки это удобный способ.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """
    Генератор сессий для dependency injection в хендлерах.

    Паттерн использования:
        async with get_session() as session:
            result = await session.execute(...)

    async with гарантирует что сессия закроется даже если произошла ошибка.
    """
    async with async_session_maker() as session:
        yield session
