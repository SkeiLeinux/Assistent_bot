"""resolve/extractor.py — единственная точка вызова LLM в домене питания (ТЗ §4–§6).

**Что изменилось в волне 1.** Раньше извлекатель обслуживал только дневниковый
`/analyze`, а каталог (`/foods/analyze`) ходил в LLM своим путём с тремя другими
промптами, требовавшими от модели арифметики. Теперь путь один: все ПЯТЬ путей
(дневник текст/фото, блюдо по составу/по фото/совмещённо) идут сюда, отличаясь
только режимной преамбулой (§4.3). Модель везде отвечает одним контрактом —
`per100g` + граммовки; умножение и сложение делает код (L2, §5.4).

Порядок обработки ответа:
  1. вызов (батчами, если снимков больше лимита модели — §5.2);
  2. C1: снятие ```-обёртки и разбор JSON; невалидно → один ретрай с ужесточающей
     строкой, повторно → `LLMError` (единственный источник отказа, §2.2);
  3. схема `ExtractedItem` (§3.1): отказ модели («unreadable») выражается явно;
  4. C2–C5 (§6): флаги + возможное понижение `readout` — числа не трогаются;
  5. дедуп позиций после батчей (§5.5);
  6. C6 для составных режимов: сходимость Σ граммовок с заявленным весом.

**Почему проверки применяются здесь, а не в `pipeline.refine`** (где они стоят в
псевдокоде ТЗ §7.2): режимы блюда лестницу не проходят вовсе (расчётный путь,
ADR-0013 §17), и при размещении в `refine` половина путей осталась бы без
проверок. Результат для лестницы тот же — она читает уже понижённый `readout`,
что эквивалентно `label_ok = readout=="label" and not checks.downgraded`.

Направление зависимости (ADR-0004): resolve → services (транспорт и DTO), не
наоборот. FoodService получает извлекатель инъекцией из композиционного корня.
"""

import asyncio
import base64
import logging
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from app.config import settings
from app.contexts.nutrition.resolve import prompts
from app.contexts.nutrition.resolve.cache import normalize_name
from app.contexts.nutrition.resolve.checks import (
    FLAG_WEIGHT_MISMATCH,
    apply_checks,
    extract_json,
    weight_mismatch,
)
from app.contexts.nutrition.resolve.ports import ExtractedItem, ExtractResult
from app.contexts.nutrition.services.llm_service import LLMClient, LLMError, LLMTarget

logger = logging.getLogger(__name__)

# Приоритет при слиянии дублей (§5.5): чем выше, тем «надёжнее» происхождение.
_READOUT_RANK = {"label": 2, "estimate": 1, "unreadable": 0}


@runtime_checkable
class Extractor(Protocol):
    """Контракт извлекателя. FoodService зависит только от него (тесты инжектят фейк).

    Это и есть шов I7 после консолидации: единственная операция — извлечение в
    одном из режимов, а не пять методов, каждый со своей контрактной моделью.
    Агрегация за пределами контракта намеренно (§5.4): считает код домена.
    """

    async def extract(
        self,
        *,
        mode: str = prompts.MODE_DIARY,
        text: str | None = None,
        images: list[bytes] | None = None,
        caption: str = "",
        mime_type: str = "image/jpeg",
        dish_name: str | None = None,
        weight_g: float | None = None,
    ) -> list[ExtractedItem]: ...


def _merge(base: ExtractedItem, other: ExtractedItem) -> ExtractedItem:
    """Слияние двух записей об одном продукте (§5.5).

    Побеждает более надёжный `readout`; при равенстве — большая `confidence`.
    `portion_g` берётся ненулевой, `note` конкатенируются, флаги объединяются.
    """
    base_rank = (_READOUT_RANK.get(base.readout, 0), base.confidence)
    other_rank = (_READOUT_RANK.get(other.readout, 0), other.confidence)
    winner, loser = (base, other) if base_rank >= other_rank else (other, base)
    # Note проигравшего-отказника не переносим: «этикетку не видно» — утверждение
    # о конкретном снимке, и на позиции, которую другой снимок прочитал, оно
    # превратилось бы в ложное предупреждение пользователю.
    loser_note = None if loser.readout == "unreadable" else loser.note
    notes = [n for n in (winner.note, loser_note) if n and n.strip()]
    seen: dict[str, None] = {}
    for flag in (*base.warnings, *other.warnings):
        seen[flag] = None
    return winner.model_copy(
        update={
            "portion_g": winner.portion_g if winner.portion_g else loser.portion_g,
            "brand": winner.brand or loser.brand,
            "fat_pct": winner.fat_pct if winner.fat_pct is not None else loser.fat_pct,
            "barcode": winner.barcode or loser.barcode,
            "note": "; ".join(dict.fromkeys(notes)) or None,
            "warnings": list(seen),
        }
    )


def dedup(items: list[ExtractedItem], *, by_name_only: bool = False) -> list[ExtractedItem]:
    """Схлопывает повторы по ключу `(normalize(name), brand, fat_pct)` (§5.5).

    `by_name_only=True` — составные режимы блюда (`dish_text`/`dish_combined`).
    Там множество позиций ЗАДАНО составом, который модель получает дословно в
    каждом батче, поэтому две записи с одним именем — заведомо одна и та же
    строка состава, а не два похожих товара. Полный ключ в этом режиме вредит:
    батч, увидевший этикетку, сообщает бренд, а батч без неё — нет, и ключи
    расходятся. Замерено на C-04 (`tierC_dish_combined`, 2026-08-01): итог
    получался 384.7 вместо 208.7 — ингредиенты складывались дважды.
    На дневниковом пути ключ прежний: там разные бренды — это разные товары.

    **Принятый риск (OQ-5, решение С.):** два одинаковых продукта, съеденных
    дважды, схлопнутся в один. Пользователь видит это на экране подтверждения и
    правит вес. Обратная ошибка — дубль от лица и оборота одной упаковки —
    встречается чаще и раздражает сильнее.

    **Второй проход — `unreadable`-двойники по одному имени.** Ключ §5.5 включает
    бренд, а позиция с `readout="unreadable"` бренда сообщить не может: она и
    означает «этот продукт я не разобрал». Поэтому по полному ключу она никогда
    не совпадёт с прочитанным близнецом, и правило приоритета `label > estimate >
    unreadable` из того же §5.5 оказалось бы мёртвым кодом.

    Это не теория: прогон яруса C (C-03/C-04, 2026-08-01) дал ровно такую картину
    — в составном режиме текст состава уходит в КАЖДЫЙ батч, поэтому второй батч
    честно отвечает `unreadable` по продуктам, чьих этикеток на его снимках нет.
    Вклад в КБЖУ у них нулевой, но в списке позиций они оставались дублями и
    заодно удваивали Σ граммовок — из-за чего C6 ложно срабатывал на наборах,
    которые обязан пропускать молча.

    Порядок первого появления сохраняется: позиции показываются пользователю в
    том же порядке, в каком он снимал.
    """
    out: list[ExtractedItem] = []
    index: dict[tuple, int] = {}
    for item in items:
        key = (
            (normalize_name(item.name),)
            if by_name_only
            else (normalize_name(item.name), normalize_name(item.brand or ""), item.fat_pct)
        )
        pos = index.get(key)
        if pos is None:
            index[key] = len(out)
            out.append(item)
        else:
            out[pos] = _merge(out[pos], item)

    # Проход 2: отказ по продукту, который другой батч всё-таки прочитал, —
    # утверждение, потерявшее силу. Слияние только в эту сторону: прочитанная
    # позиция от него ничего не теряет, кроме, возможно, note с причиной отказа.
    by_name: dict[str, int] = {}
    for i, item in enumerate(out):
        if item.readout != "unreadable":
            by_name.setdefault(normalize_name(item.name), i)
    dropped: set[int] = set()
    for i, item in enumerate(out):
        if item.readout != "unreadable":
            continue
        target = by_name.get(normalize_name(item.name))
        if target is not None:
            out[target] = _merge(out[target], item)
            dropped.add(i)
    return [it for i, it in enumerate(out) if i not in dropped]


class LLMExtractor:
    """OpenAI-совместимая реализация извлекателя (vision + text, строгий JSON).

    Модель — `settings.llm_vision_model` (с 2026-07-27 `gpt-5.4-mini`: победитель
    замера на 25 эталонах яруса A, ADR-0017 §16).

    `prompt_version` существует ради одного требования приёмки (ТЗ §12.3): раннер
    обязан прогнать обе версии промпта, иначе «не хуже baseline» недоказуемо.
    Прод всегда на дефолте.
    """

    def __init__(
        self,
        target: LLMTarget | None = None,
        model: str | None = None,
        *,
        prompt_version: str = prompts.DEFAULT_PROMPT_VERSION,
    ) -> None:
        self._client = LLMClient(target=target, model=model)
        self.model = self._client.model
        self.prompt_version = prompt_version
        # Наблюдаемость для раннера eval: сырой ответ, usage и число вызовов
        # последнего extract(). Прод их игнорирует — это для замера цены,
        # латентности и того, что батчи действительно разрезали ввод.
        self.last_raw: str | None = None
        self.last_usage: dict | None = None
        self.last_call_count: int = 0

    @property
    def system_prompt(self) -> str:
        return prompts.SYSTEM_PROMPTS[self.prompt_version]

    def _max_images(self) -> int:
        return self._client.max_images

    # ── один вызов: запрос → позиции ────────────────────────────────────────

    @staticmethod
    def _with_retry_hint(user_content):
        """Тот же запрос + строка «верни только JSON» (§6.1)."""
        if isinstance(user_content, str):
            return user_content + prompts.RETRY_JSON_SUFFIX
        parts = [dict(p) for p in user_content]
        for part in parts:
            if part.get("type") == "text":
                part["text"] = part["text"] + prompts.RETRY_JSON_SUFFIX
                break
        return parts

    async def _call(self, user_content, n_images: int) -> list[ExtractedItem]:
        call = await self._client.complete(
            system=self.system_prompt, user_content=user_content, n_images=n_images
        )
        self.last_raw = call.text
        self.last_usage = _sum_usage(self.last_usage, call.usage)
        self.last_call_count += 1
        try:
            return _parse(call.text)
        except LLMError:
            # Один ретрай с ужесточающей строкой. Второй провал уходит наверх:
            # бесконечное «попробуем ещё раз» дороже честной ошибки.
            logger.warning("extractor: ответ не разобран, ретрай с ужесточением")
            retry = await self._client.complete(
                system=self.system_prompt,
                user_content=self._with_retry_hint(user_content),
                n_images=n_images,
            )
            self.last_raw = retry.text
            self.last_usage = _sum_usage(self.last_usage, retry.usage)
            self.last_call_count += 1
            return _parse(retry.text)

    # ── публичный вход ──────────────────────────────────────────────────────

    async def extract(
        self,
        *,
        mode: str = prompts.MODE_DIARY,
        text: str | None = None,
        images: list[bytes] | None = None,
        caption: str = "",
        mime_type: str = "image/jpeg",
        dish_name: str | None = None,
        weight_g: float | None = None,
    ) -> list[ExtractedItem]:
        self.last_usage = None
        self.last_call_count = 0
        preamble = prompts.user_preamble(
            mode, text=text, caption=caption, dish_name=dish_name, weight_g=weight_g
        )
        images = list(images or [])

        if not images:
            items = await self._call(preamble, 0)
        else:
            batches = _split(images, self._max_images())
            if len(batches) == 1:
                items = await self._call(_parts(preamble, batches[0], mime_type), len(batches[0]))
            else:
                # §5.2: батчи вместо молчаливой обрезки. Обрезка теряла ровно тот
                # снимок, ради которого пользователь его прислал (оборот упаковки
                # с таблицей), и делала это молча. Семафор — чтобы разбор из
                # десяти снимков не выедал лимит запросов провайдера.
                logger.info(
                    "extractor: %d снимков > лимита %d → %d батчей",
                    len(images), self._max_images(), len(batches),
                )
                sem = asyncio.Semaphore(max(1, settings.llm_batch_concurrency))

                async def run(batch: list[bytes]) -> list[ExtractedItem]:
                    async with sem:
                        return await self._call(_parts(preamble, batch, mime_type), len(batch))

                items = [
                    item
                    for chunk in await asyncio.gather(*(run(b) for b in batches))
                    for item in chunk
                ]

        items = dedup(items, by_name_only=mode in prompts.COMPOSED_MODES)

        # C6 — свойство НАБОРА позиций, а не отдельной позиции, поэтому считается
        # здесь, после дедупа. Только составные режимы: в dish_photos `portion_g`
        # по контракту null, и Σ граммовок там не определена по построению.
        if mode in prompts.COMPOSED_MODES and weight_g:
            if weight_mismatch([i.portion_g for i in items], weight_g):
                items = [
                    i.model_copy(update={"warnings": [*i.warnings, FLAG_WEIGHT_MISMATCH]})
                    for i in items
                ]
        return items


# ── вспомогательное ──────────────────────────────────────────────────────────

def _parse(raw: str) -> list[ExtractedItem]:
    """C1 + схема §3.1. Обе ошибки — `LLMError`, чтобы ретрай был один на оба случая."""
    data = extract_json(raw)
    try:
        result = ExtractResult.model_validate(data)
    except ValidationError as e:
        logger.warning(f"extractor: invalid schema: {e}\nraw: {raw}")
        raise LLMError("Модель вернула некорректные данные") from e
    return [apply_checks(item) for item in result.items]


def _split(images: list[bytes], cap: int) -> list[list[bytes]]:
    """Режет снимки на батчи по лимиту модели, СОХРАНЯЯ порядок.

    Порядок важен: набор C-04 датасета кладёт оригиналы в первый батч, а повторные
    кадры тех же товаров — во второй, и только так дедуп проверяется через
    границу батча, а не внутри одного вызова.
    """
    cap = max(1, cap)
    return [images[i : i + cap] for i in range(0, len(images), cap)] or [[]]


def _parts(preamble: str, images: list[bytes], mime_type: str) -> list[dict]:
    """Текст первым, снимки после (§5.1)."""
    parts: list[dict] = [{"type": "text", "text": preamble}]
    for image_bytes in images:
        b64 = base64.b64encode(image_bytes).decode()
        parts.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}}
        )
    return parts


def _sum_usage(acc: dict | None, add: dict | None) -> dict | None:
    """Складывает usage батчей: цена разбора — это цена всех его вызовов."""
    if add is None:
        return acc
    if acc is None:
        return dict(add)
    out = dict(acc)
    for key, value in add.items():
        if isinstance(value, (int, float)) and isinstance(out.get(key), (int, float)):
            out[key] = out[key] + value
    return out
