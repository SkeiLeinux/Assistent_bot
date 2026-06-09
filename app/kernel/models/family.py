"""kernel Family + Membership (ТЗ Identity §3.2–3.3).

Family — пара. Членство вынесено в таблицу Membership (не FK на User) —
решение С. «не упрощаем». Инварианты: ≤2 членства на семью (I1),
user_id уникален → максимум одна семья на пользователя (I2).
Роли отсутствуют намеренно (равноправие; RBAC = North Star).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship


from app.db.base import Base


class Family(Base):
    __tablename__ = "families"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="family", cascade="all, delete-orphan"
    )


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique → пользователь состоит максимум в одной семье (I2).
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    family: Mapped["Family"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship()  # noqa: F821 — резолв по registry
