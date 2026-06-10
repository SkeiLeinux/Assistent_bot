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
    FoodService,
    Goals,
    MacroProgress,
    MealGroup,
    PeriodView,
    Totals,
)
from .llm_service import (
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMService,
    NutritionItem,
    NutritionLLM,
    NutritionResult,
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
    # LLM (провайдер-агностично, I7)
    "LLMService",
    "NutritionLLM",
    "NutritionItem",
    "NutritionResult",
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
