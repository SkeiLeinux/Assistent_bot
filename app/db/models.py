"""
db/models.py — транзитные доменные модели (дормант до своего среза).

⚠️ Состояние Среза 2a:
  • User/Family переехали в app/kernel/models/ (Срез 1).
  • ListCategory/ListItem мигрировали в app/contexts/lists/models/ (Срез 2a) и удалены отсюда.
  • Остались Nutrition-модели (SharedFood/FoodEntry) — приводятся к spec-nutrition в Срезе 2c
    (тогда же: SharedFood.family_id, NutritionGoals, namespace /api/v1/nutrition/*).

FK-колонки ссылаются на kernel-таблицы users/families по имени. Cross-relationships
на User/Family срезаны (иначе маппер не сконфигурится с чистым kernel-User).
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SharedFood(Base):
    """
    Общая база блюд — сохранённые пользователями рецепты/продукты с КБЖУ на 100 г.
    Виден всем; любой может выбрать блюдо, указать свою порцию и добавить запись.
    """
    __tablename__ = "shared_foods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    weight_g: Mapped[float] = mapped_column(Float)           # стандартная порция
    kcal_per_100g: Mapped[float] = mapped_column(Float)
    protein_per_100g: Mapped[float] = mapped_column(Float)
    fat_per_100g: Mapped[float] = mapped_column(Float)
    carbs_per_100g: Mapped[float] = mapped_column(Float)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FoodEntry(Base):
    """
    Запись дневника питания — один приём пищи (или один продукт).

    Ключевое отличие от списков: дневник ЛИЧНЫЙ, привязан к user_id, а не к
    family_id. Партнёр чужие записи не видит и уведомлений по ним не получает.

    КБЖУ хранятся как абсолютные значения уже для съеденной порции
    (а не на 100 г), чтобы суммирование за день было простым SUM().
    """
    __tablename__ = "food_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # created_at = момент приёма пищи. По нему группируем записи по дням
    # (с поправкой на user.timezone в репозитории).
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    # breakfast / lunch / dinner / snack. NULL = не указан.
    meal_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Сырое описание как ввёл пользователь ("гречка с курицей").
    description: Mapped[str] = mapped_column(Text)
    # Вес порции в граммах, если известен (может быть NULL для блюд "на глаз").
    weight_g: Mapped[float | None] = mapped_column(Float, nullable=True)

    kcal: Mapped[float] = mapped_column(Float)
    protein: Mapped[float] = mapped_column(Float)
    fat: Mapped[float] = mapped_column(Float)
    carbs: Mapped[float] = mapped_column(Float)

    # Откуда взята запись: "manual" (текст), "photo" (фото + vision) или "db" (из базы блюд).
    source: Mapped[str] = mapped_column(String(10), default="manual")
    # Telegram file_id фото, если запись создана из фото.
    photo_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Ссылка на SharedFood, если запись создана из общей базы блюд.
    shared_food_id: Mapped[int | None] = mapped_column(
        ForeignKey("shared_foods.id"), nullable=True, index=True
    )
