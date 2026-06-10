"""
db/models.py — пусто (транзитные доменные модели вычищены).

История переезда (вариант C, инкрементальный решейп):
  • User/Family → app/kernel/models/ (Срез 1).
  • ListCategory/ListItem → app/contexts/lists/models/ (Срез 2a).
  • SharedFood/FoodEntry/NutritionGoals → app/contexts/nutrition/models/ (Срез 2c,
    волна A). Здесь больше не объявляются: канонический источник — контекст Nutrition
    (tablename'ы shared_foods/food_entries/nutrition_goals регистрируются там).

Модуль оставлен как заглушка, чтобы не ломать исторические ссылки в доках; ни одной
Base-таблицы не объявляет. Метадату для Alembic/тестов набирают пакеты kernel/contexts
(см. alembic/env.py и tests/conftest.py).
"""
