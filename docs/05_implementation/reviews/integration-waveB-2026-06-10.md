# Интеграция волны B (S3: reminders + analytics + delivery) — 2026-06-10

Сводный отчёт integrator-agent (build-contract §6). Вход для reviewer-agent и чек-поинта С.
Записан оркестратором (агент вернул отчёт сообщением из-за харнесс-ограничения на запись docs).

## Смонтировано

**REST (`app/delivery/miniapp_api/web.py`):**
- `/api/v1/reminders/*` (GET/POST list, GET/PATCH/DELETE by id, POST `/ack`, POST `/snooze`) — `contexts.reminders.api.register_routes`.
- `/api/v1/analytics/*` (`/overview`, `/digest/preview`, `/digest/schedules`, PUT `/digest/schedules/{cadence}`) — `analytics.api.register_routes`.
- Обработчики доменных ошибок Reminders в `error_middleware` (ReminderNotFound→404, FamilyRequired→409, InvalidInput→422, ReminderError→422) — у Reminders плоские `Exception`, маппинг явный. Analytics-роуты бросают `web.HTTPException` напрямую.
- 72 маршрута в собранном приложении; 10 новых эндпоинтов присутствуют; viewer/session из существующего `auth_middleware`.

**Композиция (`app/main.py`):**
- `Notifier(bot)` — DI `Bot` (единственный исходящий шов, I1/ADR-0004).
- `scheduler.register(make_reminder_poll(async_session_maker, notifier))` — грейс по дефолту фабрики (3 ч).
- `scheduler.register(make_digest_job(async_session_maker, make_digest_deliver(notifier)))`.
- `CallbackDispatcher` с `register("rem", {"ack", "snooze"})` поверх `ReminderService`, проброшен в `build_dispatcher(callback_dispatcher=...)`.

**Метаданные/тесты:** `alembic/env.py` и `tests/conftest.py` дополнены импортами `contexts.reminders.models` + `analytics.schedule` (регистрация таблиц в `Base.metadata`).

## Миграция
- **Ревизия `933845dd7c61`** (`S3 wave B: reminders + analytics`), `down_revision = 8c1ef1bb5136` (хвост волны A).
- **Single head:** линейная цепочка `base → 237ca7b0e901 → b1f2a3c4d5e6 → 8c1ef1bb5136 → 933845dd7c61`. Multi-head (прецедент S0) не возник.
- **Diff прочитан глазами:** только две новые таблицы — `digest_schedules` (FK→families, uq family+cadence, enum cadence non-native) и `reminders` (FK→families/users, enums recurrence/status/scope non-native, индексы next_fire_at/owner_id/family_id). Ничего по kernel/db/finance/nutrition/lists. `downgrade` корректен. Применено на чистую тест-БД → upgrade head без ошибок, 20 таблиц включая обе новые.

## Линтер
- `lint-imports`: **7 kept / 0 broken** (290 deps vs 283 до — новые рёбра `main → analytics/reminders/notifications` слой-легальны). Контракты волны B уже были в `pyproject.toml`, новых не добавлял.

## Прогон + smoke
- **pytest: 174 passed** (интеграция ничего не сломала).
- **Smoke `python -m app.main` (temp DB, таймаут 30 с) — boot-wiring чист:** миграции → web `127.0.0.1:8080` (kernel+lists+finance+nutrition+reminders+analytics) → scheduler с 2 jobs оттикал → обе job реально отработали (SELECT по `reminders` и `digest_schedules`, COMMIT) → бот ушёл в polling. Остановлен по таймауту (polling блокирует, exit 124 — ожидаемо).

## Зависимости
- Новых внешних пакетов волна B не заявляла — ничего не ставил (aiogram уже есть).

## Адаптеры склейки
1. **Notifier-порт reminders ↔ notifications.Notifier:** адаптер НЕ потребовался. `notifications.Notifier.deliver(recipients, text, *, buttons, kind)` удовлетворяет порт. Классы `Button` разные, но оба `{text, callback_data}`, `render_keyboard` читает по атрибутам — duck-typing, проверено (`reminder_buttons(7)` → `rem:ack:7`/`rem:snooze:7`).
2. **Analytics deliver-адаптер:** `make_digest_deliver(notifier)` в main.py — `deliver(chat_ids, text)` → лёгкие `_ChatRecipient(telegram_user_id=...)` → `notifier.deliver(recipients, text, kind="digest")`.
3. **Грейс (Q-rem-2):** дефолт фабрики `DEFAULT_GRACE_SECONDS` (3 ч); проброс из `config.py` — follow-up (вне границы интегратора).

**Callback-склейка (Q-del-2):** rem-обработчики владеют собственной сессией на нажатие (`open → ack/snooze → commit`). Невидимое/удалённое напоминание: `ReminderService` бросает `ReminderNotFound` → обработчик поднимает `ReminderClosed` → «Напоминание уже закрыто» (edge §10). Снуз = 60 мин (R1).

## Парковки волны — статус
- **Q-rem-1** (deliver не принимал кнопки/User/kind): **закрыта монтажом.**
- **Q-rem-2** (грейс в конфиг): **follow-up** (зарегистрировано с дефолтом).
- **Q-del-2** (привязка `rem`→reminder_service): **закрыта монтажом** (main.py).
- **Q-an-1/Q-an-2** (recent_changes/goals): **закрыты** вариантом А.
- **Q-an-3/Q-an-4** (top-3 трат, усечение >4096): принятые обходы в коде Analytics.

## Findings (для reviewer / С.)
1. **Q-del-1 (change-хук Lists) — НЕ смонтирован, остаётся открытым.** `notify_list_change`/`ListChange`/`NOTIFY_MATRIX` готовы и протестированы (`tests/test_delivery_changes.py`), но точка вызова after-commit на мутациях Lists требует, чтобы Lists-API-хендлеры (`app/contexts/lists/api/routes.py` — доменный пакет, read-only для интегратора) отдавали метаданные изменения (scope/family/hidden_from/list_name/item_text), и `Notifier` был прокинут в web-app (сейчас `start_web_server()` без Bot — меняется сигнатура). Молчаливо править домен не стал. **Рекомендация:** отдельная задача с касанием Lists-домена.
2. **Сид `default_schedules(family_id)` при создании семьи — НЕ смонтирован.** Дайджест работает без сида (REST `put_schedule` создаёт по требованию; пустой набор — корректный no-op). Но дефолты (daily 23:59 / weekly вс 23:00, Катя) не засеваются. Чистый сид требует касания обоих путей создания семьи: kernel REST `post_family` и бот `cmd_newfamily`. **Рекомендация:** перенести сид в `PairingService.create_family` (kernel-домен) — решение С.
3. **Косметика `run_migrations`:** `subprocess.run(...encoding="utf-8")` читает stderr alembic, на Windows-консоли cp1251 → `UnicodeDecodeError` в reader-треде. Миграции применяются успешно. Пред-существующий артефакт, не волна B. Рекомендация: `errors="replace"`.

## Граница записи — соблюдена
Запись только в: `alembic/env.py`, `alembic/versions/933845dd7c61_*.py`, `app/delivery/miniapp_api/{__init__,web}.py`, `app/main.py`, `tests/conftest.py`. Доменные пакеты не тронуты. `docs/06_program/**` (правки С.) не тронуты. Коммит — за С.

---

## Addendum — S3.1 wiring-срез (закрытие findings 1–2, решение С. «дослать оба сейчас»)

После интеграции дослан завершающий wiring-срез (general-purpose агент, оркестратор), закрывающий findings 1 и 2. **Миграций не добавлял** (ни таблиц, ни колонок).

**Finding 1 — push «партнёр изменил список» (F6.2) — ✅ СМОНТИРОВАН** через инверсию зависимости (без нарушения слоёв; `start_web_server` НЕ менялась):
- `app/contexts/lists/change_events.py` (новый) — contexts-owned `ListChangeData` (на kernel-`Scope`) + порт `set_change_listener`/`emit` (дефолт no-op). Lists НЕ импортирует delivery.
- `app/contexts/lists/api/routes.py` — эмиссия после успешных мутаций: post_list→list_created, delete_list→list_deleted, post_item→item_added, patch_item→item_done/item_moved/item_text_edited, delete_item→item_deleted; clear_done не эмитит. Сервис аддитивно: `list_meta(list_id)`, снапшоты на delete.
- `app/delivery/bot/list_changes.py` — `make_lists_change_listener(notifier)` (ListChangeData→ListChange→notify_list_change).
- `app/main.py` — `set_change_listener(make_lists_change_listener(notifier))` на старте.

**Finding 2 — сид дефолтных расписаний дайджеста — ✅ СМОНТИРОВАН:**
- `app/analytics/schedule.py::seed_default_schedules(session, family_id)` (экспорт из `app/analytics/__init__.py`).
- Зовётся из двух delivery-точек создания семьи: `kernel_api.py::post_family` и `bot/handlers.py::cmd_newfamily`. `join` не сидит.

**Парковка Q-del-1** в `questions/delivery.md` помечена ✅ РЕШЕНО (S3.1).

**Гейт после S3.1 (проверено оркестратором независимо):** `lint-imports` 7 kept/0 broken (ни одного нового ребра contexts→delivery — инверсия порта держит границу) · `pytest -q` **189 passed** (+15: `test_list_change_wiring.py`, `test_analytics_seed.py`) · single head `933845dd7c61` · **boot-smoke** `app.main` — web + scheduler (2 job) + polling поднимаются.

### Остаётся (follow-up, не блокеры коммита)
- **Q-rem-2** — грейс напоминаний в `config.py` (сейчас дефолт фабрики 3 ч).
- **Q-an-3 / Q-an-4** — принятые обходы в коде Analytics (top-3 трат, усечение >4096).
- **Косметика #3** — `run_migrations` на Windows: `subprocess.run(..., errors="replace")` (на каждом boot печатает `UnicodeDecodeError` при декоде stderr alembic в cp1251; миграции применяются, boot проходит). Тривиальный one-liner в `app/main.py`.
