"""DigestSchedule — конфиг расписания ретро-дайджеста (ТЗ §3.1).

Единственная write-сущность Analytics (read-side в остальном). Несколько расписаний
на семью (ежедневное + еженедельное), у каждого свой `time_of_day`/`last_sent_at`/
охват (ТЗ §3.1, I7). Логика «созрело ли» и «период с прошлого раза» — здесь, чтобы
digest-job оставался тонким (job.py зовёт is_due/period_bounds).

Тайминг: `time_of_day`/`weekday`/`last_sent_at` сравниваются в таймзоне ВЛАДЕЛЬЦА
семьи (ТЗ §3.1, F6.4). `now`/`last_sent_at` — наивный UTC (как `utcnow_naive` в
проекте); конвертация в локальную зону — внутри is_due/period_bounds. Дублей при
рестарте нет благодаря `last_sent_at` (edge §11): окно считается «отстрелянным», если
last_sent_at попадает в текущую локальную дату-окна.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_DEFAULT_TZ = "Europe/Amsterdam"
_SUNDAY = 6  # ТЗ §3.1: вс = 6


class Cadence(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


class DigestSchedule(Base):
    __tablename__ = "digest_schedules"
    __table_args__ = (
        # ≤1 daily + ≤1 weekly на семью (ТЗ §3.1).
        UniqueConstraint("family_id", "cadence", name="uq_digest_family_cadence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(
        ForeignKey("families.id"), nullable=False, index=True
    )
    cadence: Mapped[Cadence] = mapped_column(
        Enum(
            Cadence,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            length=20,
        ),
        nullable=False,
    )
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_of_day: Mapped[time] = mapped_column(Time, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # База периода «с прошлого раза» — своя у каждого расписания (ТЗ §3.1, I6).
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ── Дефолт-сид (Катя): daily 23:59 + weekly вс 23:00 (ТЗ §3.1) ────────────────

def default_schedules(family_id: int) -> list[DigestSchedule]:
    """Два дефолтных расписания семьи. Сидируется при создании семьи (интегратор)."""
    return [
        DigestSchedule(
            family_id=family_id,
            cadence=Cadence.DAILY,
            weekday=None,
            time_of_day=time(23, 59),
            enabled=True,
            last_sent_at=None,
        ),
        DigestSchedule(
            family_id=family_id,
            cadence=Cadence.WEEKLY,
            weekday=_SUNDAY,
            time_of_day=time(23, 0),
            enabled=True,
            last_sent_at=None,
        ),
    ]


def seed_default_schedules(session, family_id: int) -> None:
    """Персистит дефолтные расписания (daily 23:59 / weekly вс 23:00) в сессию.

    Зовётся delivery в точках создания семьи (kernel post_family / бот newfamily);
    commit делает вызывающий (auth_middleware / handler). join НЕ сидит — семья
    уже существует с расписаниями."""
    session.add_all(default_schedules(family_id))


# ── Тайминг ───────────────────────────────────────────────────────────────────

def _tz(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or _DEFAULT_TZ)
    except Exception:  # noqa: BLE001 — неизвестная зона → дефолт
        return ZoneInfo(_DEFAULT_TZ)


def _to_local(naive_utc: datetime, tz: ZoneInfo) -> datetime:
    """Наивный UTC → aware локальный."""
    return naive_utc.replace(tzinfo=timezone.utc).astimezone(tz)


def _window_start_date(schedule: DigestSchedule, local_now: datetime) -> date:
    """Локальная дата текущего окна расписания (для дедупликации отправок).

    DAILY — сегодняшняя дата; WEEKLY — дата ближайшего прошедшего (или сегодняшнего)
    `weekday` на этой неделе.
    """
    today = local_now.date()
    if schedule.cadence == Cadence.WEEKLY:
        target = schedule.weekday if schedule.weekday is not None else _SUNDAY
        # Сколько дней назад был нужный weekday (0..6).
        delta = (today.weekday() - target) % 7
        return today - timedelta(days=delta)
    return today


def is_due(schedule: DigestSchedule, now: datetime, tz_name: str | None = None) -> bool:
    """Созрело ли расписание к моменту `now` (наивный UTC) в зоне владельца семьи.

    Условия: enabled; для weekly — сегодня нужный weekday; локальное время дня
    наступило; окно ещё не отстреляно (last_sent_at не попадает в дату окна).
    """
    if not schedule.enabled:
        return False

    tz = _tz(tz_name)
    local_now = _to_local(now, tz)

    if schedule.cadence == Cadence.WEEKLY:
        target = schedule.weekday if schedule.weekday is not None else _SUNDAY
        if local_now.weekday() != target:
            return False

    # Время дня наступило?
    if local_now.timetz().replace(tzinfo=None) < schedule.time_of_day:
        return False

    # Окно уже отстреляно? (last_sent_at в локальной дате окна или позже).
    window_date = _window_start_date(schedule, local_now)
    if schedule.last_sent_at is not None:
        last_local_date = _to_local(schedule.last_sent_at, tz).date()
        if last_local_date >= window_date:
            return False

    return True


def period_bounds(
    schedule: DigestSchedule, now: datetime, tz_name: str | None = None
) -> tuple[date, date]:
    """Охват дайджеста [date_from, date_to] (включительно), в локальных датах.

    `date_to` — локальная дата `now`. `date_from` — локальная дата `last_sent_at`
    (период «с прошлого раза», ТЗ §6 I6); при первом запуске — окно по каденсу
    (daily ≈ день, weekly ≈ неделя).
    """
    tz = _tz(tz_name)
    local_now = _to_local(now, tz)
    date_to = local_now.date()

    if schedule.last_sent_at is not None:
        date_from = _to_local(schedule.last_sent_at, tz).date()
    elif schedule.cadence == Cadence.WEEKLY:
        date_from = date_to - timedelta(days=6)
    else:
        date_from = date_to  # daily: окно одного дня

    if date_from > date_to:
        date_from = date_to
    return date_from, date_to
