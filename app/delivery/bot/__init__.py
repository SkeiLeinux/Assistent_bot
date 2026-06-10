"""Telegram-вход бота: пейринг + generic callback-диспетчер push-кнопок (ТЗ §7).

Композиция (main.py) собирает CallbackDispatcher, регистрирует префикс ``rem``
→ reminder_service и передаёт его в build_dispatcher; change-уведомления Lists
(notify_list_change) монтируются интегратором на мутации Lists API.
"""

from .callbacks import (
    CallbackDispatcher,
    CallbackResult,
    ReminderClosed,
    parse_callback,
)
from .list_changes import (
    ListChange,
    ListChangeEvent,
    make_lists_change_listener,
    notify_list_change,
    should_notify,
)
from .setup import build_callback_router, build_dispatcher

__all__ = [
    "build_dispatcher",
    "build_callback_router",
    "CallbackDispatcher",
    "CallbackResult",
    "ReminderClosed",
    "parse_callback",
    "ListChange",
    "ListChangeEvent",
    "make_lists_change_listener",
    "notify_list_change",
    "should_notify",
]
