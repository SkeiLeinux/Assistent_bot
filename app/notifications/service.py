"""Notifications.service — единый исходящий шов в Telegram (ТЗ §4/§5, ADR-0003).

Stateless best-effort доставка с политикой недоставки §5:
  успех                       → sent;
  TelegramForbiddenError      → лог(warning), без ретрая, без алерта (ожидаемо, I4);
  TelegramBadRequest          → лог, без ретрая; алерт (неожиданная ошибка);
  транзиент (сеть/429/5xx/проч)→ один ретрай; стойко → алерт админу;
  фейл одного получателя НЕ прерывает остальных.

Инварианты: I1 (единственный шов), I3 (best-effort: попытка + один ретрай),
I4 (без шторма; неудача самого алерта — только лог), I5 (Bot через DI).

Транспорт ``Bot`` инъектится в конструктор (I5) — сервис не импортирует
bot-инстанс из хендлеров. Алерт по умолчанию идёт на ``admin_telegram_id``;
для тестов/композиции можно подменить ``alert_sink``.
"""

import asyncio
import html
import logging
import traceback
from collections.abc import Callable, Iterable, Sequence

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.kernel.models import User
from app.notifications.models import Button, DeliveryReport

logger = logging.getLogger(__name__)

# Лимит Telegram на длину сообщения; >лимита — усекаем (Q1-А5), без разбивки.
TELEGRAM_MAX_LEN = 4096
# Короткая задержка перед единственным ретраем транзиента (§5).
RETRY_DELAY_SECONDS = 1.0


def render_keyboard(buttons: Sequence[Button] | None) -> InlineKeyboardMarkup | None:
    """Button(CH-9) → aiogram-инлайн-клавиатура. Кнопки в столбик (по строке)."""
    if not buttons:
        return None
    rows = [
        [InlineKeyboardButton(text=b.text, callback_data=b.callback_data)]
        for b in buttons
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def truncate(text: str) -> str:
    """Усечь до лимита Telegram по строкам с хвостом «…и ещё N» (§10, Q1-А5)."""
    if len(text) <= TELEGRAM_MAX_LEN:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    used = 0
    tail_reserve = 40  # запас под строку-хвост
    budget = TELEGRAM_MAX_LEN - tail_reserve
    for i, line in enumerate(lines):
        add = len(line) + (1 if kept else 0)
        if used + add > budget:
            remaining = len(lines) - i
            kept.append(f"…и ещё {remaining} строк")
            break
        kept.append(line)
        used += add
    result = "\n".join(kept)
    # Страховка, если одна строка длиннее лимита.
    if len(result) > TELEGRAM_MAX_LEN:
        result = result[: TELEGRAM_MAX_LEN - 1] + "…"
    return result


class Notifier:
    """Исходящий шов доставки. Держит инъектнутый ``Bot`` (I5)."""

    def __init__(self, bot, *, alert_sink: Callable[[str], object] | None = None) -> None:
        self._bot = bot
        # alert_sink позволяет подменить канал алерта (тест/композиция);
        # по умолчанию — собственный alert_admin на admin_telegram_id.
        self._alert_sink = alert_sink

    # ── Публичный интерфейс (ТЗ §8) ───────────────────────────────────────────

    async def deliver(
        self,
        recipients: Iterable[User],
        text: str,
        *,
        buttons: Sequence[Button] | None = None,
        kind: str = "message",
    ) -> DeliveryReport:
        """Доставить ``text`` получателям с политикой §5. Возвращает сводку."""
        markup = render_keyboard(buttons)
        body = truncate(text)
        sent = blocked = failed = 0

        for user in recipients:
            outcome = await self._deliver_one(user, body, markup, kind)
            if outcome == "sent":
                sent += 1
            elif outcome == "blocked":
                blocked += 1
            else:
                failed += 1
        return DeliveryReport(sent=sent, blocked=blocked, failed=failed)

    async def alert_admin(self, text: str) -> None:
        """Служебный алерт на ``admin_telegram_id``. No-op, если не задан (§8).

        Неудача самого алерта — только лог, без ретрая и рекурсии (I4).
        """
        admin_id = settings.admin_telegram_id
        if not admin_id:
            logger.warning("Алерт админу (admin_telegram_id не задан): %s", text)
            return
        try:
            await self._bot.send_message(chat_id=admin_id, text=truncate(text))
        except Exception as exc:  # noqa: BLE001 — алерт не зацикливаем (I4)
            logger.error("Не удалось отправить алерт админу: %s", exc)

    async def alert_admin_exc(self, exc: BaseException, context: str = "") -> None:
        """Алерт об исключении (переиспользует @dp.error()-путь, A5)."""
        logger.exception("Unhandled exception: %s", exc, exc_info=exc)
        text = self._format_exception(exc, context)
        await self.alert_admin(text)

    # ── Доставка одному получателю + политика §5 ───────────────────────────────

    async def _deliver_one(self, user: User, text, markup, kind: str) -> str:
        chat_id = user.telegram_user_id
        try:
            await self._send(chat_id, text, markup)
            return "sent"
        except TelegramForbiddenError:
            # Ожидаемое действие пользователя: лог, без ретрая, без алерта (I4).
            logger.warning("Доставка %s (%s): бот заблокирован пользователем", chat_id, kind)
            return "blocked"
        except TelegramBadRequest as exc:
            # Запрос некорректен — ретрай бессмысленен; но это неожиданно → алерт.
            logger.warning("Доставка %s (%s): bad request: %s", chat_id, kind, exc)
            await self._emit_alert(f"BadRequest при доставке ({kind}) chat={chat_id}: {exc}")
            return "failed"
        except Exception as exc:  # noqa: BLE001 — транзиент: один ретрай
            return await self._retry_transient(chat_id, text, markup, kind, exc)

    async def _retry_transient(self, chat_id, text, markup, kind: str, first_exc) -> str:
        """Один ретрай транзиента (§5). Стойко → алерт админу, без шторма (I4)."""
        delay = RETRY_DELAY_SECONDS
        if isinstance(first_exc, TelegramRetryAfter):
            delay = min(getattr(first_exc, "retry_after", delay) or delay, 30)
        logger.warning(
            "Доставка %s (%s): транзиент %s — ретрай через %.0fs",
            chat_id, kind, type(first_exc).__name__, delay,
        )
        await asyncio.sleep(delay)
        try:
            await self._send(chat_id, text, markup)
            return "sent"
        except TelegramForbiddenError:
            logger.warning("Доставка %s (%s): бот заблокирован (на ретрае)", chat_id, kind)
            return "blocked"
        except Exception as exc:  # noqa: BLE001 — стойкий фейл → алерт
            logger.error("Доставка %s (%s): стойкий фейл: %s", chat_id, kind, exc)
            await self._emit_alert(
                f"Стойкий фейл доставки ({kind}) chat={chat_id}: {exc}"
            )
            return "failed"

    async def _send(self, chat_id: int, text: str, markup) -> None:
        await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)

    async def _emit_alert(self, text: str) -> None:
        """Маршрут алерта: внешний sink (тест/композиция) либо alert_admin."""
        if self._alert_sink is not None:
            self._alert_sink(text)
            return
        await self.alert_admin(text)

    @staticmethod
    def _format_exception(exc: BaseException, context: str) -> str:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        text = (
            f"🔴 Ошибка в боте\n"
            f"Тип: {type(exc).__name__}\n"
            f"Сообщение: {exc}\n"
        )
        if context:
            text += f"Контекст: {context}\n"
        text += f"\n{tb}"
        return text
