"""FoodEntry — запись личного дневника питания (ТЗ §4.1, Shareable: PERSONAL).

КБЖУ хранятся как **абсолют за порцию** (I1), не на 100 г → сумма за день/период
= простой `SUM`. `created_at` — наивный UTC; границы суток считаются в
`user.timezone` сервисом.

Видимость PERSONAL (ТЗ §6 I4): запись видит только владелец; партнёр чужой
дневник не видит, уведомлений нет. Маппинг миксина (F1.3): `Shareable.owner_id`
↔ `user_id`. scope фиксирован PERSONAL по сущности, отдельной колонки не требует
(ТЗ §3); разрешение PERSONAL зависит только от владельца (kernel §3.5) — фильтр
в репозитории прямой по `user_id`.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FoodEntry(Base):
    __tablename__ = "food_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Маппинг Shareable.owner_id ↔ user_id; PERSONAL (личный дневник).
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # Момент приёма пищи (наивный UTC). Группировка по дням — в user.timezone.
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    # breakfast / lunch / dinner / snack. NULL = не указан (авто по времени, I3).
    meal_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    description: Mapped[str] = mapped_column(Text)  # название позиции
    weight_g: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Абсолют за порцию (I1).
    kcal: Mapped[float] = mapped_column(Float)
    protein: Mapped[float] = mapped_column(Float)
    fat: Mapped[float] = mapped_column(Float)
    carbs: Mapped[float] = mapped_column(Float)

    # manual (текст) / photo (фото+vision) / db (из каталога).
    source: Mapped[str] = mapped_column(String(10), default="manual")
    photo_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Источник из каталога (для source="db").
    shared_food_id: Mapped[int | None] = mapped_column(
        ForeignKey("shared_foods.id"), nullable=True, index=True
    )
