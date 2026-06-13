"""resolve/ — retrieval-augmented разбор КБЖУ (ADR-0013, spec-nutrition-retrieval R1).

Изолированный подпакет внутри Nutrition (границы — ADR-0004). Зависимость
направленная: `resolve` зависит от `services`/`config`, НЕ наоборот (extractor
переиспользует Groq-клиент из `services.llm_service`). Поэтому FoodService НЕ
импортирует resolve — резолвер/извлекатель собираются в композиционном корне
(api-роут) и инжектятся в сервис; так цикл services↔resolve исключён и контракт
import-linter «resolve — leaf внутри Nutrition» остаётся зелёным.

Ключевой факт (фиксируем комментарием): LLM сам в интернет не ходит. Retrieval —
отдельные адаптеры за портом `NutritionSource` (R1: только Open Food Facts).
`extractor` (Scout) лишь сегментирует ввод и оценивает per100g; «estimate» — это
та самая оценка, отдельного LLM-вызова на неё нет.
"""

from app.contexts.nutrition.resolve.errors import SourceError
from app.contexts.nutrition.resolve.extractor import Extractor, LLMExtractor
from app.contexts.nutrition.resolve.pipeline import ResolverPipeline
from app.contexts.nutrition.resolve.ports import (
    ExtractedItem,
    ExtractResult,
    Macros,
    NutritionSource,
    ProductResolver,
    ResolvedNutrition,
    ResolveQuery,
)
from app.contexts.nutrition.resolve.sources import EstimateSource, OpenFoodFactsSource


def build_extractor() -> Extractor:
    """Дефолтный извлекатель (Groq/Scout, §6). Композиционный корень — api-роут."""
    return LLMExtractor()


def build_resolver() -> ProductResolver:
    """Дефолтная лестница R1: label-short-circuit → OFF → estimate (§4).

    Web (Compound, R2) и barcode (fast-follow) сюда добавятся новым адаптером —
    без правки лестницы (заменяемость источников, ADR-0002).
    """
    from app.config import settings

    return ResolverPipeline(
        network_sources=[
            OpenFoodFactsSource(
                base_url=settings.off_base_url,
                user_agent=settings.off_user_agent,
                lc=settings.off_lc,
                timeout_s=settings.off_timeout_s,
            ),
        ],
        fallback=EstimateSource(),
        total_budget_s=settings.resolver_total_budget_s,
    )


__all__ = [
    "Macros",
    "ExtractedItem",
    "ExtractResult",
    "ResolveQuery",
    "ResolvedNutrition",
    "NutritionSource",
    "ProductResolver",
    "Extractor",
    "LLMExtractor",
    "ResolverPipeline",
    "OpenFoodFactsSource",
    "EstimateSource",
    "SourceError",
    "build_extractor",
    "build_resolver",
]
