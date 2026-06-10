"""List — типизированный список (контейнер видимости, ТЗ §4.1).

Применяет kernel-`Scoped` (owner_id/family_id/scope) — контейнерная видимость.
hidden_from на уровне списка НЕТ: скрытие поэлементное (живёт на ListItem).
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.time import utcnow_naive
from app.kernel.models import Scoped

from .enums import ListType


class List(Base, Scoped):
    __tablename__ = "lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[ListType] = mapped_column(
        Enum(
            ListType,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            length=20,
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    emoji: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # owner_id / family_id / scope — из Scoped (kernel).
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=utcnow_naive
    )

    items: Mapped[list["ListItem"]] = relationship(  # noqa: F821 — резолв по registry
        "ListItem",
        back_populates="list",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
