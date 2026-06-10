"""ActualAmount — факт с дневной гранулярностью (ТЗ §4.4).

Хранится на ЛИСТЬЯХ дерева (подкатегория); родитель — агрегат on-the-fly (I3/I5).
Разрежённо: строка только на ненулевой день. Месяц/период/родитель — `SUM` запросом.
"""

from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ActualAmount(Base):
    __tablename__ = "finance_actuals"
    __table_args__ = (
        UniqueConstraint("category_id", "date", name="uq_finance_actual_cat_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("finance_categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
