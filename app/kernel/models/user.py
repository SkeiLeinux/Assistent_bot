"""kernel User — идентичность (ТЗ Identity §3.1).

Несёт ТОЛЬКО идентичность. Доменные per-user данные (цели КБЖУ и т.п.) живут
в таблицах своих контекстов по user_id (ADR-0004, OQ-4) — НЕ колонками здесь.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(200))
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Получатель алертов об ошибках (наблюдаемость, req §5).
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # IANA-таймзона: нужна напоминаниям/дайджесту и группировке дневника по дням.
    timezone: Mapped[str] = mapped_column(
        String(50), default="Europe/Amsterdam", server_default="Europe/Amsterdam"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
