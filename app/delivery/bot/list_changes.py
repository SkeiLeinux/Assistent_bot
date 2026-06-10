"""delivery — change-уведомления Lists (F6.2, ТЗ §7), строго через видимость (I2).

Эмитится из ``delivery`` (top-слой: импортирует и Lists, и Notifications) ПОСЛЕ
успешных мутаций Lists API — хук-after. Notifications-слой эту эмиссию не делает
сам (он ниже contexts по слоям и Lists не знает); монтаж хука на конкретные
мутации — задача интегратора (см. парковку Q-del-1).

Получатели резолвятся через kernel (scope/family) с двумя вычитаниями (I2):
  - актор не получает своё же изменение (ТЗ §7);
  - исключённый видимостью (hidden_from элемента) не уведомляется (скрытый
    подарок не пушится одаряемому, A3).

Матрица «событие → шлём» (Q1-Б3, spec-notifications §7/§14):
  ITEM_ADDED / ITEM_DONE / ITEM_MOVED / ITEM_DELETED → шлём;
  LIST_CREATED / LIST_DELETED → шлём;
  ITEM_TEXT_EDITED → НЕ шлём (шум).
Полнота монтажа (какие именно вызовы Lists дёргают хук) — парковка Q-del-1.
"""

import enum
import logging
from dataclasses import dataclass, field

from app.kernel.models.shareable import Scope
from app.notifications.recipients import resolve_recipients

from app.contexts.lists.change_events import ListChangeData

logger = logging.getLogger(__name__)


class ListChangeEvent(str, enum.Enum):
    """Типы изменений Lists (spec-notifications §7)."""

    ITEM_ADDED = "item_added"
    ITEM_DONE = "item_done"
    ITEM_MOVED = "item_moved"
    ITEM_DELETED = "item_deleted"
    ITEM_TEXT_EDITED = "item_text_edited"  # НЕ уведомляется (шум, Б3)
    LIST_CREATED = "list_created"
    LIST_DELETED = "list_deleted"


# Матрица-конфиг (Q1-Б3): событие → отправлять ли уведомление.
# Каркас намеренно перечисляет все события явно — добавление/смена политики =
# правка одной строки, а не кода (build-contract: матрица-конфиг).
NOTIFY_MATRIX: dict[ListChangeEvent, bool] = {
    ListChangeEvent.ITEM_ADDED: True,
    ListChangeEvent.ITEM_DONE: True,
    ListChangeEvent.ITEM_MOVED: True,
    ListChangeEvent.ITEM_DELETED: True,
    ListChangeEvent.LIST_CREATED: True,
    ListChangeEvent.LIST_DELETED: True,
    ListChangeEvent.ITEM_TEXT_EDITED: False,
}

# Человекочитаемые фрагменты «партнёр изменил X» (ru, §7).
_VERB: dict[ListChangeEvent, str] = {
    ListChangeEvent.ITEM_ADDED: "добавил(а)",
    ListChangeEvent.ITEM_DONE: "отметил(а) выполненным",
    ListChangeEvent.ITEM_MOVED: "перенёс(ла)",
    ListChangeEvent.ITEM_DELETED: "удалил(а)",
    ListChangeEvent.LIST_CREATED: "создал(а) список",
    ListChangeEvent.LIST_DELETED: "удалил(а) список",
}


def should_notify(event: ListChangeEvent) -> bool:
    """Решение матрицы: шлём ли уведомление по событию (Q1-Б3)."""
    return NOTIFY_MATRIX.get(event, False)


@dataclass(frozen=True)
class ListChange:
    """Описание совершённого изменения Lists для эмиссии уведомления.

    hidden_from — исключённые видимостью на уровне элемента (I2); для
    list-событий пусто. scope/family_id берутся из контейнера List.
    """

    event: ListChangeEvent
    actor_id: int
    family_id: int | None
    scope: Scope
    list_name: str
    item_text: str | None = None
    hidden_from: list[int] = field(default_factory=list)


def render_change_text(change: ListChange) -> str:
    """«партнёр изменил X» (§7)."""
    verb = _VERB.get(change.event, "изменил(а)")
    if change.item_text:
        return f"Партнёр {verb}: «{change.item_text}» (список «{change.list_name}»)"
    return f"Партнёр {verb} «{change.list_name}»"


async def notify_list_change(session, notifier, change: ListChange) -> None:
    """Хук-after: эмитировать change-уведомление через Notifications (I2).

    notifier — объект с ``deliver(recipients, text, *, buttons, kind)`` (Notifier
    либо совместимый). Получатели резолвятся строго через видимость; пустой
    список → ничего не шлём (актор-сам-себе / скрытый элемент).
    """
    if not should_notify(change.event):
        return

    recipients = await resolve_recipients(
        session,
        scope=change.scope,
        owner_id=change.actor_id,
        family_id=change.family_id,
        exclude_actor=change.actor_id,
        hidden_from=change.hidden_from,
    )
    if not recipients:
        return

    text = render_change_text(change)
    await notifier.deliver(recipients, text, kind="list_change")


# ── Порт-слушатель: Lists эмитит ListChangeData → delivery шлёт (F6.2) ────────

def make_lists_change_listener(notifier):
    """Фабрика слушателя для contexts.lists.change_events.set_change_listener.

    Маппит нейтральный ListChangeData (contexts-owned) в delivery-ListChange и
    зовёт notify_list_change. Инверсия зависимости: Lists не знает delivery,
    delivery подписывается на его порт (монтаж — в app/main.py). Доставка
    best-effort: сбои отдельной доставки гасятся уровнем Notifier."""

    async def _listener(session, data: ListChangeData) -> None:
        change = ListChange(
            event=ListChangeEvent(data.event),
            actor_id=data.actor_id,
            family_id=data.family_id,
            scope=data.scope,
            list_name=data.list_name,
            item_text=data.item_text,
            hidden_from=list(data.hidden_from),
        )
        await notify_list_change(session, notifier, change)

    return _listener
