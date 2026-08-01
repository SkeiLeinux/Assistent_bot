"""resolve/sources/estimate.py — терминальный fallback (spec-nutrition-retrieval §9).

НЕ делает нового LLM-вызова: возвращает per100g, которую извлекатель уже положил в
позицию (`q.fallback_per100g`). Всегда успешна → гарантия ответа и обратная
совместимость: если OFF (и веб в R2) ничего не дали, числа = сегодняшняя оценка
модели. Источник помечается `estimate` — пользователю показываем как прикидку.
"""

from app.contexts.nutrition.resolve.ports import ResolvedNutrition, ResolveQuery


class EstimateSource:
    """Последняя ступень лестницы. `None` только когда чисел нет в самой позиции."""

    name = "estimate"
    timeout_s = 0.0  # без сети

    async def lookup(self, q: ResolveQuery) -> ResolvedNutrition | None:
        # readout="unreadable" (ТЗ §3.1): модель отказалась выдумывать числа, и
        # оценке взяться неоткуда. Вернуть нули значило бы превратить честный
        # отказ в данные — позиция уходит в `unresolved` (§6.6).
        if q.fallback_per100g is None:
            return None
        return ResolvedNutrition(per100g=q.fallback_per100g, source="estimate")
