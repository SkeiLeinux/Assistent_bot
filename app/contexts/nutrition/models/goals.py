"""NutritionGoals — дневные цели КБЖУ пользователя (ТЗ §4.3, дельта kernel OQ-4).

Дельта target↔current (ТЗ §3): цели КБЖУ съехали с kernel-`User`
(`target_kcal/...`) в эту Nutrition-сущность, 1:1 по `user_id` (`Unique`).
PERSONAL — цели видит только владелец (ТЗ §6 I4). NULL в поле = цель не задана.
"""

from sqlalchemy import Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NutritionGoals(Base):
    __tablename__ = "nutrition_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 1:1 с пользователем (kernel OQ-4). Маппинг owner ↔ user_id; PERSONAL.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )
    target_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_protein: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_carbs: Mapped[float | None] = mapped_column(Float, nullable=True)
