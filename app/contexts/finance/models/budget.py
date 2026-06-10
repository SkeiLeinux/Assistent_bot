"""BudgetPlan — план по категории на месяц (ТЗ §4.3).

План per-category (на родителе и/или ребёнке, I4) — инвариант суммы НЕ применяется
к плану (только к факту/дням, I3). `period_month` — первый день месяца (год-месяц).
"""

from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BudgetPlan(Base):
    __tablename__ = "finance_budget_plans"
    __table_args__ = (
        UniqueConstraint(
            "category_id", "period_month", name="uq_finance_plan_cat_month"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("finance_categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Первый день месяца (нормализованный «год-месяц»).
    period_month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    planned_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
