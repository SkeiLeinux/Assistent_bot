"""ListItemHistory — lightweight action-лог изменений элемента (ТЗ §4.3, L3).

Без полного field-diff; кап хранения обеспечивает репозиторий (под диск VPS).
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

from .enums import HistoryAction


class ListItemHistory(Base):
    __tablename__ = "list_item_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("list_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[HistoryAction] = mapped_column(
        Enum(
            HistoryAction,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            length=20,
        ),
        nullable=False,
    )
    detail: Mapped[str | None] = mapped_column(String(200), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    item: Mapped["ListItem"] = relationship(  # noqa: F821 — резолв по registry
        "ListItem", back_populates="history"
    )
