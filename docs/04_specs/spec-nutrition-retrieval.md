# ТЗ — Nutrition: retrieval-augmented разбор КБЖУ (инкремент R)

**Версия:** v0.5 · **Дата:** 2026-06-14
**Статус:** ✅ R1 реализован (Claude Code, 2026-06-13/14; 225 тестов зелёные); сквозной live-прогон (LLM→OFF) — С. на VPS при приёмке
**Реализует:** ADR-0013 (принят С., 2026-06-13)
**Опирается на:** `04_specs/spec-nutrition.md` (домен Nutrition v0.2 — это дельта к нему); `06_program/feasibility-nutrition-web-search.md`; ADR-0004 (изоляция); ADR-0002 (ACL-философия источников); фактический код `app/contexts/nutrition/` (на 2026-06-13)
**Скоуп:** отдельный инкремент к уже отгруженному E1-Nutrition; вне скоупа текущего MVP-тестирования.
**Исполнение:** Claude Code (Windows venv), цикл edit→run→fix.

> **Цель документа — снять всю неоднозначность для реализации.** Если по ходу обнаружится развилка, не покрытая здесь, — НЕ домысливать: пометить `# TODO(spec)` и спросить С.

> **Без фича-флагов (С., 2026-06-13).** Резолвер всегда включён; надёжность — встроенной деградацией лестницы (источник не ответил → следующая → в пределе `estimate`). Откат — `git revert`. Web — этап R2 (кодом, §16).

> **Мульти-продуктовость (С., 2026-06-13).** `/analyze` (дневник) — **мульти-продуктовый**: один ввод (строка / фото-тарелка) → **СПИСОК позиций**; резолвер уточняет **КАЖДУЮ** позицию независимо. `/foods/analyze` (каталог) — **одно блюдо, расчётное** (без ретривала, `source=estimate`).

---

## 0. Что меняем в одном абзаце

Сейчас `/analyze` одним LLM-вызовом сегментирует ввод на продукты и для каждого даёт **оценку** КБЖУ (`_SYSTEM_PROMPT`, `items[]`). Числа модель берёт «из головы» — для брендовых продуктов неточно. Инкремент R сохраняет сегментацию и один LLM-вызов, но (а) расширяет извлечение полями идентификации (`brand/fat_pct/portion_g/barcode/label_complete`) и просит **per100g на 100 г** для каждой позиции, (б) вставляет **резолвер**, который для каждой позиции пытается заменить оценку на **точные данные из Open Food Facts** (а в R2 — из веба). Источник каждой позиции фиксируется в **провенансе** (`label`/`off`/`estimate`). Инвариант `analyze → confirm → save` (I5) сохраняется; найденное кэшируется в `SharedFood` при сохранении.

**Ключевой факт (комментарием в коде):** LLM сам не ищет в интернете. Retrieval — отдельные адаптеры (OFF; в R2 — Groq Compound). `llama-4-scout` используется для сегментации/извлечения (и в R2 — для парса веб-выдачи). «estimate» — это per100g, которое извлекатель уже вернул; **отдельного LLM-вызова на оценку нет**.

---

## 1. Целевое поведение

`/analyze` возвращает **список** позиций; резолвер применяется к каждой. Примеры:

| Кейс | Вход | Поведение |
|---|---|---|
| **1a. Текст, 1 продукт** | «200гр молоко Домик в деревне 3.2%» | 1 позиция: извлечь `{name,brand,fat_pct,portion_g, per100g(оценка), label_complete=false}` → OFF по `brand+name+fat` → нашли: per100g из OFF × 200/100, `source=off`; не нашли → оценка, `source=estimate`. |
| **1b. Текст, N продуктов** | «гречка 200, курица 150» | 2 позиции (сегментация в одном LLM-вызове). Каждая резолвится отдельно: «гречка»→OFF/estimate, «курица»→OFF/estimate. `test_text_input` сохраняется. |
| **2. Фото этикетки + «200г»** | изображение + portion | Обычно 1 позиция. Если этикетка читается на 100% → `per100g` точные с этикетки, `label_complete=true`, `source=label`. Иначе → OFF по названию → estimate. *(barcode — fast-follow, §16.)* |
| **3. Фото продукта / тарелки** | изображение | 1..N позиций. У каждой: `label_complete=true` (если своя этикетка видна) → label; иначе → OFF → estimate. |

Во всех случаях — существующий **confirm-экран** (список позиций; у каждой бейдж источника; правка работает), затем save.

---

## 2. Архитектура

Новый изолированный подпакет `app/contexts/nutrition/resolve/` (границы — ADR-0004; import-linter: `resolve` зависит от `services`/`models`, не наоборот; delivery — только через `FoodService`).

```
nutrition/
  resolve/
    __init__.py
    ports.py          # Protocol ProductResolver, NutritionSource; DTO ExtractedItem, ResolveQuery, ResolvedNutrition, Macros
    pipeline.py       # ResolverPipeline.refine(item) — лестница на ОДНУ позицию
    extractor.py      # LLM-сегментация+извлечение (vision/text) → list[ExtractedItem]   (1 вызов)
    sources/
      __init__.py
      openfoodfacts.py  # OpenFoodFactsSource (адаптер)
      estimate.py       # EstimateSource — возвращает per100g из самого item (БЕЗ LLM-вызова)
      # compound_web.py — добавляется в R2
    errors.py
    cache.py          # нормализация ключа; синергия со SharedFood — в FoodService
```

- **`NutritionSource`** — контракт ступени: `async def lookup(q: ResolveQuery) -> ResolvedNutrition | None`. Каждый источник — адаптер (как ACL, ADR-0002): добавить/сменить = новый адаптер (так R2-web встанет без правки лестницы).
- **`ProductResolver.refine(item: ExtractedItem) -> ResolvedNutrition`** (реализует `ResolverPipeline`): прогоняет ступени для ОДНОЙ позиции, первый успех — победитель; в пределе — `estimate` (per100g из item).
- **`extractor.extract(text|image) -> list[ExtractedItem]`** — один LLM-вызов сегментирует ввод и заполняет каждую позицию (§6).
- `FoodService` получает `ProductResolver` инъекцией (дефолт — реальный pipeline; тесты инжектят фейк — живых вызовов из pytest нет).
- Контракт `NutritionLLM` сохраняется (его использует `extractor`).
- **`/foods/analyze` резолвер НЕ использует** — остаётся текущий расчёт по составу (`_DISH_PROMPT`/`_COMBINED_PROMPT`), `source=estimate`.

---

## 3. Конвейер (точная последовательность)

```
# /analyze (дневник) — мульти-продукт
items = extractor.extract(text | image)        # 1 LLM-вызов → list[ExtractedItem]
out = []
for it in items:                               # резолв КАЖДОЙ позиции
    r = resolver.refine(it)                     # лестница §4 → ResolvedNutrition(+source)
    out.append(scale(r.per100g, it.portion_g) + {source, source_ref, matched_name})
return {"items": out}                           # БЕЗ сохранения (I5)
confirm (UI, список) → user правит/подтверждает
save_entries(items) → INSERT FoodEntry(+source,+source_ref) ×N
    → для позиций с source ∈ {label, off, web}: upsert SharedFood (кэш, §11)

# /foods/analyze (каталог) — одно блюдо, БЕЗ резолвера
result = current_dish_calc(name, weight, text?, images?)   # как сейчас
return {... , source: "estimate"}
```

Псевдокод refine одной позиции:

```python
async def refine(self, it: ExtractedItem) -> ResolvedNutrition:
    if it.label_complete:                       # этикетка прочитана — поиск не нужен
        return ResolvedNutrition(it.per100g, source="label", confidence=it.confidence)
    q = it.to_query()                           # name/brand/fat/barcode + fallback per100g (=it.per100g)
    for source in self.network_sources:         # R1: [OFF]   (R2: [OFF, CompoundWeb])
        try:
            r = await asyncio.wait_for(source.lookup(q), source.timeout_s)
        except (TimeoutError, SourceError) as e:
            log.warning("source %s failed: %s", source.name, e); continue
        if r is not None:
            return r
    return ResolvedNutrition(it.per100g, source="estimate", confidence=it.confidence)  # fallback = оценка extractor
```

Латентность: на ввод из N позиций — **1 LLM-вызов** + до N сетевых запросов (только не-`label` позиции). OFF-запросы выполнять с уважением к лимиту (≤10/мин): последовательно или семафором ≤2–3. Общий мягкий бюджет `resolver_total_budget_s` на весь ввод; если вышел — оставшиеся позиции отдаём как `estimate` (числа уже есть, без сети). Кэш (SharedFood) гасит повторные.

---

## 4. Лестница фолбэков (на ОДНУ позицию)

1. **label-short-circuit** — `label_complete` → `source=label` (per100g с этикетки).
2. **(fast-follow) barcode** — если `barcode` есть → OFF by barcode. *В R1 пропускается (barcode не извлекаем), §16.*
3. **OFF full-text** — `OpenFoodFactsSource.lookup` по `brand + name + fat_pct` (§7). Таймаут `off_timeout_s`.
4. **estimate** — per100g из самого `ExtractedItem` (оценка модели, полученная при извлечении), `source=estimate`. Всегда успешна — гарантия ответа.

> Ступень **web (Compound)** встаёт между 3 и 4 в **R2** (§8, §16).

Деградация: пустой/таймаут/ошибка ступени → лог `warning` + следующая. Это и есть замена «мастер-флага».

---

## 5. DTO-схемы (pydantic; `resolve/ports.py`)

```python
class Macros(BaseModel):
    kcal: float; protein: float; fat: float; carbs: float   # на 100 г

class ExtractedItem(BaseModel):
    name: str                      # «молоко питьевое»
    brand: str | None = None       # «Домик в деревне»
    fat_pct: float | None = None   # 3.2 (дискриминатор молочки)
    portion_g: float | None = None # масса этой порции; None если не указана
    barcode: str | None = None     # EAN, fast-follow
    per100g: Macros                # ВСЕГДА заполнено: с этикетки (label_complete) или оценка модели
    label_complete: bool = False   # этикетка прочитана на 100% → per100g точные
    confidence: float = 0.0

class ExtractResult(BaseModel):
    items: list[ExtractedItem]     # JSON-режим требует объект сверху

class ResolveQuery(BaseModel):     # вход ступени-источника
    name: str; brand: str | None; fat_pct: float | None; barcode: str | None
    fallback_per100g: Macros       # = ExtractedItem.per100g (для EstimateSource)

class ResolvedNutrition(BaseModel):
    per100g: Macros
    source: Literal["label","off","web","nutritionix","estimate"]   # web/nutritionix — R2/позже
    source_ref: str | None = None   # URL/код товара/цитата (off/web)
    matched_name: str | None = None # как назван матч в источнике
    confidence: float = 0.0
```

Масштаб на порцию — в `FoodService`: `value = per100g.* * portion_g/100`, округление как в текущем коде (kcal→1 знак). `NutritionItem` (существующий) += `source: str`, `source_ref: str | None`.

**Опция C (С., 2026-06-14) — пересчёт на confirm при правке веса.** `/analyze` отдаёт на каждой позиции ещё и `per100g` (транспорт, не персистится); confirm-экран пересчитывает КБЖУ при изменении веса. Это закрывает прежний edge `portion_g=None`: начально числа = per100g, вес пуст; ввод веса даёт корректный масштаб. `NutritionItem` += `kcal_100g/protein_100g/fat_100g/carbs_100g` (транспорт).

Валидация: `name` непустой; числа `≥ 0`; невалидный ответ источника → «не найдено» (None), не краш.

---

## 6. Извлечение (`extractor.py`) — сегментация + идентификация (1 LLM-вызов)

Один извлекатель, два входа (текст / изображение), выход — `list[ExtractedItem]` (строгий JSON). Использует `NutritionLLM` (Scout). Для фото — vision-часть; для текста — текстовая. Заменяет нынешний прямой `_SYSTEM_PROMPT`-путь в `/analyze`.

> **Фронт:** мультифото-аплоадер уже на проде (#2) — фото-ветка переиспользует, заново не делает.

**Промпт извлечения (system) — дословно, целевой:**

```
Ты — парсер дневника питания. Раздели запрос (текст и/или фото) на ОТДЕЛЬНЫЕ продукты/блюда и для КАЖДОГО верни запись.
Верни СТРОГО JSON-объект:
{"items": [ {"name":"<родовое название без бренда, рус.>","brand":<строка|null>,"fat_pct":<число|null>,"portion_g":<число|null>,"barcode":<строка цифр|null>,"per100g":{"kcal":<ч>,"protein":<ч>,"fat":<ч>,"carbs":<ч>},"label_complete":<true|false>,"confidence":<0..1>}, ... ]}
Правила:
- Одна запись на каждый отдельный продукт/блюдо. Пример: «гречка 200, курица 150» → ДВЕ записи.
- per100g — ВСЕГДА заполни, значения на 100 г: если на фото читается этикетка — точные значения с неё и label_complete=true; иначе — твоя оценка и label_complete=false.
- name — без бренда и жирности («молоко питьевое»); brand — отдельно («Домик в деревне»); fat_pct — если указан.
- portion_g — масса именно этой порции, если понятна из запроса; иначе null.
- barcode — только если цифры штрихкода явно видны; иначе null.
- Числа без единиц. Никакого текста вне JSON.
```

> Для текста модель сегментирует и оценивает (как нынешний `_SYSTEM_PROMPT`, плюс поля идентификации). Для фото этикетки — обычно одна запись с `label_complete=true`. Для фото тарелки — несколько записей с `label_complete=false`.

---

## 7. Источник — Open Food Facts (`sources/openfoodfacts.py`)

Бесплатно, **без ключа**, обязателен кастомный **User-Agent**. READ без авторизации.

**Эндпоинты:**
- Полнотекст (R1): `GET {off_base_url}/cgi/search.pl?search_terms=<brand name fat>&search_simple=1&action=process&json=1&page_size=20&lc=ru&fields=code,product_name,product_name_ru,brands,quantity,nutrition_data_per,nutriments,completeness` → `{count, products:[...]}`.
- Штрихкод (fast-follow): `GET {off_base_url}/api/v2/product/{barcode}.json?fields=<те же>` → `{status, product}`.
- (альт.) Search-a-licious: `GET https://search.openfoodfacts.org/search?q=<...>`. **Движок — за Code; дефолт `cgi/search.pl`**, зафиксировать в коде.

> **Локаль:** для RU добавлять `lc=ru` (при необходимости субдомен `ru.openfoodfacts.org`). Поведение `lc/cc` **подтвердить живым запросом**. Покрытие основных продуктов проверено С. (2026-06-13) — достаточное.

**Заголовки:** `User-Agent: {off_user_agent}` (напр. `lifeManager_bot/0.4 (<admin_email>)`).
**Лимиты:** ~10/мин (поиск), ~15/мин (товар) — сериализация/семафор + кэш (§11). Таймаут `off_timeout_s`.

### 7.1 Маппинг полей OFF → `Macros` (на 100 г)

| Наш `Macros` | OFF (`nutriments.*`) | Если отсутствует |
|---|---|---|
| `kcal` | `energy-kcal_100g` | взять `energy-kj_100g` / 4.184; нет и его → продукт невалиден (skip) |
| `protein` | `proteins_100g` | нет → skip продукт |
| `fat` | `fat_100g` | нет → skip продукт |
| `carbs` | `carbohydrates_100g` | нет → skip продукт |

`matched_name` = `product_name_ru` ∥ `product_name`; `source_ref` = `{off_base_url}/product/{code}`; бренд из `brands` (CSV → первый).

> Имена полей — по документированной схеме OFF; **подтвердить на реальном ответе при реализации** (`curl` в реальном venv). Расхождение — поправить и зафиксировать.

### 7.2 Выбор лучшего матча (детерминированно)

Из `products[]`: (1) оставить с полными 4 значениями; (2) если задан `brand` — предпочесть содержащие бренд (ci, ё→е), но не отбрасывать остальные; (3) скоринг ↓: бренд (3) + доля токенов `name` (2) + близость `fat_pct` (|Δ|<0.6 → бонус) + `completeness` (1); (4) топ-1; если после (1) пусто → `None` (→ следующая ступень). Без fuzzy-«на глаз» слияния (как ADR-0002): лучше «не найдено», чем не тот продукт.

---

## 8. Веб через Groq Compound — ЭТАП R2 (не входит в R1)

Добавляется кодом после R1 как ещё один `NutritionSource` (`sources/compound_web.py`), встаёт в лестницу между OFF и estimate. Без рантайм-флага — этапность кода. Модель `groq_compound_model` (деф. `groq/compound`). Вызов — `chat.completions.create`; Compound сам ищет в вебе (Tavily) и отдаёт цитаты.

**Промпт (system), дословно (для R2):**

```
Ты — нутрициолог с доступом к веб-поиску. Найди КБЖУ указанного продукта на 100 г из надёжного источника (сайт производителя, маркетплейс, база продуктов).
Верни СТРОГО JSON: {"per100g": {"kcal":<ч>,"protein":<ч>,"fat":<ч>,"carbs":<ч>}, "matched_name": "<что нашёл>", "source_ref": "<URL>"}
Если достоверно не нашёл — верни {"per100g": null}. Никакого текста вне JSON. Числа без единиц, на 100 г.
```

**User:** `Продукт: <brand> <name> <fat_pct>%`. Таймаут `compound_timeout_s`. Парс — `_extract_json`; `per100g==null` → `None` (→ estimate). `source="web"`.

---

## 9. EstimateSource (`sources/estimate.py`) — fallback без LLM

НЕ делает нового LLM-вызова. Возвращает `ResolvedNutrition(per100g = q.fallback_per100g, source="estimate")` — то есть оценку, которую `extractor` уже положил в `ExtractedItem.per100g`. Всегда последняя, всегда успешна → гарантия ответа и обратная совместимость (если OFF/веб ничего не дали, цифры = сегодняшняя оценка модели).

---

## 10. Контракт API (дельта) — синкнут в `openapi.yaml`

`/analyze` — каждая позиция получает провенанс; `/foods/analyze` — `source=estimate` (расчёт, без ретривала). `openapi.yaml` уже синхронизирован (`ProvenanceSource`, `source`/`source_ref`/`matched_name`).

**`POST /api/v1/nutrition/analyze`** →
```json
{"items": [
  {"name":"молоко Домик в деревне 3.2%","w":200,"kcal":118,"p":5.8,"f":6.4,"c":9.4,
   "source":"off","source_ref":"https://world.openfoodfacts.org/product/...","matched_name":"Молоко питьевое 3,2%"},
  {"name":"гречка","w":200,"kcal":280, "source":"estimate"}
]}
```
**`POST /api/v1/nutrition/foods/analyze`** → как сейчас + `"source":"estimate"`.
**`POST /api/v1/nutrition/entries`** → принять/сохранить `source`/`source_ref` на каждую запись (`source_ref` — новая nullable-колонка; `source` уже есть, семантика → провенанс `ProvenanceSource`).

Короткие имена полей фронта (`name/w/kcal/p/f/c`) сохраняются; добавляются `source`, `source_ref`, `matched_name`.

---

## 11. Кэш и синергия со SharedFood

- Кэшируем **при сохранении записи** (`save_entries`), для позиций с `source ∈ {label, off, web}` (НЕ `estimate`): upsert `SharedFood` (FAMILY).
- **Ключ дедупа:** нормализованный `(brand + name + fat_pct)` (lower, trim, ё→е, схлоп пробелов) ⊕ `barcode` если есть. Перед созданием — поиск по ключу; есть → не дублировать (можно `use_count++`).
- Эффект: повтор того же продукта → мгновенный пик из «Базы», без LLM и сети. Каталог = самонаполняющийся кэш.
- Видимость: дневник PERSONAL, каталог FAMILY (вне семьи кэш пропускаем; запись в дневник идёт).

---

## 12. Конфиг (`app/config.py`, `.env`) — R1

Фича-флагов нет. Только источник и таймауты:

| Ключ | Тип/деф. | Назначение |
|---|---|---|
| `off_base_url` | str = `https://world.openfoodfacts.org` | база OFF |
| `off_user_agent` | str (**обязателен**) | напр. `lifeManager_bot/0.4 (<admin_email>)` |
| `off_lc` | str = `ru` | локаль поиска |
| `off_timeout_s` | float = 2.5 | таймаут запроса к OFF |
| `resolver_total_budget_s` | float = 12.0 | мягкий бюджет на сетевые ступени всего ввода |

**R2 добавит:** `groq_compound_model` (`groq/compound`), `compound_timeout_s` (8.0).

---

## 13. Ошибки, деградация, латентность

| Ступень | Сбой | Действие |
|---|---|---|
| extractor (LLM) | нет ключа/429/кривой JSON | как сейчас: `LLMNotConfigured`/`LLMRateLimit`/`LLMError` → REST 502/понятный текст |
| OFF | таймаут/HTTP≠200/пусто/нет матча | лог `warning`, `None` → следующая ступень |
| (R2) Compound | таймаут/ошибка/`per100g=null` | лог `warning`, `None` → estimate |
| estimate | — | всегда успешна (per100g из item) |

Мульти-латентность: 1 LLM-вызов + ≤N OFF-запросов (только не-`label`), последовательно/семафор ≤2–3 (лимит OFF 10/мин); бюджет `resolver_total_budget_s` на весь ввод — при превышении остаток позиций отдаём `estimate` без сети. Никогда не показываем `estimate`-число как «точное».

---

## 14. Тест-план (без живых вызовов в pytest)

**Фейки:** `FakeProductResolver`, `FakeNutritionSource`, существующий фейк `NutritionLLM`. Живых OFF/Groq в тестах нет.
**Фикстуры:** 2–3 реальных JSON OFF в `tests/fixtures/off_*.json` (снять `curl`-ом при реализации) → юнит маппинга и выбора матча без сети.

**Матрица:**
- extractor: «гречка 200, курица 150» → **2** `ExtractedItem` с `per100g` и `portion_g` (регресс `test_text_input`); «200гр молоко Домик в деревне 3.2%» → 1 item с `brand="Домик в деревне", fat_pct=3.2`; фото-этикетка → `label_complete=true`; фото тарелки → N items, `label_complete=false`.
- refine (одна позиция): label-short-circuit; OFF hit → off; OFF miss → estimate(=item.per100g); таймаут OFF → estimate. *(R2: OFF miss→web hit; web miss→estimate.)*
- мульти-ввод: список из 2 → одна позиция off, другая estimate; порядок и число сохранены.
- OFF маппинг: `energy-kcal_100g`→kcal; kj→пересчёт; нет белка→skip; выбор лучшего матча.
- масштаб: per100g × portion/100 на каждую позицию; округление = текущему.
- кэш: save с `source=off` → SharedFood создан; повтор ключа → без дубля; `source=estimate` → не создаём; вне семьи → не создаём, дневник пишется.
- контракт: `/analyze` отдаёт `source/source_ref` на позицию; `/entries` сохраняет; `/foods/analyze` → `source=estimate`.
- бюджет: при превышении `resolver_total_budget_s` остаток позиций → estimate без сетевых вызовов.

---

## 15. Критерии приёмки (DoD)

- [x] `/analyze` мульти: «гречка 200, курица 150» → 2 позиции, каждая со своим `source`; `test_text_input` адаптирован (`test_nutrition.py`).
- [x] Кейсы 1–3 дают корректный `source` (`label`/`off`/`estimate`) и масштаб; off-ветка проверена на реальной фикстуре «молоко Домик в деревне 3.2%» (`off_milk_search.json`). *Сквозной live (реальный LLM→OFF) — С. на VPS при приёмке.*
- [x] refine деградирует по §4 (miss/таймаут/ошибка → следующая; estimate всегда отвечает); бюджет соблюдается (`test_nutrition_resolver.py`).
- [x] confirm-экран показывает источник на позицию (`SourceBadge`, screens.jsx); правка/сейв сохраняют `source`/`source_ref`.
- [x] `/foods/analyze` → `source=estimate`, ретривал не вызывается (поведение как сейчас + поле source).
- [x] Кэш в SharedFood по §11 (создание, дедуп, skip estimate, skip вне семьи).
- [x] Маппинг OFF подтверждён на живом ответе (curl 2026-06-13, api/v2/product/4690228004018); имена полей совпадают.
- [x] Тесты на фейках/фикстурах зелёные (225 passed); `import-linter` зелёный (контракт «resolve — leaf within Nutrition»).
- [x] gate-тест RU-покрытия OFF пройден (С., 2026-06-13).
- [x] `openapi.yaml` синкнут; changelog обновлён.

---

## 16. Поэтапность внутри инкремента

- **R1:** extractor (мульти, текст+фото) → refine per-item (label/OFF/estimate) → провенанс → кэш SharedFood → контракт-дельта → тесты. Web и barcode НЕ входят. `/foods/analyze` — расчётный.
- **R2:** добавить `CompoundWebSource` + конфиг; вставить в лестницу между OFF и estimate (§8).
- **R-fast-follow (barcode):** чтение EAN с фото (клиент `ZXing/quagga` или сервер `pyzbar`) → шаг 2 (OFF by barcode).
- **Позже (опц.):** Nutritionix/USDA для generic — тот же порт.

---

## 17. Зафиксированные решения

- **Мульти-продуктовость** (С., 2026-06-13): `/analyze` — список позиций, резолв per-item; `/foods/analyze` — одно блюдо, расчётное (`source=estimate`), без ретривала.
- **`estimate` — не отдельный LLM-вызов**, а per100g, пришедшая из извлекателя (1 вызов на ввод).
- **Фича-флаги убраны**: резолвер всегда вкл.; надёжность — деградацией; web — этап R2 кодом.
- Авто-`SharedFood` — при сохранении записи; провенанс показываем; web (R2) — с цитатой.
- Полнотекст OFF — дефолт `cgi/search.pl` (Code фиксирует выбор).
- Фиксы #1 (`add_explicit`) и #2 (мультифото) — **уже в проде**, из инкремента исключены.

## 18. Changelog
- **v0.5 (2026-06-14) — решения С. по открытым вопросам R1.** (1) `portion_g=None` → **опция C**: `/analyze` отдаёт `per100g` на позицию (транспорт, не персистится), confirm-экран пересчитывает КБЖУ при правке веса (`NutritionItem += kcal/protein/fat/carbs_100g`; сериализатор `_item` отдаёт `per100g`; `screens.jsx` пересчёт в `update`). (2) `off_user_agent` — дефолт-заглушка оставлена, значение `lifeManager_bot (skyline987650@gmail.com)`. (3) Семантика `source`→провенанс — оставлена как есть. (4) Дедуп явного тумблера «в базу» — оставлен. openapi `FoodItemDraft += per100g`.
- **v0.4 (2026-06-13) — R1 реализован (Claude Code).** Пакет `app/contexts/nutrition/resolve/` (ports/errors/cache/pipeline/extractor/sources{openfoodfacts,estimate}). Лестница `refine`: label→OFF→estimate (web/barcode/флагов нет). `extractor` — §6 дословно, мульти-вывод (1 LLM-вызов). Резолвер/извлекатель собираются в композиционном корне (api-роут) и инжектятся → `services` НЕ импортирует `resolve`; новый import-linter контракт «resolve — leaf within Nutrition» (8/8 KEPT). DTO §5; OFF маппинг/выбор §7 (поля подтверждены живым curl к api/v2, search.pl периодически 503 — форма ответа идентична). `NutritionItem` += `source/source_ref/matched_name` (matched_name не персистится); `FoodEntry` += `source_ref`, `source` 10→20 (миграция `e7a1c0ffee01`, single head). Кэш SharedFood §11 (дедуп по нормализованному имени, skip estimate/вне семьи). `/foods/analyze` → `source=estimate`. Фронт: `SourceBadge` на confirm-экране + протаскивание `source/source_ref` в сейв. Тесты: `test_nutrition_resolver.py` (28) + адаптированы `test_nutrition.py`/`test_nutrition_api.py`; всего 225 passed, без живых вызовов. **Открытые `# TODO(spec)` для С.:** (1) `portion_g=None` — теперь числа = per100g, вес None (раньше модель прикидывала порцию-абсолют); (2) `off_user_agent` дан с дефолтом (не hard-required), чтобы не ронять boot — на проде переопределить в `.env`.
- **v0.3 (2026-06-13):** примирён ретривал с мульти-продуктовым `/analyze` — extractor возвращает **список** позиций (1 LLM-вызов, сегментация как `_SYSTEM_PROMPT` + идентификация + `per100g`), резолвер `refine` применяется к КАЖДОЙ позиции; `estimate` = per100g из item (без доп. вызова). `/foods/analyze` зафиксирован расчётным (`source=estimate`, без ретривала). DTO: `ExtractedItem`/`ExtractResult`/`ResolveQuery`. Обновлены §0–§6, §9, §10, §13–§17.
- **v0.2 (2026-06-13):** убраны фича-флаги; web → этап R2; gate-тест OFF пройден; учтено #1/#2 в проде; openapi синкнут.
- **v0.1 (2026-06-13):** первичная редакция по ADR-0013.
