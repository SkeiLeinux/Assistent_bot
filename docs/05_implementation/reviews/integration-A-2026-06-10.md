# Интеграция волны A (Finance + Nutrition) — 2026-06-10

**Интегратор:** integrator-agent (по build-contract §6). **Коммит:** за С. (не сделан). Отчёт зафиксирован оркестратором из вывода агента.

## §6.1 Границы — чисто
`git diff --stat` + status: общие файлы (`alembic`, `app/main.py`, `app/delivery`, `pyproject.toml`, `requirements.txt`, `tests/conftest.py`) агентами не тронуты. Правки агентов — только в `app/contexts/finance/**`, `app/contexts/nutrition/**`, `tests/test_finance*`, `tests/test_nutrition.py` + удаление 4 файлов food-сырья. Нарушений нет.

## §6.2 Зависимости
`openpyxl==3.1.5` → requirements.txt (заперт в `contexts.finance.importing`); `pip install` в `.venv` подтверждён.

## §6.3 Линтер-контракты (pyproject.toml) — +4 forbidden
- `app.analytics` ↛ `app.contexts.finance.{models,repositories,services,api,importing}` (allow_indirect_imports)
- `app.analytics` ↛ `app.contexts.nutrition.{models,repositories,services,api}`
- `app.analytics` ↛ `app.contexts.reminders`
- `*` (кроме `app.contexts.finance`) ↛ `app.contexts.finance.importing` (allow_indirect_imports — запрещён только прямой импорт ACL)

Итог: **lint-imports 7 kept / 0 broken**.

## §6.4 Q-nut-1…4 — выполнено
- **Q-nut-1 (вариант B):** классы `SharedFood`/`FoodEntry` удалены из `app/db/models.py` (пустая заглушка с историей); в `tests/conftest.py` импорт переключён на `app.contexts.{finance,nutrition}.models`; шим `nutrition/models/_compat.py` + его вызов удалены как мёртвый код; `alembic/env.py` переключён на контекст-метадату; зачищены висячие food re-export'ы в `app/services/__init__.py` и `app/repositories/__init__.py`.
- **Q-nut-2:** nutrition `register_routes` смонтирован; `auth_middleware` уже кладёт `request["viewer"]` и `request["user"]` — доп. правок не нужно.
- **Q-nut-3:** error-middleware дополнен: `InvalidInput`→422, `FamilyRequired`→409, `NutritionError`→422, `LLMNotConfiguredError`→503, `LLMRateLimitError`→429, `LLMError`→502.
- **Q-nut-4:** покрыт контрактом §6.3.

## §6.5 Миграция — одна, single head
- Ревизия **`8c1ef1bb5136`** "S2 wave A: finance + nutrition", down_revision = Lists head.
- Diff прочитан: 6 таблиц finance + `nutrition_goals` + дельта `shared_foods.family_id` (FK `fk_shared_foods_family_id`, имя задано вручную — SQLite batch-mode требует именованный FK). Ничего лишнего по kernel/db/lists.
- `alembic upgrade head` на чистой тест-БД — ок; `alembic heads` = **ровно одна** (multi-head риск S0 проверен явно). Drift-check (повторный autogenerate) — пусто. Схема: 17 таблиц.

## §6.6 Монтаж REST
`web.py`: `/api/v1/finance/*` (import/months/categories/review/resolve/analytics/income/export) + `/api/v1/nutrition/*` (day/goals/analyze/entries/period/export/foods); `create_app()` = 56 routes. Удалён транзит `app/delivery/miniapp_api/api.py` (старый food-API).

## §6.7 Прогон
- **pytest:** 75 passed (.venv)
- **lint-imports:** 7 kept / 0 broken
- **smoke `python -m app.main`:** полный boot — миграции → web 8080 (kernel+lists+finance+nutrition) → scheduler → polling на живой Telegram; чистый внешний стоп; throwaway-БД удалена, dev-БД не тронута.

## Парковки
- **Q-finance-1 (I3):** spec-finance v0.4, реализация агента соответствует. Закрыто.
- **Q-finance-2 (`metric`):** принимается и игнорируется, ответ — полный AnalyticsDTO. Передано reviewer.
- **Q-finance-3 (`carryover`):** Σ actual CARRYOVER-источников по целым месяцам + дубль в by_source. Передано reviewer.
- **Q-nut-1…4:** выполнены.

## Findings (не чинено — вне границы/волны)
1. **Дормант-транзит kernel сломан по импорту (предсуществующий, не регресс A):** `app/handlers/common.py`, `app/repositories/{user,family}_repository.py`, `app/services/{user,notification}_service.py` импортируют отсутствующие символы из `app/db/models.py` (ушли в S1). Модули полностью дормант (живых импортёров нет); pytest и lint-imports зелёные. Решейп — S3/delivery.
2. **`run_migrations` в `app/main.py`** декодирует stderr alembic как UTF-8 → `UnicodeDecodeError` в логе при CP1251-выводе (косметика; миграции применяются). Предсуществует. Кандидат на фикс вне волны A.
3. **В рабочем дереве** также модифицированы (не интегратором): `.claude/settings.json`, `docs/{ADR-0002,spec-finance,agents-guide,openapi}` — спек-синк С. + доки агентов. Для коммита С.

**Ключевые файлы:** `alembic/versions/8c1ef1bb5136_*.py` · `app/delivery/miniapp_api/web.py` · `pyproject.toml` · `alembic/env.py` · `tests/conftest.py`
