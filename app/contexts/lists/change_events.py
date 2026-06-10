"""Lists change-events — contexts-owned порт для real-time push (F6.2).

Проблема слоёв: real-time-уведомление «партнёр изменил список» рендерит и шлёт
delivery (`app/delivery/bot/list_changes.py`), но Lists НЕ может звать delivery
напрямую — delivery выше по слоям (import-linter: `app.delivery → app.contexts`).
Решение — инверсия зависимости: Lists владеет нейтральным описанием изменения
(`ListChangeData`) и портом-слушателем; delivery на старте (`app/main.py`) ставит
свою реализацию через `set_change_listener`. Здесь импортируется только kernel
(`Scope`), который ниже по слоям — нарушения границ нет.

Дефолт — no-op: пока слушатель не установлен (тесты, запуск без delivery),
`emit` ничего не делает. Доставка best-effort: эмиссия идёт после успешной
мутации, но до commit middleware; падение слушателя не должно ронять мутацию —
этим управляет реализация слушателя (delivery ловит/логирует свои сбои).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.kernel.models.shareable import Scope

# Тип слушателя: async (session, ListChangeData) -> None.
ChangeListener = Callable[["object", "ListChangeData"], Awaitable[None]]


@dataclass(frozen=True)
class ListChangeData:
    """Нейтральное (без delivery-типов) описание совершённого изменения Lists.

    `event` — строковый код, совпадающий с `ListChangeEvent.value` в delivery
    (`"item_added" | "item_done" | "item_moved" | "item_deleted" |
    "item_text_edited" | "list_created" | "list_deleted"`). Маппинг строки в
    delivery-enum делает фабрика слушателя в delivery, не Lists.

    scope/family_id — из контейнера-списка; hidden_from — исключённые видимостью
    на уровне элемента (для list-событий пусто).
    """

    event: str
    actor_id: int
    family_id: int | None
    scope: Scope
    list_name: str
    item_text: str | None = None
    hidden_from: list[int] = field(default_factory=list)


# Модуль-левел порт: единственный слушатель (на двоих пользователей шина —
# оверинжиниринг, NFR простоты). Устанавливается интегратором из main.py.
_listener: ChangeListener | None = None


def set_change_listener(fn: ChangeListener | None) -> None:
    """Установить (или снять, передав None) слушателя change-событий Lists."""
    global _listener
    _listener = fn


async def emit(session, data: ListChangeData) -> None:
    """Эмитировать change-событие слушателю. Нет слушателя → no-op (дефолт)."""
    listener = _listener
    if listener is None:
        return
    await listener(session, data)


__all__ = ["ListChangeData", "ChangeListener", "set_change_listener", "emit"]
