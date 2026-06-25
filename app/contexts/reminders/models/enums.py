"""Перечисления контекста Reminders (ТЗ §3.1, §7)."""

import enum


class Recurrence(str, enum.Enum):
    """Повторяемость напоминания (§2; RRULE — North Star)."""

    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    INTERVAL = "interval"


class ReminderStatus(str, enum.Enum):
    """Состояние срабатывания (FSM §7). Журнала нет — статус на Reminder (R3)."""

    ACTIVE = "active"
    DONE = "done"
    MISSED = "missed"
    CANCELLED = "cancelled"


class DeliveryTarget(str, enum.Enum):
    """Адресат push (S2, §3.1). NULL ⇒ из scope (PERSONAL→OWNER, FAMILY→FAMILY)."""

    OWNER = "owner"
    FAMILY = "family"
    PARTNER = "partner"
