"""Reminder — напоминание (SSOT расписания, ТЗ §3.1).

Применяет kernel-`Scoped` (owner_id/family_id/scope) — контейнерная видимость I6:
PERSONAL → владелец; FAMILY → семья. hidden_from на напоминании нет (модель §3.1):
generic-напоминания не скрываются поэлементно (подарки — домен Lists).

Состояние срабатывания держится ТОЛЬКО здесь (`next_fire_at`/`status`/`last_fired_at`,
R3) → переживает рестарт/деплой без потерь/дублей (I1, ADR-0003). Всё время — naive
UTC (§12): локальное задаётся через owner.timezone при создании в сервисе.
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.time import utcnow_naive
from app.kernel.models import Scoped

from .enums import DeliveryTarget, Recurrence, ReminderStatus


def _enum(enum_cls):
    # Свежий нативно-выключенный Enum на класс (как в kernel/lists): строковые
    # значения в БД, без общего PG-типа — DB-агностичность (ADR-0005).
    return Enum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        length=20,
    )


class Reminder(Base, Scoped):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    # owner_id / family_id / scope — из Scoped (kernel).
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # Ядро расписания (UTC, naive). NULL = завершено/одноразовое отработало.
    next_fire_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    recurrence: Mapped[Recurrence] = mapped_column(
        _enum(Recurrence), nullable=False, default=Recurrence.NONE
    )
    # Шаг для INTERVAL (секунды). Для WEEKLY день недели берётся из next_fire_at.
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # S1: условие остановки повторений (только при recurrence≠NONE).
    max_occurrences: Mapped[int | None] = mapped_column(Integer, nullable=True)
    until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fired_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # S2: адресат push. NULL ⇒ из scope (PERSONAL→OWNER, FAMILY→FAMILY).
    delivery_target: Mapped[DeliveryTarget | None] = mapped_column(
        _enum(DeliveryTarget), nullable=True
    )

    status: Mapped[ReminderStatus] = mapped_column(
        _enum(ReminderStatus), nullable=False, default=ReminderStatus.ACTIVE
    )
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=utcnow_naive
    )
