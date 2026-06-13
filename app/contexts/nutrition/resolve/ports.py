"""resolve/ports.py — DTO и контракты резолвера КБЖУ (spec-nutrition-retrieval §5).

DTO (pydantic):
  - Macros            — КБЖУ на 100 г (единица обмена всех ступеней).
  - ExtractedItem     — одна позиция, как её вернул извлекатель (§6): идентификация
                        + per100g (оценка ИЛИ с этикетки) + label_complete.
  - ExtractResult     — обёртка-объект (JSON-режим требует объект сверху).
  - ResolveQuery      — вход ступени-источника (что искать + fallback per100g).
  - ResolvedNutrition — выход ступени: per100g + провенанс (source/source_ref/...).

Контракты (Protocol):
  - NutritionSource   — одна ступень лестницы: `lookup(q) -> ResolvedNutrition|None`.
  - ProductResolver   — прогон лестницы на ОДНУ позицию: `refine(item) -> ResolvedNutrition`.
"""

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, field_validator

ProvenanceSource = Literal["label", "off", "web", "nutritionix", "estimate"]


class Macros(BaseModel):
    """КБЖУ на 100 г. Все числа неотрицательны."""

    kcal: float
    protein: float
    fat: float
    carbs: float

    @field_validator("kcal", "protein", "fat", "carbs")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        return max(0.0, float(v))


class ExtractedItem(BaseModel):
    """Одна позиция из ввода (1 LLM-вызов сегментирует ввод на список таких, §6)."""

    name: str                       # родовое название без бренда («молоко питьевое»)
    brand: str | None = None        # «Домик в деревне»
    fat_pct: float | None = None    # 3.2 — дискриминатор молочки
    portion_g: float | None = None  # масса именно этой порции; None если не указана
    barcode: str | None = None      # EAN (fast-follow; в R1 не извлекается)
    per100g: Macros                 # ВСЕГДА заполнено: с этикетки или оценка модели
    label_complete: bool = False    # этикетка прочитана на 100% → per100g точные
    confidence: float = 0.0

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name пустой")
        return v

    def to_query(self) -> "ResolveQuery":
        """Запрос для ступени-источника. fallback_per100g = текущая оценка (estimate)."""
        return ResolveQuery(
            name=self.name,
            brand=self.brand,
            fat_pct=self.fat_pct,
            barcode=self.barcode,
            fallback_per100g=self.per100g,
        )


class ExtractResult(BaseModel):
    """Обёртка-объект для строгого JSON-режима модели (массив на верхнем уровне нельзя)."""

    items: list[ExtractedItem]


class ResolveQuery(BaseModel):
    """Вход ступени-источника."""

    name: str
    brand: str | None = None
    fat_pct: float | None = None
    barcode: str | None = None
    fallback_per100g: Macros        # = ExtractedItem.per100g (для EstimateSource)


class ResolvedNutrition(BaseModel):
    """Выход ступени: КБЖУ на 100 г + провенанс (показываем пользователю)."""

    per100g: Macros
    source: ProvenanceSource
    source_ref: str | None = None   # URL/код товара/цитата (off/web)
    matched_name: str | None = None # как назван матч в источнике
    confidence: float = 0.0


@runtime_checkable
class NutritionSource(Protocol):
    """Ступень лестницы — заменяемый адаптер (ADR-0002). Добавить источник = новый
    адаптер за этим контрактом, лестницу не трогаем (так R2-web встанет без правок)."""

    name: str
    timeout_s: float

    async def lookup(self, q: ResolveQuery) -> ResolvedNutrition | None: ...


@runtime_checkable
class ProductResolver(Protocol):
    """Прогон лестницы на ОДНУ позицию. `allow_network=False` — бюджет ввода вышел:
    сразу estimate, без сети (§3)."""

    async def refine(
        self, item: ExtractedItem, *, allow_network: bool = True
    ) -> ResolvedNutrition: ...
