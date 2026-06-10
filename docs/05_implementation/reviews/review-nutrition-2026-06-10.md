# Review Nutrition (волна A, S2c) — 2026-06-10

**Вердикт: ✅ принять**

Независимая приёмка против `docs/04_specs/spec-nutrition.md` (v0.2), критерии A1–A6 построчно + инварианты I1/I2/I5/I7. Я read-only.

## Механика (прогнал сам, .venv)
- **pytest (полный):** 75 passed. `test_nutrition.py` — 16 тестов зелёные.
- **lint-imports:** 7 kept / 0 broken. «Analytics reads Nutrition only via read-module» KEPT; «Contexts are mutually independent» KEPT.

## Границы записи
Чисто. Агент писал только `app/contexts/nutrition/**` и `tests/test_nutrition.py`; реализовал минус-право — удалил транзит-сырьё еды: `app/services/{food_service,llm_service}.py`, `app/repositories/{food_repository,shared_food_repository}.py` (подтверждено `git diff --cached`: 4 файла в staged deletions, на диске отсутствуют). Старый food-API `app/delivery/miniapp_api/api.py` удалён интегратором. Прочие изменённые общие файлы (`db/models.py`, `conftest.py`, `web.py`, `pyproject.toml`) — зона интегратора (Q-nut-1…4). Нарушений нет.

## A-критерии (построчно, тест прочитан — не вырожден)
- **A1 ✅** `test_text_input_analyze_then_save` (текст→analyze→save, сумма дня 920 ккал), `test_photo_input_analyze` (vision-фейк), `test_db_input_recalculates_per_portion` (3-й способ — каталог), `test_goals_roundtrip_and_progress`, `test_export_markdown_dump`. Дневник/каталог/3 способа/цели/выгрузка работают под kernel-Viewer и scope.
- **A2 ✅** `test_goals_roundtrip_and_progress`. Цели в `NutritionGoals` по `user_id`; прогресс-бары читают оттуда (`MacroProgress`); null = снять цель. Дельта-проверка: `User.target_*` удалены — модель `User` в kernel их не несёт (S1), транзит db/models вычищен (Q-nut-1 variant B интегратором). Подтверждено отсутствие колонок.
- **A3 ✅** `test_diary_personal_invisible_to_partner` (FoodEntry PERSONAL — партнёр чужой дневник не видит), `test_partner_cannot_edit_or_delete_foreign_entry` (owner-guard на update/delete), `test_catalog_family_visible_to_both` (SharedFood FAMILY общий), `test_catalog_not_visible_across_families` (FAMILY-изоляция между семьями), `test_catalog_requires_family`. NutritionGoals — владельцу (фильтр по `viewer.id`). Видимость не вырождена.
- **A4 ✅** `test_analyze_does_not_write` (analyze_text/analyze_photo → дневник пуст), `test_text_input_analyze_then_save`, `test_save_to_db_creates_shared_food` (save_to_db → каталог на 100 г), `test_db_input_recalculates_per_portion` (пересчёт по граммам). analyze ≠ save, подтверждение в UI.
- **A5 ✅** `test_llm_error_propagates_invalid` (невалидный → LLMError), `test_llm_rate_limit_propagates` (429 → LLMRateLimitError ⊂ LLMError), `test_llm_is_injectable_provider_agnostic` (FoodService зависит только от Protocol `NutritionLLM`, фейк подменяет Groq, `fake.calls==2`). Строгий JSON (`response_format=json_object`) + pydantic-валидация в `llm_service._complete`.
- **A6 ✅** `test_read_macros_by_day_owner_only` (read отдаёт КБЖУ владельца; партнёр чужой дневник через read не видит), `test_read_macros_total_sums_period`. Analytics берёт КБЖУ только через `contexts.nutrition.read` (import-linter). LLM-специфика заперта в `services/llm_service.py` (read его не импортирует).

## Инварианты с зубами
- **I1 (абсолют за порцию, сутки в `user.timezone`)** — `test_read_macros_total_sums_period` (SUM абсолютов) + `test_day_boundary_in_user_timezone` (Pacific/Kiritimati UTC+14: запись 2026-01-01 22:30 UTC → локально 2-е число; в дне 1-го пусто, во 2-м есть). Реально проверяет таймзонные границы суток, не косметика. `created_at` — наивный UTC, границы переводятся в `_day_bounds_utc`.
- **I2 (пересчёт на 100 г)** — `test_db_input_recalculates_per_portion` (200 г/680 ккал → 340/100 г; порция 100 г → 340) + `test_save_to_db_creates_shared_food` (600/3 = 200/100 г). `save_shared_food` хранит на 100 г, `create_entry_from_shared` пересчитывает по портеру.
- **I5 (analyze не пишет)** — `test_analyze_does_not_write`. В коде analyze_* только зовут `self.llm.*`, ни одной записи в БД.
- **I7 (LLM за интерфейсом)** — `NutritionLLM` Protocol; `FoodService.__init__(llm=…)` инжектируемый; Groq-клиент ленивый синглтон (`_get_client`), импорт модуля сети не трогает. Замена провайдера = только `llm_service.py`.
- **Видимость (A3, kernel I10)** — нет чтений мимо PERSONAL-фильтра: дневник всюду фильтруется `FoodEntry.user_id == viewer.id` (service + read); каталог — `family_id`. Партнёр через прямой service-вызов и через read-модуль чужой дневник получает пустым.

## GAP-6 (живые внешние вызовы) — закрыт
LLM в тестах — **только** `FakeLLM`/`RaisingLLM` за интерфейсом `NutritionLLM`. Греп по `tests/` на `groq|AsyncGroq|telegram|aiogram|Bot(|httpx|requests.` — ни одного живого вызова (совпадения — только поле `telegram_user_id`). Импорт `llm_service` не инстанцирует клиент (ленивость). Ни одного обращения к Groq/Telegram из pytest.

## Поведение 1:1 с current-state (вариант C)
Сверено: `FoodEntry`/`SharedFood`/`NutritionGoals` — поля как в ТЗ §4 (KBJU абс. за порцию у entry, на 100 г у каталога, `SharedFood.family_id` — дельта OQ-N2, `NutritionGoals.user_id` 1:1). 13 REST-эндпоинтов под `/api/v1/nutrition/*` (OQ-N3), маппинг 1:1 со старыми bare-путями. meal_type авто по времени (5/11/16/22, I3), группировка по приёмам, прогресс-бары, markdown-выгрузка с шаблоном промпта — сохранены. Переписывания сверх дельты §3 не обнаружено.

## Findings
1. **[инфо, не блокер]** Видимость Nutrition проверяется через сервис/read напрямую (фильтр по `user_id`/`family_id`), а не через `tests/shareable_harness.py`. Это корректно: `FoodEntry`/`SharedFood` по ТЗ §3 (F1.3) — не полный `Shareable`-миксин (нет колонки `scope`, scope фиксирован по сущности), поэтому синтетический `Gift`-харнесс к ним неприменим. Брифовое требование «видимость через харнесс» относится к Shareable-сущностям; здесь верный суррогат. Не нарушение.
2. **[инфо]** `FoodService.__init__` дефолтит `llm=LLMService()`. Конструктор `LLMService` читает только `settings.groq_model` (сеть/ключ — лениво при первом вызове), поэтому инстанцирование без инъекции безопасно; в проде REST-вызов analyze_* без ключа даст `LLMNotConfiguredError` (503, маппится error-middleware — Q-nut-3). Поведение ожидаемое.
3. **[инфо]** `GET /api/v1/nutrition/foods/analyze` и dish-режимы (text/photo/combined) есть в API и сервисе, но dish-методы покрыты только через `test_llm_is_injectable_provider_agnostic` (combined). Прямого приёмочного теста на `analyze_dish_*`-роуты нет — это не A-критерий (A1 покрывает «создание блюд в каталог» на уровне save_shared_food). Достаточно для приёмки; полное E2E роутов — на волне C (фронт).

## Парковки (оценка)
Q-nut-1…4 — все легитимны: затрагивают файлы вне границы записи nutrition-агента (зона интегратора — `db/models.py`, `conftest.py`, миниапа, `pyproject.toml`). Не «улучшено молча», а корректно вынесено на интеграционный шаг. Интегратор их выполнил (`integration-A-2026-06-10.md §6.4`): Q-nut-1 variant B (food-классы удалены, conftest на контекст-метадату, `_compat`-шим удалён как мёртвый код), Q-nut-2/3 (монтаж + error-middleware), Q-nut-4 (контракт линтера). Закрыты корректно.

## Условия приёмки
Нет (вердикт ✅). Findings — информационные.
