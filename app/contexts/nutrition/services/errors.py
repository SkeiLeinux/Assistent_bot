"""Доменные ошибки Nutrition. REST-middleware миниапы мапит их в коды ответа.

LLM-ошибки (LLMError и подклассы) живут в llm_service (провайдер-агностичны, I7).
"""


class NutritionError(Exception):
    """База доменных ошибок Nutrition."""


class InvalidInput(NutritionError):
    """Некорректный ввод запроса (REST: 422)."""


class FamilyRequired(NutritionError):
    """Действие требует семьи (каталог FAMILY) — пользователь ещё не спарен."""


class EntryNotFound(NutritionError):
    """Запись дневника не найдена/не принадлежит зрителю (REST: 404)."""


class FoodNotFound(NutritionError):
    """Блюдо каталога не найдено/не видно семье зрителя (REST: 404)."""
