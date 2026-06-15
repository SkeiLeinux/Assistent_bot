"""DishIngredient — строка состава составного блюда (snapshot).

Составное блюдо («одно блюдо из нескольких ингредиентов», фича 2026-06-14) хранит
разбивку отдельной строкой на КАЖДЫЙ ингредиент. Состав вешается на одного из двух
родителей:
  - FoodEntry  (food_entry_id)  — состав записи дневника (PERSONAL, viewer.id);
  - SharedFood (shared_food_id)  — состав блюда каталога (FAMILY, family_id).
CHECK гарантирует ровно одного родителя (без полиморфной ссылки): запись и блюдо
независимы, при сохранении в оба места состав дублируется по строке на родителя.

КБЖУ — **абсолют за порцию** ингредиента (как FoodEntry, I1): сумма строк = КБЖУ
блюда. Провенанс ингредиента (`source`/`source_ref`, ProvenanceSource ADR-0013)
сохраняется, чтобы в составе был виден источник чисел по каждой позиции.

ON DELETE CASCADE: удаление записи/блюда уносит её состав — в проде SQLite форсит
`PRAGMA foreign_keys=ON` (app/db/engine), в тестах — conftest; каскад на Core-DELETE.
"""

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DishIngredient(Base):
    __tablename__ = "dish_ingredients"
    __table_args__ = (
        # Ровно один родитель: либо запись дневника, либо блюдо каталога.
        CheckConstraint(
            "(food_entry_id IS NULL) <> (shared_food_id IS NULL)",
            name="ck_dish_ingredient_one_parent",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    food_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("food_entries.id", ondelete="CASCADE"), nullable=True, index=True
    )
    shared_food_id: Mapped[int | None] = mapped_column(
        ForeignKey("shared_foods.id", ondelete="CASCADE"), nullable=True, index=True
    )

    position: Mapped[int] = mapped_column(Integer, default=0)  # порядок в блюде
    name: Mapped[str] = mapped_column(String(200))
    weight_g: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Абсолют за порцию ингредиента (I1).
    kcal: Mapped[float] = mapped_column(Float, default=0.0)
    protein: Mapped[float] = mapped_column(Float, default=0.0)
    fat: Mapped[float] = mapped_column(Float, default=0.0)
    carbs: Mapped[float] = mapped_column(Float, default=0.0)

    # Провенанс ингредиента (ADR-0013, ProvenanceSource); nullable — ручная позиция.
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
