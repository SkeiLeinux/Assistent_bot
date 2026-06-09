"""
db/models.py — SQLAlchemy модели.

Изменения в этой версии:
  ListCategory получила self-referential связь:
    parent_id → ссылка на родительскую категорию (NULL = корневая)
    children  → список дочерних категорий
    parent    → родительская категория

  Это паттерн Adjacency List — самый простой способ хранить деревья в SQL.
  Каждая запись знает только своего непосредственного родителя.
  Для получения всего пути (breadcrumb) идём вверх по цепочке parent.
"""

from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# ⚠️ Срез 1: User и Family переехали в app/kernel/models/ (kernel — источник идентичности).
# Здесь остались ТОЛЬКО транзитные доменные модели (Lists/Nutrition); их FK-колонки
# ссылаются на kernel-таблицы users/families по имени. Cross-relationships на User/Family
# срезаны (иначе маппер не сконфигурится с чистым kernel-User) — будут восстановлены при
# миграции доменов под Shareable/visible_to в Срезе 2. Сами модели приводятся к specs там же.


class ListCategory(Base):
    __tablename__ = "list_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    emoji: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # ── Иерархия (Adjacency List) ──────────────────────────────────────────
    # parent_id = NULL означает корневую категорию (видна в главном меню).
    # parent_id = 5 означает что эта категория вложена в категорию с id=5.
    #
    # remote_side=[id] — говорит SQLAlchemy что "родительская" сторона
    # связи — это поле id ЭТОЙ ЖЕ таблицы. Без этого он не поймёт
    # направление self-referential связи.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("list_categories.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    children: Mapped[list["ListCategory"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",  # удалил родителя — удалились все дети
    )
    parent: Mapped["ListCategory | None"] = relationship(
        back_populates="children",
        remote_side="ListCategory.id",  # строковая ссылка т.к. класс ещё не до конца определён
    )
    # ──────────────────────────────────────────────────────────────────────

    items: Mapped[list["ListItem"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
    )


class ListItem(Base):
    __tablename__ = "list_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("list_categories.id"), index=True)
    added_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    is_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    category: Mapped["ListCategory"] = relationship(back_populates="items")


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
