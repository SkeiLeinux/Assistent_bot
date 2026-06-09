"""Исходящий шов уведомлений (generic; транспорт Telegram через DI Bot).

Срез 0 извлекает два примитива:
  deliver(bot, chat_ids, text)   — best-effort рассылка (из services/notification_service._send)
  alert_admin(bot, exc, context) — алерт админу об ошибке (из bot.py error_handler)

Полная политика ретраев/алертов (ADR-0003), маршрутизация callback'ов и
change-уведомления Lists — Срез 3. Старый list-специфичный NotificationService
остаётся в app/services/ до S3.

Контракт import-linter: notifications → kernel, db (+ aiogram Bot через DI).
"""

import html
import logging
import traceback

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.config import settings

logger = logging.getLogger(__name__)


async def deliver(bot: Bot, chat_ids: list[int], text: str) -> None:
    """Разослать text получателям. Сбой доставки одному не прерывает остальных."""
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except TelegramForbiddenError:
            logger.warning("Доставка %s: бот заблокирован", chat_id)
        except TelegramBadRequest as e:
            logger.warning("Доставка %s: %s", chat_id, e)
        except Exception as e:  # noqa: BLE001 — best-effort, не валим вызвавшую операцию
            logger.error("Доставка %s: неожиданная ошибка: %s", chat_id, e)


async def alert_admin(bot: Bot, exc: BaseException, context: str = "") -> None:
    """Отправить админу трейсбек ошибки. No-op, если admin_telegram_id не задан."""
    logger.exception("Unhandled exception: %s", exc, exc_info=exc)
    if not settings.admin_telegram_id:
        return

    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    tb_escaped = html.escape(tb)
    max_len = 3800
    if len(tb_escaped) > max_len:
        tb_escaped = "...(обрезано)\n" + tb_escaped[-max_len:]

    text = (
        f"🔴 <b>Ошибка в боте</b>\n\n"
        f"<b>Тип:</b> {html.escape(type(exc).__name__)}\n"
        f"<b>Сообщение:</b> {html.escape(str(exc))}\n"
    )
    if context:
        text += f"<b>Контекст:</b> {html.escape(context)}\n"
    text += f"\n<pre>{tb_escaped}</pre>"

    try:
        await bot.send_message(
            chat_id=settings.admin_telegram_id, text=text, parse_mode="HTML",
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Не удалось отправить алерт админу: %s", e)
