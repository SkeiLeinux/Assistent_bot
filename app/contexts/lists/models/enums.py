"""Перечисления контекста Lists (ТЗ §4)."""

import enum


class ListType(str, enum.Enum):
    """Тип списка (6 типов MVP). Per-type семантика статусов — North Star."""

    TASK = "task"
    SHOPPING = "shopping"
    IDEA = "idea"
    MOVIE = "movie"
    GIFT = "gift"
    LINK = "link"


class ItemStatus(str, enum.Enum):
    """Обобщённый статус элемента (L1): «посмотрено»/«выполнено» = DONE."""

    OPEN = "open"
    DONE = "done"


class HistoryAction(str, enum.Enum):
    """Тип события в action-логе истории (§4.3)."""

    CREATED = "created"
    EDITED = "edited"
    STATUS_CHANGED = "status_changed"
    MOVED = "moved"
