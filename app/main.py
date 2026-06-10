"""app.main — точка входа: композиция в одном event-loop.

Поднимает по порядку: миграции → Bot+Dispatcher → web (мини-апа) → scheduler → polling.
Запуск только из корня репозитория (pydantic-settings и alembic берут пути от CWD):

    python -m app.main

Срез 3 (волна B) — композиция склейки контекстов:
  • Notifier(bot) — единственный исходящий шов в Telegram (DI Bot, ADR-0004);
  • scheduler.register(reminder_poll) — срабатывание напоминаний (ADR-0003, ТЗ §6);
  • scheduler.register(digest_job) — ретро-дайджест (Analytics, аудит F2.1);
  • CallbackDispatcher с префиксом «rem» → ReminderService.ack/snooze (push-кнопки §7);
  • deliver-адаптер digest-job (chat_ids,text) → Notifier.deliver(recipients,...).
"""

import asyncio
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings
from app.db.engine import async_session_maker
from app.delivery.bot import (
    CallbackDispatcher,
    CallbackResult,
    ReminderClosed,
    build_dispatcher,
    make_lists_change_listener,
)
from app.delivery.miniapp_api import start_web_server
from app.notifications import Notifier
from app.scheduler import Scheduler

from app.analytics import make_digest_job
from app.contexts.lists.change_events import set_change_listener
from app.contexts.reminders.services import (
    ReminderNotFound,
    ReminderService,
    make_reminder_poll,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def run_migrations() -> None:
    """alembic upgrade head из того же venv, что и текущий python.

    На Windows alembic.exe лежит в .venv/Scripts рядом с python; на Linux — в .venv/bin.
    """
    python = Path(sys.executable)
    alembic = python.parent / ("alembic.exe" if sys.platform == "win32" else "alembic")
    if not alembic.exists():
        logger.error("alembic не найден рядом с python: %s. Запусти: pip install alembic", alembic)
        raise RuntimeError("alembic not found in current venv")

    logger.info("Применяем миграции базы данных...")
    result = subprocess.run(
        [str(alembic), "upgrade", "head"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        logger.error("Ошибка миграций:\n%s", result.stderr)
        raise RuntimeError("Не удалось применить миграции")
    logger.info("Миграции применены успешно")


# ── Адаптеры склейки волны B (трения 1–3 интеграционного брифа) ──────────────────

@dataclass(frozen=True)
class _ChatRecipient:
    """Лёгкий получатель для digest-deliver: Notifier._deliver_one читает только
    telegram_user_id. digest-job отдаёт уже chat_ids (=telegram_user_id), а
    Notifier.deliver ждёт объекты-получатели — этот адаптер закрывает зазор
    (трение 2: deliver(chat_ids,text) ↔ deliver(recipients,*,buttons,kind))."""

    telegram_user_id: int


def make_digest_deliver(notifier: Notifier):
    """Адаптер deliver(chat_ids, text) → Notifier.deliver(recipients, text, kind=...).

    make_digest_job ждёт `deliver: async (chat_ids: list[int], text: str)`; реальный
    шов — Notifier.deliver(recipients: list[User], *, buttons, kind). Оборачиваем
    chat_ids в объекты с telegram_user_id (этого хватает Notifier._deliver_one)."""

    async def deliver(chat_ids: list[int], text: str) -> None:
        recipients = [_ChatRecipient(telegram_user_id=cid) for cid in chat_ids]
        await notifier.deliver(recipients, text, kind="digest")

    return deliver


def build_callback_dispatcher() -> CallbackDispatcher:
    """Generic callback-диспетчер push-кнопок с префиксом «rem» → ReminderService
    (Q-del-2, ТЗ §7). Обработчик владеет собственной сессией на нажатие (open →
    ack/snooze → commit), чтобы быть независимым от lifecycle middleware; callback
    по уже закрытому/удалённому напоминанию (ReminderNotFound) → no-op + короткий
    ответ (edge §10) через ReminderClosed, который диспетчер трактует как closed.

    Трение 1: ReminderService.ack/snooze принимают (viewer, id[, minutes]) — ровно
    то, что ожидает обработчик (Q-del-2)."""
    dispatcher = CallbackDispatcher()

    async def _on_ack(viewer, arg: str) -> CallbackResult:
        async with async_session_maker() as session:
            svc = ReminderService(session)
            try:
                await svc.ack(viewer, int(arg))
            except ReminderNotFound:
                raise ReminderClosed(_safe_int(arg))
            await session.commit()
        return CallbackResult(answer="Отмечено выполненным")

    async def _on_snooze(viewer, arg: str) -> CallbackResult:
        async with async_session_maker() as session:
            svc = ReminderService(session)
            try:
                # ⏰ Отложить 1 ч (R1, reminder_buttons): 60 минут.
                await svc.snooze(viewer, int(arg), minutes=60)
            except ReminderNotFound:
                raise ReminderClosed(_safe_int(arg))
            await session.commit()
        return CallbackResult(answer="Отложено на 1 ч")

    dispatcher.register("rem", {"ack": _on_ack, "snooze": _on_snooze})
    return dispatcher


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def main() -> None:
    run_migrations()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Единственный исходящий шов в Telegram (I1, ADR-0004): Bot инъектится сюда.
    notifier = Notifier(bot)

    # F6.2: real-time push «партнёр изменил список». Lists эмитит нейтральный
    # ListChangeData через свой порт; delivery подписывается здесь (инверсия
    # зависимости — contexts не тянет ребро на delivery, ADR-0004).
    set_change_listener(make_lists_change_listener(notifier))

    # Generic callback-диспетчер push-кнопок (rem:ack/snooze → ReminderService).
    callback_dispatcher = build_callback_dispatcher()
    dp = build_dispatcher(callback_dispatcher=callback_dispatcher)

    # Веб-сервер мини-апы и планировщик — в том же event-loop, что и бот:
    # aiogram ходит в Telegram через polling, aiohttp параллельно отдаёт фронт+API,
    # scheduler тикает по таймеру. Общая БД — одна async_session_maker.
    web_runner = await start_web_server()

    scheduler = Scheduler(interval_seconds=60.0)
    # Job срабатывания напоминаний (ADR-0003, ТЗ §6). Грейс — дефолт фабрики (3 ч,
    # R2); проброс из config — follow-up (Q-rem-2: поля в config.py пока нет).
    scheduler.register(make_reminder_poll(async_session_maker, notifier))
    # Job ретро-дайджеста (Analytics, аудит F2.1) — через deliver-адаптер.
    scheduler.register(make_digest_job(async_session_maker, make_digest_deliver(notifier)))
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await scheduler.stop()
        await web_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
