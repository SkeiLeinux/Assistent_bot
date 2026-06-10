"""Модели контекста Nutrition. Импорт регистрирует таблицы в общем Base.metadata.

Канонический источник таблиц shared_foods/food_entries/nutrition_goals (транзитный
`app/db/models.py` вычищен на интеграции волны A; шим `_compat` снят как мёртвый код).
"""

from .food_entry import FoodEntry
from .goals import NutritionGoals
from .shared_food import SharedFood

__all__ = ["FoodEntry", "SharedFood", "NutritionGoals"]
