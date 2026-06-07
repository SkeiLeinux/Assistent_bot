"""
db/database.py — подключение к базе данных и инициализация.

Здесь один раз создаётся engine и фабрика сессий (async_session_maker).
Все остальные части приложения получают сессию через get_session().

Про async в SQLAlchemy:
  Обычный SQLAlchemy блокирует поток пока ждёт ответа от базы.
  asyncio-версия (create_async_engine) не блокирует — бот может
  обрабатывать другие сообщения пока идёт запрос к БД.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from db.models import Base
from config import settings


# Engine — это "соединение" с базой данных.
# echo=True выводит все SQL-запросы в консоль — удобно при разработке,
# в продакшне лучше выключить (echo=False).
engine = create_async_engine(
    settings.database_url,
    echo=True,
)

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
