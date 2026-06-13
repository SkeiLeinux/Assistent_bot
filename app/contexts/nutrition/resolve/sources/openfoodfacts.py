"""resolve/sources/openfoodfacts.py — Open Food Facts (spec-nutrition-retrieval §7).

Бесплатно, без ключа, READ без авторизации; обязателен кастомный User-Agent.
Дефолт — полнотекстовый `cgi/search.pl` (выбор зафиксирован за Code, §17). Штрихкод
(api/v2) — fast-follow, в R1 не вызывается (barcode не извлекаем).

Имена полей `nutriments.*` подтверждены живым ответом OFF (curl, 2026-06-13,
api/v2/product/4690228004018 — «Домик в деревне 3,2 %»): схема `energy-kcal_100g`,
`proteins_100g`, `fat_100g`, `carbohydrates_100g`, `energy-kj_100g`, плюс
`product_name(_ru)`, `brands`, `completeness`, `code` — совпала с §7.1 и фикстурами
(search.pl периодически отдаёт 503/пусто, но форма ответа идентична v2). Сетевой
вызов инъектируется (`fetch`) — в pytest живых запросов нет (§14).
"""

import logging

import aiohttp

from app.contexts.nutrition.resolve.errors import SourceError
from app.contexts.nutrition.resolve.ports import Macros, ResolvedNutrition, ResolveQuery

logger = logging.getLogger(__name__)

# Поля ответа OFF (минимум для маппинга и выбора). Подтверждены живым curl (см. модуль).
_FIELDS = (
    "code,product_name,product_name_ru,brands,quantity,"
    "nutrition_data_per,nutriments,completeness"
)


def _num(nutriments: dict, key: str) -> float | None:
    """Число из nutriments по ключу; нечисло/отсутствие → None."""
    val = nutriments.get(key)
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _norm(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def _tokens(value: str | None) -> list[str]:
    return [t for t in _norm(value).replace(",", " ").split() if t]


def _first_brand(brands_csv: str | None) -> str:
    """`brands` приходит CSV — берём первый бренд."""
    if not brands_csv:
        return ""
    return brands_csv.split(",")[0].strip()


def _best_name(product: dict) -> str:
    """matched_name = product_name_ru ∥ product_name (§7.1)."""
    return (product.get("product_name_ru") or product.get("product_name") or "").strip()


def map_product(product: dict) -> Macros | None:
    """OFF product → Macros на 100 г (§7.1). Нет любого из 4 значений → None (skip)."""
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


def select_best(products: list[dict], q: ResolveQuery) -> dict | None:
    """Детерминированный выбор лучшего матча (§7.2). Лучше None, чем не тот продукт.

    (1) оставить только с полными 4 значениями; (2)+(3) скоринг: бренд(3) +
    доля токенов name(2) + близость fat(бонус) + completeness(1); (4) топ-1.
    """
    candidates = [p for p in products if map_product(p) is not None]
    if not candidates:
        return None

    brand_norm = _norm(q.brand)
    name_tokens = set(_tokens(q.name))

    def score(product: dict) -> float:
        s = 0.0
        prod_brand = _norm(_first_brand(product.get("brands")))
        prod_name = _norm(_best_name(product))
        # (2) бренд: предпочесть содержащие, но не отбрасывать остальные
        if brand_norm and (brand_norm in prod_brand or brand_norm in prod_name):
            s += 3.0
        # доля совпавших токенов родового имени
        if name_tokens:
            prod_tokens = set(_tokens(_best_name(product)))
            s += 2.0 * (len(name_tokens & prod_tokens) / len(name_tokens))
        # (3) близость жирности (дискриминатор молочки)
        if q.fat_pct is not None:
            prod_fat = _num(product.get("nutriments") or {}, "fat_100g")
            if prod_fat is not None and abs(prod_fat - q.fat_pct) < 0.6:
                s += 1.0
        # полнота карточки OFF
        try:
            s += float(product.get("completeness") or 0.0)
        except (TypeError, ValueError):
            pass
        return s

    # стабильность: при равном скоре сохраняем исходный порядок выдачи OFF
    return max(candidates, key=score)


class OpenFoodFactsSource:
    """Полнотекстовый поиск OFF (`cgi/search.pl`) → ResolvedNutrition|None (source=off)."""

    name = "off"

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        lc: str = "ru",
        timeout_s: float = 2.5,
        page_size: int = 20,
        fetch=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.lc = lc
        self.timeout_s = timeout_s
        self.page_size = page_size
        # Инъекция сетевого вызова: тесты подменяют фикстурой (живых запросов нет).
        self._fetch = fetch or self._default_fetch

    def _search_terms(self, q: ResolveQuery) -> str:
        fat = f"{q.fat_pct:g}%" if q.fat_pct is not None else None
        return " ".join(p for p in (q.brand, q.name, fat) if p)

    async def _default_fetch(self, params: dict) -> dict | None:
        """Живой GET к OFF. HTTP≠200 → SourceError (pipeline ловит → следующая ступень)."""
        url = f"{self.base_url}/cgi/search.pl"
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        headers = {"User-Agent": self.user_agent}
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
                async with s.get(url, params=params) as resp:
                    if resp.status != 200:
                        raise SourceError(f"OFF HTTP {resp.status}")
                    # OFF иногда отдаёт json как text/plain → content_type=None.
                    return await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise SourceError(f"OFF network error: {e}") from e

    async def lookup(self, q: ResolveQuery) -> ResolvedNutrition | None:
        params = {
            "search_terms": self._search_terms(q),
            "search_simple": "1",
            "action": "process",
            "json": "1",
            "page_size": str(self.page_size),
            "lc": self.lc,
            "fields": _FIELDS,
        }
        data = await self._fetch(params)
        if not data:
            return None
        products = data.get("products") or []
        best = select_best(products, q)
        if best is None:
            logger.info("OFF: нет подходящего матча для %r", self._search_terms(q))
            return None
        macros = map_product(best)  # select_best гарантирует полноту
        code = best.get("code")
        return ResolvedNutrition(
            per100g=macros,
            source="off",
            source_ref=f"{self.base_url}/product/{code}" if code else None,
            matched_name=_best_name(best) or None,
            confidence=float(best.get("completeness") or 0.0),
        )
