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
    LLMTarget,
    NutritionItem,
    NutritionResult,
    UnresolvedItem,
    classify_failure,
    default_targets,
    make_llm_model_guard,
    run_startup_guard,
    set_llm_alert_hook,
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
    "LLMTarget",
    "NutritionItem",
    "NutritionResult",
    "UnresolvedItem",
    "LLMError",
    "LLMNotConfiguredError",
    "LLMRateLimitError",
    # отказоустойчивость (ADR-0018): список целей, классификатор, guard, алерты
    "default_targets",
    "classify_failure",
    "run_startup_guard",
    "make_llm_model_guard",
    "set_llm_alert_hook",
    # ошибки домена
    "NutritionError",
    "InvalidInput",
    "FamilyRequired",
    "EntryNotFound",
    "FoodNotFound",
]
