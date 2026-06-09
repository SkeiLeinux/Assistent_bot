"""kernel Invite — инвайт-код пейринга (ТЗ Identity §3.4).

Полноценная сущность (не строка Family.invite_code) — решение С. «не упрощаем».
Одноразовый (I3): переход active→used атомарен, повторное использование отклоняется.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InviteStatus(str, enum.Enum):
    ACTIVE = "active"
    USED = "used"
    EXPIRED = "expired"
    REVOKED = "revoked"


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[InviteStatus] = mapped_column(
        Enum(
            InviteStatus,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            length=20,
        ),
        default=InviteStatus.ACTIVE,
        server_default=InviteStatus.ACTIVE.value,
        index=True,
    )
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
