"""reminder_poll — job-callable срабатывания (ТЗ §6, ADR-0003).

Один тик generic-`scheduler/` (раз в N сек, конфиг) зовёт этот callable. Своего
loop/демона у Reminders нет (F2.1). Инвариант I1/I4: состояние только в БД, claim+advance
в одной транзакции до доставки → рестарт/двойной тик не теряют и не дублируют push.

Поток тика (§6):
  1. выбрать созревшие (status=ACTIVE AND next_fire_at <= now);
  2. для каждого В ОДНОЙ ТРАНЗАКЦИИ:
       - грейс (now − next_fire_at): в окне → к доставке; сверх окна → MISSED (без доставки);
       - last_fired_at = now;
       - продвинуть next_fire_at по recurrence (I3) либо закрыть одноразовое (DONE, I2);
       - MISSED + recurring → продвинуть к ближайшему БУДУЩЕМУ слоту (I5, без «шторма»);
     COMMIT (состояние зафиксировано до доставки — рестарт безопасен);
  3. доставка через Notifications (recipients по scope I6, обе кнопки R1) — best-effort,
     ПОСЛЕ коммита: фейл доставки не откатывает advance (I3/I8).

Доставка инъектируется (`Notifier` порт) — домен Telegram-агностичен (ADR-0004).
Часы инъектируются (`now_fn`) — тесты без sleep.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.db.time import utcnow_naive
from app.contexts.reminders.models import Recurrence, Reminder, ReminderStatus
from app.contexts.reminders.repositories import ReminderRepository
from app.contexts.reminders.services.delivery_port import Notifier, reminder_buttons
from app.contexts.reminders.services.recipients import resolve_recipients
from app.contexts.reminders.services.recurrence import advance_to_future, next_after

logger = logging.getLogger(__name__)

# Грейс-окно misfire — конфиг, дефолт 3 ч (R2). Хранится секундами для DI/тестов.
DEFAULT_GRACE_SECONDS = 3 * 60 * 60


@dataclass
class _Fired:
    """Созревшее, прошедшее в окне грейса → к доставке после коммита."""

    reminder_id: int
    text: str


async def _process_due(
    repo: ReminderRepository, now: datetime, grace: timedelta
) -> list[_Fired]:
    """Шаг 1–2: продвинуть состояние созревших. Возвращает то, что надо доставить."""
    to_deliver: list[_Fired] = []
    for rem in await repo.due(now):
        scheduled = rem.next_fire_at  # гарантированно не None (фильтр due)
        in_grace = (now - scheduled) <= grace
        rem.last_fired_at = now

        if in_grace:
            # «Догнать» один раз: доставить и продвинуть/закрыть (I2/I3/I5).
            to_deliver.append(_Fired(reminder_id=rem.id, text=rem.text))
            _advance_after_fire(rem, scheduled)
        else:
            # Сверх грейса: MISSED, без доставки (I5).
            if rem.recurrence == Recurrence.NONE:
                rem.status = ReminderStatus.MISSED
                rem.next_fire_at = None
            else:
                # Повторяемое: к ближайшему будущему слоту, статус остаётся ACTIVE
                # (FSM MISSED→ACTIVE), без «шторма» промежуточных срабатываний.
                rem.status = ReminderStatus.ACTIVE
                rem.next_fire_at = advance_to_future(
                    scheduled, now, rem.recurrence, rem.interval_seconds
                )
    return to_deliver


def _advance_after_fire(rem: Reminder, scheduled: datetime) -> None:
    """I2/I3: одноразовое → DONE/None; повторяемое → следующий слот, ACTIVE."""
    if rem.recurrence == Recurrence.NONE:
        rem.status = ReminderStatus.DONE
        rem.next_fire_at = None
    else:
        rem.status = ReminderStatus.ACTIVE
        rem.next_fire_at = next_after(scheduled, rem.recurrence, rem.interval_seconds)


async def run_poll(
    session,
    notifier: Notifier,
    *,
    now_fn=utcnow_naive,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
) -> int:
    """Один проход срабатывания. Возвращает число доставленных напоминаний.

    `session` — живая AsyncSession; транзакцией владеет этот callable (commit здесь).
    """
    now = now_fn()
    grace = timedelta(seconds=grace_seconds)
    repo = ReminderRepository(session)

    fired = await _process_due(repo, now, grace)
    # COMMIT до доставки: состояние зафиксировано → рестарт/повторный тик безопасны (I1/I4).
    await session.commit()

    delivered = 0
    for f in fired:
        rem = await repo.get(f.reminder_id)
        if rem is None:
            continue
        recipients = await resolve_recipients(session, rem)
        if not recipients:
            logger.warning("reminder %s: нет получателей", rem.id)
            continue
        try:
            await notifier.deliver(
                recipients,
                f.text,
                buttons=reminder_buttons(rem.id),
                kind="reminder",
            )
            delivered += 1
        except Exception:  # noqa: BLE001 — best-effort; фейл не откатывает advance (I3/I8)
            logger.exception("reminder %s: доставка не удалась", rem.id)
    return delivered


def make_reminder_poll(
    session_factory,
    notifier: Notifier,
    *,
    now_fn=utcnow_naive,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
):
    """Фабрика job-callable для регистрации в generic-`scheduler/` (main.py, интегратор).

    `session_factory` — async_sessionmaker. Возвращает `async def job() -> None`,
    совместимый с scheduler.Scheduler.register (Callable[[], Awaitable[None]]).
    Своей сессией на тик владеет job: открыть → run_poll (commit внутри) → закрыть.
    """

    async def job() -> None:
        async with session_factory() as session:
            await run_poll(
                session, notifier, now_fn=now_fn, grace_seconds=grace_seconds
            )

    return job
