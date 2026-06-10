# Парковка вопросов — Nutrition (S2c, волна A)

Формат — build-contract §4. Здесь: пункты-контракты, затрагивающие файлы вне
границы записи nutrition-агента (зона интегратора: `app/db/models.py`,
`tests/conftest.py`, миниапа `app/delivery/miniapp_api/`). Не «улучшено молча» —
вынесено на интеграционный шаг (build-contract §6).

---

## Q-nut-1: дублирование таблиц nutrition при переносе (transit ↔ canonical)
- Где: ТЗ §3 (перенос моделей под kernel); `app/db/models.py` (дормант-транзит,
  всё ещё объявляет `shared_foods`/`food_entries`); `tests/conftest.py:8`
  (`import app.db.models  # noqa — транзит Nutrition`).
- Проблема: канонические модели контекста (`app/contexts/nutrition/models/`)
  объявляют те же tablename'ы `shared_foods`/`food_entries` (+ `nutrition_goals`).
  Пока транзит в `app/db/models.py` жив и импортируется conftest'ом, к моменту
  импорта канонических моделей одноимённые таблицы уже в общей `Base.metadata` →
  `InvalidRequestError: Table 'food_entries' is already defined`. И `db/models.py`,
  и `conftest.py` — зона интегратора (build-contract §3), агент их не правит.
- Варианты:
  - (A, выбран как временный обход) канонические модели перед своим объявлением
    снимают одноимённые транзитные таблицы с metadata (`models/_compat.py:
    drop_transit_tables()`), зовётся 1 раз в `models/__init__.py` до импорта
    моделей. Idempotent, после зачистки транзита — no-op.
  - (B, целевой, за интегратором) удалить классы `SharedFood`/`FoodEntry` из
    `app/db/models.py`; в `tests/conftest.py` заменить `import app.db.models` на
    `import app.contexts.nutrition.models`. Тогда `_compat`-шим станет мёртвым
    кодом и его можно удалить.
- Обход в коде: реализован — `app/contexts/nutrition/models/_compat.py` +
  единственный вызов в `models/__init__.py`. Тесты (`tests/test_nutrition.py`)
  и полный сьют зелёные С этим обходом. **Действие интегратора:** выполнить
  вариант B (db/models + conftest), затем удалить `_compat.py` и его вызов.

## Q-nut-2: монтаж REST и контракт request-ключей миниапы (viewer + user)
- Где: ТЗ §9/§10 (REST под `/api/v1/nutrition/*`, current_viewer, timezone из
  kernel-User); build-contract §6.5 (монтаж REST — интегратор).
- Проблема: хендлеры `app/contexts/nutrition/api/routes.py` читают
  `request["viewer"]` (видимость: PERSONAL по `viewer.id`, FAMILY по
  `viewer.family_id`) и `request["user"]` (таймзона для границ суток — её нет в
  `Viewer`). Lists-контекст использует только `request["viewer"]`; Nutrition
  дополнительно требует `request["user"]` (kernel-`User` с `.timezone`).
  `resolve_viewer` уже возвращает `(User, Viewer)` — оба доступны.
- Варианты:
  - (A, ожидается) общий auth-middleware миниапы кладёт в request и
    `viewer`, и `user` (как делает старый food `api.py` с `request["user"]`).
    `_tz_name` в routes имеет безопасный дефолт (`Europe/Amsterdam`), если `user`
    не проставлен — но для корректных суток интегратор обязан проставить `user`.
  - (B) расширить `Viewer` полем `timezone` в kernel — нежелательно (правка
    kernel, вне волны A).
- Обход в коде: `register_routes(app)` + хендлеры готовы; читают `request["session"]`,
  `request["viewer"]`, `request["user"]`. **Действие интегратора:** смонтировать
  `register_routes`, обеспечить `request["user"]=User` и `request["viewer"]=Viewer`
  в auth-middleware; маппинг LLM-ошибок (см. Q-nut-3).

## Q-nut-3: маппинг доменных/LLM-ошибок в HTTP-коды (error-middleware миниапы)
- Где: ТЗ §9 (коды 401/403/422/502/429); I6 (LLMError/лимит). Старый food
  `api.py` имел собственный `error_middleware` (LLMNotConfigured→503, RateLimit→429,
  LLMError→502, InvalidInput→400). Lists полагается на общий middleware миниапы.
- Проблема: контекст НЕ импортирует delivery; общий error-middleware — у миниапы.
  Хендлеры nutrition бросают `InvalidInput`/`FamilyRequired` (домен) и
  `LLMError`/`LLMRateLimitError`/`LLMNotConfiguredError` (llm_service). Их в коды
  ответа должен мапить общий middleware delivery.
- Варианты:
  - (A, ожидается) общий error-middleware миниапы дополняется ветками:
    `InvalidInput`→422, `FamilyRequired`→409/422, `LLMNotConfiguredError`→503,
    `LLMRateLimitError`→429, `LLMError`→502 (как в исходном food `api.py`).
    404 (запись/блюдо не найдено) уже отдаётся хендлерами явным JSON.
- Обход в коде: хендлеры бросают типизированные ошибки; 404 — явным `_json(...,404)`.
  **Действие интегратора:** добавить ветки в error-middleware миниапы при монтаже.

## Q-nut-4: forbidden-контракт import-linter для Analytics → nutrition.read
- Где: build-contract §6.2 (линтер достроить перед волной B); ТЗ §13 A6.
- Проблема: контракт «Analytics берёт nutrition только через `.read`» (аналог
  существующего lists-контракта) добавляется в `pyproject.toml` — зона
  интегратора. Сейчас изоляция держится контрактом «Contexts are mutually
  independent» (KEPT). Отдельный forbidden-контракт для `app.analytics` →
  `app.contexts.nutrition.{models,repositories,services,api}` появится с
  analytics-контекстом (волна B).
- Обход в коде: read-граница готова (`app/contexts/nutrition/read/`), LLM заперт
  в `services/llm_service.py`. **Действие интегратора:** добавить forbidden-контракт
  в `pyproject.toml` на шаге §6.2 (перед волной B).
