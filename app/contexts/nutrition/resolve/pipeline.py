"""resolve/pipeline.py — лестница фолбэков на ОДНУ позицию (ТЗ контракта §7).

**label-preferred вместо label-short-circuit.** Прежнее правило
`if item.label_complete: return source="label"` пропускало внешний источник и
отдавало числа с ВЫСШИМ провенансом ровно там, где ошибка наиболее вероятна и
наименее заметна: замер показал, что на ~8% этикеток модель верно называет
продукт и ошибается в числах на 5–35%, оставаясь внутренне согласованной (Атуотер
такое не ловит — `llm-checks-calibration-2026-07-27.md` §3). Теперь числа с
этикетки остаются ОСНОВНЫМИ (источник тоже ошибается, подменять нечем), но
расхождение >15% показывается пользователю обеими версиями, и решает он.

Порядок: проверки (уже применены извлекателем) → сверка/поиск → estimate. В
пределе ответ есть всегда, кроме случая, когда чисел нет вовсе (`unreadable` и ни
один источник не ответил) — тогда `None`, и позиция уходит в `unresolved` (§6.6).
Деградация ступени (пусто/таймаут/ошибка → warning + следующая) — это и есть
замена «мастер-флага»: фича-флагов нет.

Бюджет на весь ввод (§3) держит вызывающий (FoodService): когда он вышел, передаёт
`allow_network=False`. Ступень web (Compound, R2) встанет в `network_sources`
между OFF и estimate без правки этого кода.
"""

import asyncio
import logging

from app.config import settings
from app.contexts.nutrition.resolve.errors import SourceError
from app.contexts.nutrition.resolve.ports import (
    ExtractedItem,
    NutritionSource,
    ResolvedNutrition,
)

logger = logging.getLogger(__name__)

FLAG_LABEL_DISAGREES = "label_disagrees_with_source"

# Уверенности матча, при которых сверке можно верить (ТЗ §7.1a, ADR-0019).
CONFIDENT_MATCHES = ("exact", "confirmed")


def match_confidence(external: ResolvedNutrition | None) -> str | None:
    """Уверенность матча внешнего источника — гейт §7.1a.

    Ступень не сообщает уверенность — поля `match_confidence` в
    `ResolvedNutrition` нет, поэтому здесь всегда None, и сверка label-preferred
    не запускается. Это НЕ временная заглушка «чтобы компилировалось», а
    требуемое поведение: замер источника показал, что прод-матчинг выбирает
    верную карточку в 36% случаев (`off-source-probe-2026-07-28.md` §3). При
    такой выборке сверка не ловила бы ошибки этикеток, а ПРОИЗВОДИЛА бы ложные.

    **Статус на 2026-08-01: механизма, который выставлял бы `confirmed`, пока
    нет и не будет в прежнем виде.** Гейт ADR-0019 §6 п.2 (LLM-переранжирование
    ≥80% верных при нуле уверенных ошибок) не взят — максимум 78% при шести
    ошибках (`05_implementation/rerank-accuracy-2026-08-01.md`). Тот же замер
    показал, что ранжирование чинить не нужно вовсе: верная карточка, когда она
    есть, всегда на ранге 1–2, а узкое место — покрытие (9–17 из 25, а не 20:
    прежнее число мерилось одной подписью и включало совпадения ккал на ДРУГИХ
    продуктах). Развилка механизма переоткрыта — ADR-0019 §9, решение за С.

    Пока решения нет, `verify_label` в конфиге фактически ни на что не влияет:
    любой результат сети на label-ветке глушится этой функцией. Это и есть
    безопасное состояние — оно ничего не выдумывает.
    """
    return getattr(external, "match_confidence", None)


def rel_diff(a: float, b: float) -> float:
    """Относительное расхождение двух калорийностей."""
    base = max(abs(a), abs(b), 1.0)
    return abs(a - b) / base


class ResolverPipeline:
    def __init__(
        self,
        network_sources: list[NutritionSource],
        fallback: NutritionSource,
        total_budget_s: float = 12.0,
        *,
        verify_label: bool | None = None,
        disagree_pct: float | None = None,
    ) -> None:
        self.network_sources = network_sources   # R1: [OFF]  (R2: [OFF, CompoundWeb])
        self.fallback = fallback                  # EstimateSource
        self.total_budget_s = total_budget_s      # читает FoodService для дедлайна ввода
        self.verify_label = (
            settings.resolver_verify_label if verify_label is None else verify_label
        )
        self.disagree_pct = (
            settings.resolver_disagree_pct if disagree_pct is None else disagree_pct
        )

    async def _run_network(self, q) -> ResolvedNutrition | None:
        """Сетевые ступени по порядку. Сбой ступени не валит разбор — следующая."""
        for source in self.network_sources:
            try:
                result = await asyncio.wait_for(source.lookup(q), source.timeout_s)
            except (TimeoutError, SourceError) as e:
                # asyncio.wait_for даёт TimeoutError (=asyncio.TimeoutError на 3.11+)
                logger.warning("resolver source %s failed: %s", source.name, e)
                continue
            except Exception as e:  # noqa: BLE001 — ни один сбой источника не валит разбор
                logger.warning("resolver source %s error: %s", source.name, e)
                continue
            if result is not None:
                return result
        return None

    async def refine(
        self, item: ExtractedItem, *, allow_network: bool = True
    ) -> ResolvedNutrition | None:
        # Проверки §6 уже применены извлекателем: `readout` понижен, флаги на
        # позиции (см. докстринг extractor). Здесь читаем результат, а не считаем
        # заново — иначе один и тот же флаг попал бы в warnings дважды.
        flags = list(item.warnings)
        label_ok = item.readout == "label" and item.per100g is not None
        q = item.to_query()

        # Сверять есть смысл только при бренде: без него шанс найти товар близок
        # к нулю, а запрос стоит времени (§7.3).
        need_verify = bool(self.verify_label and allow_network and item.brand)
        if label_ok and not need_verify:
            return ResolvedNutrition(
                per100g=item.per100g, source="label",
                confidence=item.confidence, warnings=flags,
            )

        external = await self._run_network(q) if allow_network else None

        # Гейт §7.1a: неуверенный матч приравнивается к отсутствию результата.
        # По ТЗ §7.2 гейт должен стоять ДО развилки и накрывать обе ветки; здесь
        # он накрывает только сверку с этикеткой — сознательно и асимметрично.
        # Сверка это НОВОЕ поведение (OQ-1), и без уверенности матча она
        # производила бы ложные расхождения в двух случаях из трёх (матчинг 36%,
        # `off-source-probe-2026-07-28.md` §3). А выдача OFF на ветке estimate —
        # поведение СУЩЕСТВУЮЩЕЕ (R1), и глушить его значило бы молча снять
        # ступень лестницы. Перенос гейта выше отложен вместе с блоками 7–10:
        # он имеет смысл только когда появится механизм, выставляющий `confirmed`
        # (гейт ADR-0019 §6 не взят — см. докстринг `match_confidence`).
        if label_ok and external is not None:
            if match_confidence(external) not in CONFIDENT_MATCHES:
                external = None

        if label_ok:
            if external is None:
                return ResolvedNutrition(
                    per100g=item.per100g, source="label",
                    confidence=item.confidence, warnings=flags,
                )
            if rel_diff(item.per100g.kcal, external.per100g.kcal) > self.disagree_pct:
                # Числа НЕ подменяем (§2.4): показываем обе версии, выбирает человек.
                return ResolvedNutrition(
                    per100g=item.per100g, source="label", confidence=item.confidence,
                    warnings=[*flags, FLAG_LABEL_DISAGREES], alternative=external,
                )
            return ResolvedNutrition(
                per100g=item.per100g, source="label",
                confidence=item.confidence, warnings=flags,
            )

        if external is not None:
            return external.model_copy(
                update={"warnings": [*flags, *external.warnings]}
            )

        # estimate — per100g из самой позиции; терминальная ступень.
        result = await self.fallback.lookup(q)
        if result is not None:
            return result.model_copy(update={"warnings": [*flags, *result.warnings]})
        # Чисел нет ни у модели (readout="unreadable"), ни у источников → §6.6.
        # Вернуть нули было бы хуже отказа: они выглядят как данные.
        return None
