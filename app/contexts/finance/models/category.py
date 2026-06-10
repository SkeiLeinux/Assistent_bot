"""Category + CategoryAlias — статьи расходов (ТЗ §4.1/§4.2).

Двухуровневое дерево (I1): родитель `parent_id=NULL`, подкатегория ссылается на
родителя. Идентичность подкатегории = (parent_id, name) (I2) — имена не глобально
уникальны («Прочее» — и родитель, и ребёнок). Весь Finance — FAMILY: несём
`family_id` напрямую (аудит F1.2), без Shareable-миксина (scope/hidden_from не нужны).
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

from .enums import CategoryStatus


class Category(Base):
    __tablename__ = "finance_categories"
    __table_args__ = (
        # I2: имя уникально в пределах (родитель, семья). NULL parent_id —
        # корневые: их уникальность по (family) обеспечивается на уровне name+NULL.
        UniqueConstraint(
            "parent_id", "name", "family_id", name="uq_finance_category_ident"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("finance_categories.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    family_id: Mapped[int] = mapped_column(
        ForeignKey("families.id"), nullable=False, index=True
    )
    status: Mapped[CategoryStatus] = mapped_column(
        Enum(
            CategoryStatus,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            length=20,
        ),
        nullable=False,
        default=CategoryStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    parent: Mapped["Category | None"] = relationship(
        "Category", back_populates="children", remote_side="Category.id"
    )
    children: Mapped[list["Category"]] = relationship(
        "Category",
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    aliases: Mapped[list["CategoryAlias"]] = relationship(
        "CategoryAlias",
        back_populates="category",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CategoryAlias(Base):
    """Явный алиас имени-дубля → канон (ADR-0002; без авто-fuzzy, I7)."""

    __tablename__ = "finance_category_aliases"
    __table_args__ = (
        # Алиас уникален в пределах семьи (имена не глобальны между семьями).
        UniqueConstraint(
            "alias_name", "family_id", name="uq_finance_alias_name"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    alias_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("finance_categories.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[int] = mapped_column(
        ForeignKey("families.id"), nullable=False, index=True
    )

    category: Mapped["Category"] = relationship("Category", back_populates="aliases")
