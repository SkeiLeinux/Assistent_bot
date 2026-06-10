"""ListItem — узел списка: лист или папка (ТЗ §4.2).

Самоссылочная иерархия (parent_id, adjacency list). Применяет kernel-`Hideable`
(hidden_from + таблица list_items_hidden_from, H3-a) — поэлементное скрытие от партнёра.
scope собственного нет: видимость элемента наследуется от List (§6 I7).
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.time import utcnow_naive
from app.kernel.models import Hideable

from .enums import ItemStatus


class ListItem(Base, Hideable):
    __tablename__ = "list_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(
        ForeignKey("lists.id", ondelete="CASCADE"), index=True, nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("list_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    added_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ItemStatus] = mapped_column(
        Enum(
            ItemStatus,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            length=20,
        ),
        nullable=False,
        default=ItemStatus.OPEN,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Type-специфичные поля (§4.2, L5): SQLite JSON1 → Postgres JSONB (ADR-0005).
    # Холодные данные — в фильтрах горячего пути не участвуют.
    specific_attrs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=utcnow_naive
    )
    # hidden_from — из Hideable (kernel); таблица list_items_hidden_from (H3-a).

    list: Mapped["List"] = relationship(  # noqa: F821 — резолв по registry
        "List", back_populates="items"
    )
    parent: Mapped["ListItem | None"] = relationship(
        "ListItem", back_populates="children", remote_side="ListItem.id"
    )
    children: Mapped[list["ListItem"]] = relationship(
        "ListItem",
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    history: Mapped[list["ListItemHistory"]] = relationship(  # noqa: F821
        "ListItemHistory",
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
