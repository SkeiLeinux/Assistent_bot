# Review волна B (S3: reminders + analytics + notifications/delivery + S3.1-wiring) — 2026-06-10

**Вердикт: ✅ принять** (финальное слово — за С.).

Независимая пост-интеграционная приёмка трёх контекстов (Reminders A1–A7, Analytics A1–A6,
Notifications A1–A5) + проводки (интеграция web/main/scheduler + S3.1 wiring change-хука и сида).
Reviewer ничего не правил — только этот файл.

## Механика (проверено самостоятельно, не по отчётам)
- **pytest:** `189 passed in 62.9s` — совпадает с ожиданием брифа.
- **lint-imports:** `7 kept / 0 broken` (141 файл, 297 зависимостей) — совпадает. Все 3 контракта
  «Analytics reads X only via read-module» + «Analytics does not depend on Reminders» + слои KEPT.
- **Миграция:** `933845dd7c61` — single head (`alembic heads` → один узел); `down_revision=8c1ef1bb5136`
  (хвост волны A). `upgrade/downgrade` создаёт/сносит РОВНО две таблицы (`digest_schedules`, `reminders`),
  ничего по kernel/db/lists/finance/nutrition.

## Границы записи — чисто
Весь footprint (28 modified + untracked) внутри авторизованного совокупного набора волны B:
агенты (`contexts/reminders/**`, `analytics/**`, `notifications/**`, `delivery/bot/**`), вариант А
(`contexts/{lists,nutrition}/read/**`), интегратор (`alembic/env.py`, `versions/933845dd7c61_*`,
`miniapp_api/{__init__,web}`, `main.py`, `conftest.py`), S3.1 wiring
(`contexts/lists/{change_events,api/routes,services/**,__init__}`, `delivery/bot/{list_changes,handlers,__init__}`,
`miniapp_api/kernel_api`, `analytics/{schedule,__init__}`), тесты + парковки + этот отчёт.
- **Кросс-контаминации нет:** reminders не лезет в finance/lists; analytics импортирует домены
  ТОЛЬКО через `contexts.*.read` (проверено grep — `services/models/api/repositories` чужих доменов
  не импортируются; lint это и закрепляет).
- **`docs/06_program/**`** — подтверждено: только доки (6 .md, 16 строк; ноль .py). Это параллельные
  правки С. (Q2/Q3), НЕ часть волны B — исключены из оценки корректно.
- `conftest.py` — добавлены только два import-`# noqa` (регистрация таблиц в metadata), без логики.

## Слои / анти-фантазия
- **S3.1 НЕ создал ребро `contexts→delivery`.** `contexts/lists/change_events.py` импортирует только
  `app.kernel.models.shareable.Scope` (проверено глазами + grep): порт-инверсия держит границу,
  Lists владеет нейтральным `ListChangeData` + module-level слушателем (дефолт no-op), delivery
  подписывается из `main.py`. Lint (0 broken) согласуется.
- **Lists-роуты эмитят через порт `emit`, не через delivery.** Единственные упоминания «delivery» в
  `contexts/lists` — докстринги + .pyc; реальных импортов `app.delivery`/`app.notifications` нет.
- **Контрактные поверхности vs ТЗ — расхождений нет.**
  - Reminders REST/DTO = §9 точь-в-точь (7 маршрутов под `/api/v1/reminders`; `ReminderDTO` поля
    совпадают; `interval` требует `interval_seconds`).
  - Analytics REST = §9 (`overview`, `digest/preview`, `digest/schedules` GET/PUT с `{cadence}`).
  - Notifications `deliver(recipients, text, *, buttons, kind) -> DeliveryReport` = §8.
  «Улучшений сверх ТЗ» не обнаружено.

## A-критерии — построчно (A→тест, проверено по утверждению, не по маппингу агента)

**Reminders (spec-reminders §13):**
- **A1** ✅ `test_a1_crud_and_recurrence_kinds` (CRUD + все 4 recurrence, delete→CANCELLED),
  `test_a1_interval_requires_seconds`, `test_a1_local_time_converted_to_utc`.
- **A2** ✅ `test_a2_oneshot_fires_once_then_done` (один push, повторный тик — 0),
  `test_a2_recurring_advances_next_fire_at`, `test_a2_interval_and_weekly_advance`. Реальный claim+advance.
- **A3** ✅ `test_a3_restart_does_not_lose_or_duplicate` — разные сессии на одну in-memory БД
  (имитация рестарта), не теряет (push=1) и не дублирует (повторный тик push=1). Состояние в БД.
- **A4** ✅ `test_a4_catchup_within_grace_fires_once`, `..._over_grace_oneshot_missed_no_delivery`
  (MISSED, push=0), `..._over_grace_recurring_advances_to_future` (ACTIVE, слот строго в будущем,
  без «шторма»), `..._then_fires_at_future_slot`. FSM MISSED→ACTIVE для recurring санкционирован
  спекой (§I5, диаграмма строки 121–122).
- **A5** ✅ `test_a5_ack_closes_oneshot`, `..._ack_recurring_keeps_schedule`, `..._snooze_shifts_next_fire_at`,
  `..._snooze_caps_at_next_recurring_slot`, `..._404_for_invisible`. Из push-кнопки — через
  `test_delivery_callbacks.py` (rem:ack/snooze:{id} → сервис; закрытое → no-op).
- **A6** ✅ `test_a6_personal_delivers_to_owner_only`, `..._family_delivers_to_both`,
  `..._visibility_personal_hidden_from_partner`, `..._delivery_carries_ack_snooze_buttons`.
  `resolve_recipients` резолвит по scope через kernel FamilyRepository/UserRepository (не мимо).
- **A7** ✅ `test_reminders_isolation.py` — статическая AST-проверка: нет импортов delivery/analytics/
  aiogram/соседних контекстов; нет собственного event-loop; доставка только через инъектируемый порт.
  Дублируется lint-контрактом.
- *Доп. инвариант I4:* `test_double_tick_single_push` — два тика на одном «сейчас» → один push.

**Analytics (spec-analytics §13):**
- **A1** ✅ `test_overview_aggregates_three_spheres` (три сферы on-the-fly через `read`),
  `test_get_overview` (REST). Видимость-фильтровано — см. A4.
- **A2** ✅ `test_job_delivers_to_both_and_updates_last_sent` (оба получателя, last_sent_at двигается,
  per-recipient тексты), `test_job_second_tick_same_day_no_duplicate` (повторный тик — без дубля).
- **A3** ✅ `test_digest_composition_three_spheres_no_reminders` («reminders» нет в составе и в тексте),
  `test_digest_hides_empty_sections`, `test_digest_fully_empty_returns_none`.
- **A4** ✅ `test_digest_per_recipient_personal_food` (чужое питание исключено),
  `test_digest_excludes_hidden_gift` (скрытый подарок → дайджест получателя = None),
  `test_overview_excludes_other_users_food`, `..._excludes_hidden_list_item`. Реальная `hidden_from`-таблица.
- **A5** ✅ `lint-imports` (3 контракта read-only KEPT) + grep подтвердил: analytics импортирует только
  `contexts.{lists,finance,nutrition}.read` (+ kernel). Прямого доступа к services/models нет.
- **A6** ✅ `test_get_schedules_returns_defaults`, `test_put_schedule_updates_time_and_enabled`,
  `test_put_weekly_weekday`, `test_put_bad_{cadence,time}_422`, `test_job_disabled_schedule_not_sent`
  (enabled=false → 0 рассылок). Настраиваемость + выключение покрыты на REST- и job-уровне.

**Notifications (spec-notifications §12):**
- **A1** ✅ `test_deliver_success_report` (доставка на telegram_user_id),
  `test_deliver_renders_buttons_into_inline_keyboard`, `test_deliver_without_buttons_sends_no_markup`.
- **A2** ✅ `test_forbidden_is_logged_without_retry_or_alert`, `test_bad_request_logged_no_retry_with_alert`,
  `test_transient_one_retry_then_success`, `test_transient_persistent_one_retry_then_alert`,
  `test_unknown_exception_one_retry_then_alert`, мульти-получатель — один фейл не рвёт остальных.
  Полная политика лог→1 ретрай→алерт; блокировка — без ретрая/алерта.
- **A3** ✅ `test_delivery_changes.py::test_hidden_item_not_pushed_to_excluded_partner` (явный per-delivery
  assert), `..._actor_does_not_receive_own_change`, `..._personal_change_notifies_no_one`. `resolve_recipients`
  вычитает актора + hidden_from через kernel IdentityService.
- **A4** ✅ `lint-imports` (домены не импортируют Bot/aiogram; Notifications — единственный исходящий шов);
  `test_reminders_isolation` подтверждает на стороне домена. Тесты гоняют FakeBot — живых вызовов нет.
- **A5** ✅ `test_alert_admin_sends_to_admin_id`, `..._noop_when_admin_unset`,
  `..._failure_does_not_raise_or_loop`, `test_persistent_transient_alert_failure_no_infinite_loop`.
  Алерт не зацикливается (одна попытка к админу).

**Вырожденных/недостающих A-проверок не обнаружено.** Каждый критерий закрыт тестом, проверяющим
именно утверждение (claim+advance, реальные hidden_from-строки, реальные aiogram-исключения, per-recipient
расхождение текстов), а не тавтологичный assert.

## S3.1-wiring — проверено отдельно (закрытие findings 1–2 интеграции)
- **F6.2 push «партнёр изменил список»:** `test_list_change_wiring.py` драйвит РЕАЛЬНЫЕ Lists-роуты
  (`post_list/post_item/patch_item/delete_item/delete_list/post_clear_done`) и проверяет эмитированный
  `ListChangeData` (event/scope/family/hidden_from/item_text/снапшоты при delete; `clear_done` НЕ эмитит).
  Фабрика-листенер мапит → notify_list_change (скрытый подарок не уходит исключённому, актор исключён).
- **Сид расписаний:** `seed_default_schedules` зовётся из обеих точек создания семьи
  (`kernel_api.post_family`, `bot.cmd_newfamily`); `join` не сидит — корректно. Тест `test_analytics_seed.py`.

## Findings
1. **[низкая] Q-rem-2 не закрыт по букве OQ-R2 («грейс в конфиг»).** `main.py:174` регистрирует
   `make_reminder_poll(async_session_maker, notifier)` — грейс берётся из дефолта фабрики
   `DEFAULT_GRACE_SECONDS = 3ч`, поля в `app/config.py` нет. Функционально корректно (дефолт = спека),
   но «в конфиг» (OQ-R2) формально не выполнено. Зарегистрировано как follow-up. **Не блокер.**
2. **[косметика] `run_migrations` на Windows печатает `UnicodeDecodeError`** при декоде stderr alembic
   в cp1251 (`subprocess.run(..., encoding="utf-8")` без `errors="replace"`). Подтверждено как НЕ блокер:
   миграции применяются, boot проходит, single head. Пред-существующий артефакт, не волна B. One-liner
   (`errors="replace"`) — на усмотрение С.
3. **[инфо] Q-an-3 / Q-an-4 — принятые обходы в коде Analytics.** top-3 трат = сумма факта по
   категории-листу за период (модель не хранит отдельные транзакции — это ограничение MVP, не лень);
   усечение >4096 — `_truncate_telegram` в Analytics (целевой путь — единая ответственность Notifications).
   Оба помечены в докстрингах (`digest.py`) и парковке. Легитимны.

## Парковки — оценка
- **Q-rem-1** (порт Notifier) — ✅ закрыта монтажом: порт + adapter работают (тесты доставки кнопок/kind).
- **Q-del-1** (change-хук Lists) — ✅ реально закрыта S3.1 (код + `test_list_change_wiring.py`), не на словах.
- **Q-del-2** (rem→reminder_service) — ✅ закрыта (`test_delivery_callbacks.py` + регистрация в main.py).
- **Q-an-1 / Q-an-2** (recent_changes / goals) — ✅ закрыты вариантом B (read-проекции + тесты), обходы сняты.
- **Q-an-5** — ✅ снят корректно (ListType не нужен после recent_changes).
- **Q-rem-2 / Q-an-3 / Q-an-4** — легитимные follow-up / принятые обходы (реальная недоспецификация
  или ограничение MVP-модели), помечены в коде. Не лень.

## Условия приёмки
Нет (вердикт ✅). Findings 1–3 — follow-up/косметика вне блокеров; коммит волны B — за С.
