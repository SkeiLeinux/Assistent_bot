"""
alembic/env.py — конфигурация среды выполнения Alembic.

Это файл который Alembic запускает при каждой команде (upgrade, downgrade и т.д.).
Здесь два важных момента:
  1. Откуда берём URL базы данных — из нашего config.py, не из alembic.ini
  2. Какие модели отслеживаем — импортируем Base из db/models.py

Почему async:
  Наш движок асинхронный (create_async_engine). Alembic по умолчанию синхронный,
  поэтому используем run_sync паттерн — оборачиваем синхронные операции Alembic
  в asyncio.run().
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Импортируем наши модели — Alembic будет сравнивать их с реальной схемой БД
# и генерировать миграции автоматически (autogenerate)
from db.models import Base
from config import settings

# Конфиг из alembic.ini (нужен для настройки логирования)
config = context.config

# Настройка логирования из alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata — это то что Alembic считает "желаемым состоянием" схемы.
# При autogenerate он сравнивает это с реальной БД и генерирует diff.
target_metadata = Base.metadata

# Берём URL из нашего конфига, а не из alembic.ini
# Так у нас один источник правды — файл .env
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """
    Offline режим — генерирует SQL-скрипт без подключения к БД.
    Полезно когда нужно посмотреть что будет выполнено, или применить
    миграцию вручную на сервере.
    Запуск: alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # нужно для SQLite (ALTER TABLE ограничен)
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Выполняет миграции в рамках существующего соединения."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # ВАЖНО для SQLite: он не умеет ALTER COLUMN/DROP COLUMN
                               # напрямую. render_as_batch делает это через
                               # CREATE TABLE new → копирование данных → DROP TABLE old
                               # → RENAME new → old. Автоматически и прозрачно.
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Создаём async engine и запускаем миграции."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # для миграций пул не нужен
    )

    async with connectable.connect() as connection:
        # run_sync — запускает синхронную функцию внутри async контекста.
        # Alembic сам по себе синхронный, поэтому так.
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Online режим — подключается к БД и применяет миграции."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
