"""SharedFood — общий каталог блюд семьи (ТЗ §4.2, Shareable: FAMILY).

КБЖУ хранятся **на 100 г** (I2) — при добавлении записи из каталога значения
пересчитываются по фактическому весу порции.

Видимость FAMILY (ТЗ §4.2/§6 I4, дельта OQ-N2): каталог общий внутри семьи —
`family_id` фиксирует семью, `created_by` — автора (маппинг миксина F1.3:
`Shareable.owner_id` ↔ `created_by`). scope фиксирован FAMILY по сущности,
отдельной колонки не требует (ТЗ §3) — фильтр в репозитории прямой по `family_id`.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SharedFood(Base):
    __tablename__ = "shared_foods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)  # поиск
    weight_g: Mapped[float] = mapped_column(Float)  # стандартная порция
    kcal_per_100g: Mapped[float] = mapped_column(Float)
    protein_per_100g: Mapped[float] = mapped_column(Float)
    fat_per_100g: Mapped[float] = mapped_column(Float)
    carbs_per_100g: Mapped[float] = mapped_column(Float)
    # Маппинг Shareable.owner_id ↔ created_by (ТЗ §3, аудит F1.3).
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Дельта OQ-N2: FAMILY-scope каталога — принадлежность семье.
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
