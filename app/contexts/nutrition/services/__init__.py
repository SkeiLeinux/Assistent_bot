"""Сервисы контекста Nutrition."""

from .errors import (
    EntryNotFound,
    FamilyRequired,
    FoodNotFound,
    InvalidInput,
    NutritionError,
)
from .food_service import (
    DayView,
    DishAggregate,
    FoodService,
    Goals,
    MacroProgress,
    MealGroup,
    PeriodView,
    Totals,
    aggregate_dish,
)
from .llm_service import (
    LLMClient,
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    NutritionItem,
    NutritionResult,
    UnresolvedItem,
)

__all__ = [
    # сервис дневника
    "FoodService",
    "DayView",
    "PeriodView",
    "MealGroup",
    "MacroProgress",
    "Totals",
    "Goals",
    # агрегация блюда (L2: арифметика в коде, ТЗ §5.4)
    "aggregate_dish",
    "DishAggregate",
    # LLM (провайдер-агностично, I7)
    "LLMClient",
    "NutritionItem",
    "NutritionResult",
    "UnresolvedItem",
    "LLMError",
    "LLMNotConfiguredError",
    "LLMRateLimitError",
    # ошибки домена
    "NutritionError",
    "InvalidInput",
    "FamilyRequired",
    "EntryNotFound",
    "FoodNotFound",
]
