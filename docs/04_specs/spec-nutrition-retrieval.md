# ТЗ — Nutrition: retrieval-augmented разбор КБЖУ (инкремент R)

**Версия:** v0.1 (draft для реализации) · **Дата:** 2026-06-13
**Статус:** на реализацию (Code), после приёмки С.
**Реализует:** ADR-0013 (принят С., 2026-06-13)
**Опирается на:** `04_specs/spec-nutrition.md` (домен Nutrition v0.2 — это дельта к нему); `06_program/feasibility-nutrition-web-search.md`; ADR-0004 (изоляция); ADR-0002 (ACL-философия источников); фактический код `app/contexts/nutrition/` (на 2026-06-13)
**Скоуп:** отдельный инкремент к уже отгруженному E1-Nutrition; вне скоупа текущего MVP-тестирования.
**Исполнение:** Claude Code (Windows venv), цикл edit→run→fix.

> **Цель документа — снять всю неоднозначность для реализации.** Если по ходу обнаружится развилка, не покрытая здесь, — НЕ домысливать: пометить `# TODO(spec)` и спросить С., не «решать молча».

---

## 0. Что меняем в одном абзаце

Сейчас `POST /analyze` и `POST /foods/analyze` считают КБЖУ **только оценкой модели** (`llama-4-scout`) — числа берутся «из головы», для брендовых продуктов неточно. Инкремент R вставляет **ступень получения данных (retrieval)** между распознаванием и ответом: модель извлекает *что это за продукт*, затем детерминированная **лестница источников** добывает реальные КБЖУ (Open Food Facts → веб через Groq Compound → оценка моделью как последний фолбэк), масштабирует на порцию и возвращает с **провенансом** (откуда цифры). Инвариант `analyze → confirm → save` (I5) сохраняется; найденное кэшируется в `SharedFood` при сохранении записи.

**Ключевой факт (зафиксировать в коде комментарием):** LLM сам не ищет в интернете. Retrieval — отдельные адаптеры; веб даёт Groq **Compound** (встроенный поиск на Tavily). Текущая `llama-4-scout` используется для распознавания/извлечения и для парса веб-выдачи.

---

## 1. Целевое поведение по трём кейсам

| Кейс | Вход | Поведение |
|---|---|---|
| **1. Текст** | «200гр молоко Домик в деревне 3.2%» | Извлечь `{name, brand, fat_pct, portion_g}` → OFF полнотекст → если найдено: КБЖУ на 100 г × (200/100) → вернуть `source=off`. Не найдено → веб (Compound) → не найдено → оценка модели (`source=estimate`). |
| **2. Фото этикетки + «200г»** | изображение + portion | Vision-извлечение `{name, brand, fat_pct, portion_g, per100g?, label_complete}`. Если `label_complete` (этикетка прочитана на 100%) → берём цифры с этикетки × порция, `source=label`. Иначе → OFF-поиск по названию → веб → оценка. *(barcode-ступень — fast-follow, §16.)* |
| **3. Фото продукта (название/этикетка)** | изображение (+опц. portion) | Vision-извлечение. Если КБЖУ с этикетки извлеклись полностью → масштабировать, `source=label`. Если нет → извлечь название → OFF/веб → масштабировать/оценить. **Ровно лестница ниже.** |

Во всех кейсах результат проходит через существующий **confirm-экран** (пользователь видит цифры **и источник**, может править), затем save.

---

## 2. Архитектура

Новый изолированный подпакет `app/contexts/nutrition/resolve/` (границы — по ADR-0004; импорт-линтер: `resolve` зависит от `services`/`models`, не наоборот; delivery не импортирует resolve напрямую — только через `FoodService`).

```
nutrition/
  resolve/
    __init__.py
    ports.py          # Protocol ProductResolver, NutritionSource; DTO ExtractedProduct, ResolvedNutrition
    pipeline.py       # ResolverPipeline — оркестрация лестницы (extract→resolve→scale)
    extractor.py      # LLM-извлечение продукта (vision/text) → ExtractedProduct
    sources/
      __init__.py
      openfoodfacts.py  # OpenFoodFactsSource (адаптер)
      compound_web.py   # CompoundWebSource (адаптер, Groq Compound)
      estimate.py       # EstimateSource (обёртка над текущим LLMService — последний фолбэк)
    errors.py
    cache.py          # нормализация ключа + (опц.) кэш; синергия со SharedFood — в FoodService
```

- **`NutritionSource`** — общий контракт ступени: `async def lookup(query: ResolveQuery) -> ResolvedNutrition | None`. Каждый источник — адаптер (как ACL в ADR-0002): добавить/сменить источник = новый адаптер, домен и контракт не трогаем.
- **`ProductResolver`** (порт) реализуется `ResolverPipeline`: прогоняет ступени по порядку, первый непустой результат — победитель.
- `FoodService` получает `ProductResolver` инъекцией (дефолт — реальный pipeline; тесты инжектят фейк — живых вызовов из pytest нет, как сейчас с `NutritionLLM`).
- Контракт `NutritionLLM` сохраняется (его использует `extractor` и `EstimateSource`).

---

## 3. Конвейер (точная последовательность)

```
analyze(request)                      # /analyze или /foods/analyze
  → ExtractedProduct = extractor.extract(text | image, portion_g)
  → ResolvedNutrition = resolver.resolve(ExtractedProduct)   # лестница, §4
  → scale to portion_g → NutritionItem(+source, +source_ref)
  → вернуть фронту (БЕЗ сохранения, I5)
confirm (UI) → user правит/подтверждает
save_entries(...) → INSERT FoodEntry(+source)
  → если source ∈ {off, web, label} → upsert SharedFood (кэш, §11)
```

Псевдокод pipeline:

```python
async def resolve(self, p: ExtractedProduct) -> ResolvedNutrition:
    deadline = monotonic() + settings.resolver_total_budget_s
    # 0. этикетка прочитана полностью — поиск не нужен
    if p.label_complete and p.per100g is not None:
        return ResolvedNutrition(per100g=p.per100g, source="label", confidence=p.confidence)
    for source in self.sources:           # [OFF, CompoundWeb, Estimate]
        if monotonic() > deadline and source.name != "estimate":
            continue                       # бюджет вышел — пропускаем сетевые, но estimate всегда отрабатывает
        try:
            r = await asyncio.wait_for(source.lookup(p.to_query()), source.timeout_s)
        except (TimeoutError, SourceError) as e:
            log.warning("source %s failed: %s", source.name, e); continue
        if r is not None:
            return r
    raise ResolverError("no КБЖУ resolved")   # практически недостижимо: estimate почти всегда возвращает
```

Порядок `self.sources`: `[OpenFoodFactsSource, CompoundWebSource(if enabled), EstimateSource]`. `EstimateSource` — всегда последний и всегда включён (гарантирует ответ).

---

## 4. Лестница фолбэков (нормативный алгоритм)

1. **label-short-circuit** — `label_complete and per100g present` → `source=label`. (Кейсы 2/3, удачная ветка.)
2. **(fast-follow) barcode** — если `barcode` есть → OFF by barcode. *В первом срезе шаг пропускается (barcode не извлекаем), см. §16.*
3. **OFF full-text** — `OpenFoodFactsSource.lookup` по `brand + name + fat_pct` (§8.1). Таймаут `off_timeout_s` (деф. 2.5 c).
4. **web (Compound)** — `CompoundWebSource.lookup` (§8.2), только если `nutrition_web_lookup_enabled`. Таймаут `compound_timeout_s` (деф. 8 c).
5. **estimate** — `EstimateSource` (текущий путь через `_SYSTEM_PROMPT`/`_DISH_PROMPT`), `source=estimate`.

Деградация: пустой результат/таймаут/ошибка ступени → лог `warning` + переход к следующей. Никаких исключений наружу до estimate.

---

## 5. DTO-схемы (pydantic; `resolve/ports.py`)

```python
class ExtractedProduct(BaseModel):
    name: str                      # «молоко питьевое 3.2%»
    brand: str | None = None       # «Домик в деревне»
    fat_pct: float | None = None   # 3.2  (важный дискриминатор для молочки)
    portion_g: float | None = None # 200; None если пользователь не указал
    barcode: str | None = None     # EAN, fast-follow
    per100g: "Macros | None" = None    # заполнено ТОЛЬКО если label_complete
    label_complete: bool = False   # этикетка прочитана на 100% (kcal+Б+Ж+У)
    confidence: float = 0.0        # 0..1, самооценка модели

class Macros(BaseModel):
    kcal: float; protein: float; fat: float; carbs: float

class ResolvedNutrition(BaseModel):
    per100g: Macros
    source: Literal["label","off","web","nutritionix","estimate"]
    source_ref: str | None = None   # URL/код товара/цитата (для off/web)
    matched_name: str | None = None # как назван матч в источнике (для UI/доверия)
    confidence: float = 0.0
```

Масштабирование на порцию — в `FoodService` (как сейчас `create_entry_from_shared`): `value = per100g * portion_g/100`, округление как в текущем коде (kcal→1 знак). `NutritionItem` (существующий) расширяется полями `source: str` и `source_ref: str | None`.

Валидация: `name` непустой; числа `>= 0`; `per100g.kcal` обязателен у `ResolvedNutrition`. Невалидный ответ источника → трактуется как «не найдено» (None), не как краш.

---

## 6. Извлечение продукта (`extractor.py`)

Один извлекатель, два входа (текст / изображение), один выход — `ExtractedProduct` (строгий JSON). Использует `NutritionLLM` (Scout). Для фото — vision-часть (как `analyze_photo` сейчас); для текста — текстовая.

**Промпт извлечения (system) — дословно, целевой:**

```
Ты — парсер продуктов питания. Извлеки из запроса (текст и/или фото) описание ОДНОГО продукта для последующего поиска КБЖУ.
Верни СТРОГО JSON-объект:
{"name": "<родовое название без бренда, рус.>", "brand": "<бренд или null>", "fat_pct": <число или null>, "portion_g": <число или null>, "barcode": "<цифры EAN или null>", "per100g": {"kcal":<ч>,"protein":<ч>,"fat":<ч>,"carbs":<ч>} | null, "label_complete": <true|false>, "confidence": <0..1>}
Правила:
- name — без бренда и жирности (например "молоко питьевое"), brand — отдельно ("Домик в деревне").
- fat_pct — процент жирности, если указан (важно для молочных продуктов).
- portion_g — масса порции, если её можно понять из запроса; иначе null.
- per100g заполняй ТОЛЬКО если на фото этикетки полностью читаются все четыре значения (ккал, белки, жиры, углеводы) на 100 г; иначе per100g=null и label_complete=false.
- label_complete=true ТОЛЬКО когда per100g заполнен и достоверен.
- Никакого текста вне JSON. Числа без единиц.
```

> На фото этикетки модель кладёт КБЖУ в `per100g` только при полной читаемости → даёт label-short-circuit (кейс 3). Частичная этикетка → `per100g=null`, идём искать.

---

## 7. Источник 1 — Open Food Facts (`sources/openfoodfacts.py`)

Бесплатно, **без ключа**, нужен кастомный **User-Agent** (обязателен). READ не требует авторизации.

**Эндпоинты:**
- Полнотекст (первый срез): `GET {off_base_url}/cgi/search.pl` c query:
  `search_terms=<brand name fat>&search_simple=1&action=process&json=1&page_size=20&lc=ru&fields=code,product_name,product_name_ru,brands,quantity,nutrition_data_per,nutriments,completeness`
  Ответ: `{count, page, page_size, products: [ {...}, ... ]}`.
- Штрихкод (fast-follow): `GET {off_base_url}/api/v2/product/{barcode}.json?fields=<те же>` → `{status, product:{...}}` (`status==1` если найден).
- (альт.) Search-a-licious: `GET https://search.openfoodfacts.org/search?q=<...>&page_size=20`. **Выбор движка полнотекста — за Code; дефолт `cgi/search.pl`.** Зафиксировать выбор в коде.

> **Локаль:** для RU-брендов добавлять `lc=ru` (и при необходимости пробовать субдомен `ru.openfoodfacts.org`). Точное поведение `lc/cc` **подтвердить живым запросом при реализации** (см. §15 gate-тест).

**Заголовки:** `User-Agent: {off_user_agent}` (например `lifeManager_bot/0.4 (<admin_email>)`). Без него OFF может расценить как бота.
**Лимиты:** ~10 запросов/мин (поиск), ~15/мин (товар). Сериализовать + кэш (§11). Таймаут `off_timeout_s`.

### 7.1 Маппинг полей OFF → `Macros` (на 100 г)

| Наш `Macros` | OFF (`nutriments.*`) | Если отсутствует |
|---|---|---|
| `kcal` | `energy-kcal_100g` | взять `energy-kj_100g` / 4.184; если и его нет → продукт невалиден (skip) |
| `protein` | `proteins_100g` | если нет → skip продукт |
| `fat` | `fat_100g` | если нет → skip продукт |
| `carbs` | `carbohydrates_100g` | если нет → skip продукт |

Идентификация: `matched_name` = `product_name_ru` ∥ `product_name`; `source_ref` = `{off_base_url}/product/{code}`; бренд из `brands` (CSV → первый).

> **Имена полей — по документированной схеме OFF; подтвердить на реальном ответе при реализации** (мой web-fetch не вернул JSON в этой среде; Code в реальном venv проверит `curl`-ом — это допустимо исполнителю). Если поле названо иначе у конкретной локали — поправить таблицу и зафиксировать.

### 7.2 Правило выбора лучшего матча (детерминированное)

Из `products[]`:
1. отфильтровать те, где присутствуют все 4 значения на 100 г (после правил выше);
2. отсев по бренду: если в запросе есть `brand` — оставить продукты, где `brands` содержит бренд (case-insensitive, нормализация пробелов/ё→е); если таких нет — не отбрасывать (бренд мог не совпасть по написанию);
3. скоринг по убыванию: совпадение бренда (вес 3) + доля совпавших токенов `name` (вес 2) + близость `fat_pct` (если задан; |Δ|<0.6 → бонус) + `completeness` (вес 1);
4. взять топ-1; если после п.1 пусто → вернуть `None` (не найдено → следующая ступень).

Никакого fuzzy-«на глаз» слияния (та же осторожность, что в ADR-0002): лучше отдать «не найдено», чем взять не тот продукт.

---

## 8. Источник 2 — веб через Groq Compound (`sources/compound_web.py`)

Включается флагом `nutrition_web_lookup_enabled` (деф. **false**, пока не протестируем). Модель `groq_compound_model` (деф. `groq/compound`; легче — `groq/compound-mini`). Вызов — обычный `chat.completions.create` с этой моделью; Compound сам делает веб-поиск (Tavily) и возвращает ответ + цитаты в метаданных.

**Промпт (system) — дословно, целевой:**

```
Ты — нутрициолог с доступом к веб-поиску. Найди КБЖУ указанного продукта на 100 г из надёжного источника (сайт производителя, маркетплейс, база продуктов).
Верни СТРОГО JSON: {"per100g": {"kcal":<ч>,"protein":<ч>,"fat":<ч>,"carbs":<ч>}, "matched_name": "<что нашёл>", "source_ref": "<URL источника>"}
Если достоверно не нашёл — верни {"per100g": null}. Никакого текста вне JSON. Числа без единиц, на 100 г.
```

**User:** `Продукт: <brand> <name> <fat_pct>%`. Таймаут `compound_timeout_s`.
Парс ответа: тем же `_extract_json` (есть в `llm_service.py`); если `per100g==null` → вернуть `None` (→ estimate). `source="web"`, `source_ref` из ответа (или из citations Compound, если в JSON пусто).

> Если у Compound окажется доступен приём изображений — можно слить извлечение+поиск для фото в один вызов; **пока не подтверждено**, поэтому фото = Scout-извлечение → Compound по тексту (2 ступени).

---

## 9. Источник 3 — оценка моделью (`sources/estimate.py`)

Обёртка над текущим `LLMService` (промпты `_SYSTEM_PROMPT`/`_DISH_PROMPT` остаются как есть). Возвращает `ResolvedNutrition(source="estimate")`. Всегда последний, всегда включён — гарантия ответа и обратная совместимость (если флаги выключены, поведение = сегодняшнее).

---

## 10. Контракт API (дельта)

Без новых эндпоинтов в первом срезе — расширяем существующие ответы полем источника. SSOT — это ТЗ; `docs/api/openapi.yaml` синкнуть после (Action Item).

**`POST /api/v1/nutrition/analyze`** (ответ — каждая позиция получает источник):
```json
{"items": [
  {"name":"молоко Домик в деревне 3.2%","w":200,"kcal":118,"p":5.8,"f":6.4,"c":9.4,
   "source":"off","source_ref":"https://world.openfoodfacts.org/product/...","matched_name":"Молоко питьевое 3,2%"}
]}
```
**`POST /api/v1/nutrition/foods/analyze`** (создание блюда) — в ответ добавить `source`/`source_ref` (сейчас отдаёт только КБЖУ).
**`POST /api/v1/nutrition/entries`** — принять и сохранить `source`/`source_ref` на `FoodEntry` (поле `source` уже есть; добавить `source_ref` nullable).

Сериализация — короткие имена полей фронта сохраняются (`name/w/kcal/p/f/c`), добавляются `source`, `source_ref`, `matched_name`.

---

## 11. Кэш и синергия со SharedFood

- Кэшируем **при сохранении записи** (`save_entries`), если `source ∈ {label, off, web}` (НЕ `estimate`): upsert в `SharedFood` (каталог FAMILY).
- **Ключ дедупа:** нормализованный `(brand + name + fat_pct)` (lower, trim, ё→е, схлоп пробелов) ⊕ при наличии `barcode` — по barcode. Перед созданием — поиск существующего по ключу; есть → не плодим дубль (можно инкрементить `use_count`).
- Эффект: повторный ввод того же продукта → мгновенный пик из базы (режим «База»), без LLM и сети. Каталог = самонаполняющийся кэш; гасит латентность и лимиты.
- Видимость/скоуп — без изменений: дневник PERSONAL, каталог FAMILY (нужна семья; вне семьи кэш в SharedFood пропускаем, запись в дневник идёт).

---

## 12. Конфиг (`app/config.py`, `.env`)

| Ключ | Тип/деф. | Назначение |
|---|---|---|
| `nutrition_resolver_enabled` | bool = true | мастер-флаг инкремента (false → текущее поведение) |
| `off_enabled` | bool = true | ступень Open Food Facts |
| `off_base_url` | str = `https://world.openfoodfacts.org` | база OFF |
| `off_user_agent` | str (**обязателен**) | напр. `lifeManager_bot/0.4 (<admin_email>)` |
| `off_lc` | str = `ru` | локаль поиска |
| `off_timeout_s` | float = 2.5 | таймаут OFF |
| `nutrition_web_lookup_enabled` | bool = **false** | ступень Compound (включить после теста) |
| `groq_compound_model` | str = `groq/compound` | модель веб-поиска |
| `compound_timeout_s` | float = 8.0 | таймаут Compound |
| `resolver_total_budget_s` | float = 12.0 | общий мягкий бюджет (сетевые ступени) |

Все — через `pydantic-settings`, как существующие. Документировать в `.env.example`/README.

---

## 13. Таксономия ошибок и деградация

| Ступень | Сбой | Действие |
|---|---|---|
| extractor (LLM) | нет ключа/429/кривой JSON | как сейчас: `LLMNotConfigured`/`LLMRateLimit`/`LLMError` → REST 502/понятный текст |
| OFF | таймаут/HTTP!=200/пустой/нет матча | лог `warning`, `None` → следующая ступень |
| Compound | таймаут/ошибка/`per100g=null` | лог `warning`, `None` → estimate |
| estimate | падение LLM | существующая обработка (502) |

Принцип (как fail-loud ACL, но для UX мягко): источники деградируют тихо к следующему, наружу — всегда результат с честным `source`. Никогда не показываем веб-число как «точное», если это `estimate`.

---

## 14. Тест-план (без живых вызовов в pytest)

**Фейки за контрактами:** `FakeProductResolver`, `FakeNutritionSource`, существующий фейк `NutritionLLM`. Живых вызовов OFF/Compound/Groq в тестах нет (как GAP-6 сейчас).

**Фикстуры:** сохранить 2–3 реальных JSON-ответа OFF в `tests/fixtures/off_*.json` (снять при реализации через `curl`), скормить парсеру `OpenFoodFactsSource` (юнит маппинга и выбора матча — на фикстурах, без сети).

**Матрица:**
- extractor: текст «200гр молоко Домик в деревне 3.2%» → `{name~молоко, brand="Домик в деревне", fat_pct=3.2, portion_g=200}`; фото-этикетка полная → `label_complete=true, per100g set`; фото частичное → `per100g=null`.
- pipeline: label-short-circuit; OFF hit; OFF miss→web hit; web miss→estimate; таймаут OFF→переход; бюджет вышел→estimate.
- OFF маппинг: `energy-kcal_100g`→kcal; отсутствует kcal но есть kj→пересчёт; нет белка→skip; выбор лучшего матча (бренд/токены/fat).
- масштаб: per100g × portion/100, округление = текущему.
- кэш: save с `source=off` → создаётся SharedFood; повторный тот же ключ → без дубля; `source=estimate` → SharedFood НЕ создаётся.
- контракт: `/analyze` отдаёт `source/source_ref`; `/entries` сохраняет их.
- провенанс/видимость: вне семьи кэш не пишется; запись в дневник идёт.

**gate-тест RU-покрытия (С., до включения OFF в проде):** прогнать 15–20 реально покупаемых вами продуктов через OFF-поиск, замерить hit-rate и качество матча. Решение «включать ли web-ступень по умолчанию» — по результату. (Метод — как gate-тесты в `feasibility.md`.)

---

## 15. Критерии приёмки (DoD)

- [ ] 3 кейса дают корректный `source` (`label`/`off`/`web`/`estimate`) и масштабированные КБЖУ; подтверждено на реальных примерах (вкл. «молоко Домик в деревне 3.2%»).
- [ ] Лестница деградирует по §4 (таймаут/miss → следующая; estimate всегда отвечает).
- [ ] confirm-экран показывает источник; правка работает; save сохраняет `source`/`source_ref`.
- [ ] Кэш в SharedFood по §11 (создание, дедуп, skip estimate, skip вне семьи).
- [ ] Маппинг OFF подтверждён на живом ответе; имена полей в коде совпадают с фактическими.
- [ ] Конфиг-флаги работают: `nutrition_resolver_enabled=false` → поведение = сегодняшнее (регресс не сломан).
- [ ] Тесты на фейках/фикстурах зелёные; `import-linter` зелёный (границы `resolve`).
- [ ] gate-тест RU-покрытия проведён С.; решение по `nutrition_web_lookup_enabled` принято.
- [ ] `openapi.yaml` синкнут с §10; changelog `spec-nutrition.md`/этого файла обновлён.

---

## 16. Поэтапность внутри инкремента

- **R1 (первый срез):** extractor (текст + фото) → OFF full-text → estimate; провенанс; кэш в SharedFood; контракт-дельта; тесты. **Web-ступень в коде есть, но за флагом `false`.**
- **R2:** включить и протестировать Compound web-ступень (после gate-теста).
- **R-fast-follow (barcode):** извлечение EAN с фото (клиент `ZXing/quagga` в Mini App или сервер `pyzbar`) → шаг 2 лестницы (OFF by barcode). Самый точный матч по брендам.
- **Позже (опц.):** адаптер Nutritionix/USDA для generic/англоязычных — тот же порт `NutritionSource`.

---

## 17. Делегированные решения (зафиксированы, можно оспорить)

- Авто-`SharedFood` — при сохранении записи (не при находке).
- Провенанс — показываем всегда; web — с цитатой.
- Полнотекст OFF — дефолт `cgi/search.pl`; движок выбирает Code, фиксирует в коде.
- Таймауты/бюджет — §12 (правится конфигом).

## 18. Changelog
- **v0.1 (2026-06-13):** первичная редакция по ADR-0013; лестница OFF→Compound→estimate, DTO, маппинг OFF, целевые промпты, контракт-дельта, тест-план, поэтапность R1/R2/fast-follow.
