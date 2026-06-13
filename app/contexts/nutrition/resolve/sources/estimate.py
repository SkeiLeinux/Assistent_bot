"""resolve/sources/estimate.py — терминальный fallback (spec-nutrition-retrieval §9).

НЕ делает нового LLM-вызова: возвращает per100g, которую извлекатель уже положил в
позицию (`q.fallback_per100g`). Всегда успешна → гарантия ответа и обратная
совместимость: если OFF (и веб в R2) ничего не дали, числа = сегодняшняя оценка
модели. Источник помечается `estimate` — пользователю показываем как прикидку.
"""

from app.contexts.nutrition.resolve.ports import ResolvedNutrition, ResolveQuery


class EstimateSource:
    """Последняя ступень лестницы. Никогда не возвращает None."""

    name = "estimate"
    timeout_s = 0.0  # без сети

    async def lookup(self, q: ResolveQuery) -> ResolvedNutrition:
        return ResolvedNutrition(per100g=q.fallback_per100g, source="estimate")
