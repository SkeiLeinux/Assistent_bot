"""resolve/sources/openfoodfacts.py — Open Food Facts, ТОЛЬКО lookup по штрихкоду.

**Поисковой ветки здесь нет и возвращать её не надо.** Это не уборка мёртвого
кода, а результат шести замеров (обоснование — `05_implementation/off-value-
2026-08-03.md`, решение — ADR-0019, ревизия 2026-08-03, вариант D). Коротко:
полнотекстовый `cgi/search.pl` + скоринг `select_best` выбирали ЧУЖОЙ товар.
На ветке `estimate` — единственной, где ступень в проде реально срабатывала —
карточка проигрывала типовым числам самой модели 7:13 при медиане ошибки ккал
18,3 % против 8,4 % и 11 грубых промахах из 25 (515 %, 461 %, 381 %). Пять
механизмов выбора (скоринг, 4 режима LLM-переранжирования, LLM-вето) гейт
ADR-0019 §6 не взяли; шестого прогона на этих 25 кейсах не будет по правилу,
записанному до замера.

**Что при этом НЕ опровергнуто:** данные в базе OFF хорошие — на кейсах, где
карточка проходит подпись по имени, потолок 0,6 % ошибки. Непригоден **поиск**,
а не источник. Поэтому остаётся точный lookup по коду: матчинга в нём нет вовсе,
а контрольная сумма EAN (C4.0) отсекает неверно прочитанные коды. Цена решения —
~16 % позиций вместо проектных ~80 %; остальное ловит экран подтверждения.

Эндпоинт `api/v2/product/{code}` — не тот, что положил поиск: Perl-бэкенд отдаёт
503 на `cgi/search.pl` и `api/v2/search`, а lookup по коду работает. Поэтому
миграция на `search-a-licious` (ADR-0019 §2.1) отменена вместе с поиском.

Бесплатно, без ключа, READ без авторизации; обязателен кастомный User-Agent.
Имена полей `nutriments.*` подтверждены живым ответом OFF (curl, 2026-06-13,
api/v2/product/4690228004018 — «Домик в деревне 3,2 %»). Сетевой вызов
инъектируется (`fetch`) — в pytest живых запросов нет (§14).
"""

import logging

import aiohttp

from app.contexts.nutrition.resolve.checks import ean_checksum_ok
from app.contexts.nutrition.resolve.errors import SourceError
from app.contexts.nutrition.resolve.ports import Macros, ResolvedNutrition, ResolveQuery

logger = logging.getLogger(__name__)

# Поля ответа OFF. Минимум для маппинга: ни бренд, ни `completeness` больше не
# нужны — их читал скоринг выбора, а выбирать не из чего.
_FIELDS = "code,product_name,product_name_ru,nutriments"

# Одна сессия на процесс, а не на lookup (ТЗ §8.5, D14). Прежний код открывал
# `ClientSession` на каждый вызов, то есть платил TCP+TLS handshake за КАЖДУЮ
# позицию с валидным штрихкодом. Ленивая: `ClientSession` требует запущенного
# event-loop, а модуль импортируется до него. Резолвер собирается на каждый
# запрос (`build_resolver` в api-роуте), поэтому владеть сессией должен модуль,
# а не экземпляр источника.
_session: aiohttp.ClientSession | None = None


def _shared_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_shared_session() -> None:
    """Закрыть общую сессию (вызывается на shutdown из main.py)."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _num(nutriments: dict, key: str) -> float | None:
    """Число из nutriments по ключу; нечисло/отсутствие → None."""
    val = nutriments.get(key)
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _best_name(product: dict) -> str:
    """matched_name = product_name_ru ∥ product_name (§7.1)."""
    return (product.get("product_name_ru") or product.get("product_name") or "").strip()


def map_product(product: dict) -> Macros | None:
    """OFF product → Macros на 100 г (§7.1). Нет любого из 4 значений → None (skip).

    Фолбэк «ккал из кДж» сохранён: удалить его требовала §7.2 ТЗ, потому что в
    выдаче `search-a-licious` поля `energy-kj_100g` нет. Миграция на неё отменена
    вместе с поиском, а `api/v2` это поле отдаёт — требование отпало вместе со
    своим основанием. Деление на 4.184 здесь безопасно: это арифметика КОДА над
    числом карточки, а не пересчёт, доверенный модели (ср. дефект F-01).
    """
    nutriments = product.get("nutriments") or {}
    kcal = _num(nutriments, "energy-kcal_100g")
    if kcal is None:
        kj = _num(nutriments, "energy-kj_100g")
        kcal = round(kj / 4.184, 1) if kj is not None else None
    protein = _num(nutriments, "proteins_100g")
    fat = _num(nutriments, "fat_100g")
    carbs = _num(nutriments, "carbohydrates_100g")
    if kcal is None or protein is None or fat is None or carbs is None:
        return None
    return Macros(kcal=kcal, protein=protein, fat=fat, carbs=carbs)


class OpenFoodFactsSource:
    """Точный lookup OFF по штрихкоду → ResolvedNutrition|None (source=off).

    Единственная форма обращения к OFF. Ступень отвечает ТОЛЬКО когда товар
    найден по коду — «похожих товаров» она не предлагает ни при каких условиях
    (см. докстринг модуля).
    """

    name = "off"

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        timeout_s: float = 2.5,
        fetch=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        # Инъекция сетевого вызова: тесты подменяют фикстурой (живых запросов нет).
        self._fetch = fetch or self._default_fetch

    async def _default_fetch(self, barcode: str) -> dict | None:
        """Живой GET к OFF. HTTP≠200/404 → SourceError (pipeline ловит → estimate).

        404 — это НЕ сбой: OFF так отвечает на неизвестный код. Такой ответ
        обязан деградировать в тихий miss, иначе каждый товар, которого нет в
        базе, писал бы в лог ошибку источника.
        """
        url = f"{self.base_url}/api/v2/product/{barcode}"
        # Таймаут и User-Agent — на ЗАПРОС, а не на сессию: сессия общая на
        # процесс, а таймаут принадлежит этому источнику (`off_timeout_s`).
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        headers = {"User-Agent": self.user_agent}
        try:
            async with _shared_session().get(
                url, params={"fields": _FIELDS}, timeout=timeout, headers=headers
            ) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    raise SourceError(f"OFF HTTP {resp.status}")
                # OFF иногда отдаёт json как text/plain → content_type=None.
                return await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise SourceError(f"OFF network error: {e}") from e

    async def lookup(self, q: ResolveQuery) -> ResolvedNutrition | None:
        # C4.0 — предусловие сетевого вызова, а не постфильтр. Проверка стоит
        # здесь, а не только в `apply_checks`: ступень обязана быть безопасной у
        # любого вызывающего (раннер eval ходит мимо проверок), а неверно
        # прочитанный код — это чужой товар с наивысшим доверием `exact`.
        # В замере источника контрольная сумма отсеяла 6 кодов из 12 и ещё 2
        # внутримагазинных (`off-source-probe-2026-07-28.md` §2.4).
        if not ean_checksum_ok(q.barcode):
            return None

        data = await self._fetch(q.barcode)
        # status != 1 — «товара нет в базе». Это штатный miss, а не ошибка.
        if not data or data.get("status") != 1:
            logger.info("OFF: товар не найден по коду %s", q.barcode)
            return None

        product = data.get("product") or {}
        macros = map_product(product)
        if macros is None:
            # Карточка есть, КБЖУ неполные. Достраивать нечем — лучше None, чем
            # частичные числа: позиция уйдёт в estimate с числами модели.
            logger.info("OFF: карточка %s без полного КБЖУ", q.barcode)
            return None

        code = product.get("code") or q.barcode
        return ResolvedNutrition(
            per100g=macros,
            source="off",
            source_ref=f"{self.base_url}/product/{code}",
            matched_name=_best_name(product) or None,
            # Матч однозначен: код — это идентификатор товара, а не мера похожести.
            # Прежняя реализация клала сюда `completeness`, то есть полноту
            # заполнения карточки — одна из диагностированных причин, по которым
            # скоринг выбора не коррелировал с правильностью.
            confidence=1.0,
            match_confidence="exact",
        )
