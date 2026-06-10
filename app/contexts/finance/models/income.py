"""IncomeSource + Income — доходы по источникам за месяц (ТЗ §4.5/§4.6).

IncomeSource — управляемая сущность (OQ-F1): опц. линк `PARTNER → User`. Income —
план/факт источника за месяц. Сводные строки листа (Доход/Дельта/Расход) НЕ хранятся
(I5) — выводятся аналитикой. Весь Finance FAMILY → `family_id` напрямую.
"""

from datetime import date

from sqlalchemy import (
    Date,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

from .enums import IncomeKind


class IncomeSource(Base):
    __tablename__ = "finance_income_sources"
    __table_args__ = (
        UniqueConstraint("name", "family_id", name="uq_finance_source_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[IncomeKind] = mapped_column(
        Enum(
            IncomeKind,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            length=20,
        ),
        nullable=False,
    )
    # Опциональный линк партнёра на участника семьи (OQ-F1).
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    family_id: Mapped[int] = mapped_column(
        ForeignKey("families.id"), nullable=False, index=True
    )

    incomes: Mapped[list["Income"]] = relationship(
        "Income",
        back_populates="source",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Income(Base):
    __tablename__ = "finance_incomes"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "period_month", name="uq_finance_income_source_month"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("finance_income_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period_month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    planned_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    actual_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)

    source: Mapped["IncomeSource"] = relationship(
        "IncomeSource", back_populates="incomes"
    )
