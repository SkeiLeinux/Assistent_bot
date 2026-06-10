"""delivery/bot — generic callback-диспетчер push-кнопок (ТЗ §7).

Notifications формирует кнопки (payload вида ``rem:ack:7`` / ``rem:snooze:7``);
нажатие приходит сюда, в ``delivery/bot``, который диспетчит в сервис источника
(Reminders ack/snooze). Диспетчер **generic**: новые префиксы добавляются
ТАБЛИЦЕЙ (``register``), не правкой кода диспетчера.

Шов к Reminders — инъекция: контекст reminders строится параллельно (волна B),
поэтому связывание ``rem`` → reminder_service делает композиция (main.py), а не
этот модуль (нет жёсткого импорта contexts из delivery в момент загрузки).

Edge §10: callback по закрытому/удалённому напоминанию (``DONE``/удалён) → no-op
+ короткий ответ пользователю; обработчик источника бросает ``ReminderClosed``.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Обработчик действия: (viewer, arg) -> CallbackResult. arg — «хвост» payload
# (обычно id напоминания строкой). viewer — резолвнутый kernel-зритель.
ActionHandler = Callable[[object, str], Awaitable["CallbackResult"]]


class ReminderClosed(Exception):
    """Callback по уже закрытому/удалённому напоминанию (edge §10).

    Живёт в delivery (а не в reminders) намеренно: delivery не импортирует
    reminders в момент загрузки (шов — таблица префиксов). Сервис-источник
    при ack/snooze закрытого поднимает её, диспетчер трактует как no-op.
    """

    def __init__(self, reminder_id: int | None = None) -> None:
        super().__init__(f"reminder {reminder_id} closed")
        self.reminder_id = reminder_id


@dataclass(frozen=True)
class CallbackResult:
    """Ответ диспетчера для бота: текст всплывашки + флаг show_alert."""

    answer: str = ""
    alert: bool = False


# Нейтральные ответы (ru, §10).
_NEUTRAL = CallbackResult(answer="")
_CLOSED = CallbackResult(answer="Напоминание уже закрыто")
_STALE = CallbackResult(answer="Кнопка устарела")


def parse_callback(data: str) -> tuple[str, str, str] | None:
    """``prefix:action:arg`` → кортеж. None при некорректном payload (§10)."""
    if not data:
        return None
    parts = data.split(":", 2)
    if len(parts) != 3:
        return None
    prefix, action, arg = parts
    if not prefix or not action:
        return None
    return prefix, action, arg


class CallbackDispatcher:
    """Таблица префиксов → {action: handler}. Generic (ТЗ §7)."""

    def __init__(self) -> None:
        self._table: dict[str, dict[str, ActionHandler]] = {}

    def register(self, prefix: str, actions: dict[str, ActionHandler]) -> None:
        """Зарегистрировать обработчики префикса (напр. ``rem`` → ack/snooze)."""
        self._table.setdefault(prefix, {}).update(actions)

    async def dispatch(self, viewer: object, data: str) -> CallbackResult:
        """Смаршрутизировать callback. Закрытое напоминание → no-op (§10)."""
        parsed = parse_callback(data)
        if parsed is None:
            logger.info("Callback с некорректным payload: %r", data)
            return _STALE
        prefix, action, arg = parsed
        handler = self._table.get(prefix, {}).get(action)
        if handler is None:
            logger.info("Callback без обработчика: %s:%s", prefix, action)
            return _STALE
        try:
            return await handler(viewer, arg)
        except ReminderClosed:
            return _CLOSED
