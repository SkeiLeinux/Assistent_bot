"""resolve/pipeline.py — лестница фолбэков на ОДНУ позицию (spec-nutrition-retrieval §4).

R1: label-short-circuit → OFF → estimate. Первый успех побеждает; в пределе всегда
`estimate` (per100g из самой позиции) → гарантия ответа и обратная совместимость.
Деградация (пусто/таймаут/ошибка ступени → warning + следующая) — это и есть замена
«мастер-флага»: фича-флагов нет.

Бюджет на весь ввод (§3) держит вызывающий (FoodService): когда он вышел, передаёт
`allow_network=False` — оставшиеся позиции отдаются как estimate без сети (числа уже
есть). Ступень web (Compound, R2) встанет в `network_sources` между OFF и estimate
без правки этого кода.
"""

import asyncio
import logging

from app.contexts.nutrition.resolve.errors import SourceError
from app.contexts.nutrition.resolve.ports import (
    ExtractedItem,
    NutritionSource,
    ResolvedNutrition,
)

logger = logging.getLogger(__name__)


class ResolverPipeline:
    def __init__(
        self,
        network_sources: list[NutritionSource],
        fallback: NutritionSource,
        total_budget_s: float = 12.0,
    ) -> None:
        self.network_sources = network_sources   # R1: [OFF]  (R2: [OFF, CompoundWeb])
        self.fallback = fallback                  # EstimateSource — всегда успешна
        self.total_budget_s = total_budget_s      # читает FoodService для дедлайна ввода

    async def refine(
        self, item: ExtractedItem, *, allow_network: bool = True
    ) -> ResolvedNutrition:
        # 1. этикетка прочитана на 100% — поиск не нужен (удачная ветка кейса 3)
        if item.label_complete:
            return ResolvedNutrition(
                per100g=item.per100g, source="label", confidence=item.confidence
            )

        q = item.to_query()

        # 3. сетевые ступени (R1: только OFF). barcode (шаг 2) — fast-follow, пропущен.
        if allow_network:
            for source in self.network_sources:
                try:
                    result = await asyncio.wait_for(
                        source.lookup(q), source.timeout_s
                    )
                except (TimeoutError, SourceError) as e:
                    # asyncio.wait_for даёт TimeoutError (=asyncio.TimeoutError на 3.11+)
                    logger.warning("resolver source %s failed: %s", source.name, e)
                    continue
                except Exception as e:  # noqa: BLE001 — ни один сбой источника не валит разбор
                    logger.warning("resolver source %s error: %s", source.name, e)
                    continue
                if result is not None:
                    return result

        # 4. estimate — per100g из item; всегда отвечает (гарантия)
        result = await self.fallback.lookup(q)
        if result is None:  # подстраховка: терминальная ступень не должна давать None
            return ResolvedNutrition(per100g=q.fallback_per100g, source="estimate")
        return result
