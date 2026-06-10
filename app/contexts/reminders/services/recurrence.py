"""Продвижение next_fire_at по recurrence (ТЗ §6, I3/I5).

Чистые функции над временем — без БД, легко тестируются. Дельта фиксированная,
без DST-коррекции (§12, осознанно: оба пользователя в одной зоне). Всё время — naive UTC.
"""

from datetime import datetime, timedelta

from app.contexts.reminders.models import Recurrence

_FIXED_DELTA = {
    Recurrence.DAILY: timedelta(days=1),
    Recurrence.WEEKLY: timedelta(weeks=1),
}


def step_delta(recurrence: Recurrence, interval_seconds: int | None) -> timedelta | None:
    """Шаг продвижения для повторяемого. None для NONE (одноразовое не продвигается)."""
    if recurrence in _FIXED_DELTA:
        return _FIXED_DELTA[recurrence]
    if recurrence == Recurrence.INTERVAL:
        if not interval_seconds or interval_seconds <= 0:
            return None
        return timedelta(seconds=interval_seconds)
    return None  # NONE


def next_after(
    current: datetime,
    recurrence: Recurrence,
    interval_seconds: int | None,
) -> datetime | None:
    """Следующий слот строго после `current` (один шаг). None для одноразового."""
    delta = step_delta(recurrence, interval_seconds)
    if delta is None:
        return None
    return current + delta


def advance_to_future(
    current: datetime,
    now: datetime,
    recurrence: Recurrence,
    interval_seconds: int | None,
) -> datetime | None:
    """Продвинуть к ближайшему слоту строго в будущем относительно `now` (§6, I5).

    Применяется при MISSED сверх грейса: «к ближайшему будущему слоту» — без «шторма»
    промежуточных срабатываний (while next<=now: next+=delta). Для одноразового — None.
    """
    delta = step_delta(recurrence, interval_seconds)
    if delta is None:
        return None
    nxt = current + delta
    while nxt <= now:
        nxt = nxt + delta
    return nxt
