# Ревью реализации: код ↔ документация — 2026-06-20

**Что это:** read-only сверка кода `app/**` с принятыми ТЗ (`docs/04_specs/*`, v0.4/R1). Ничего не правилось.
**Метод:** 5 субагентов по контекстам (kernel+delivery+infra · lists+analytics · finance · nutrition+resolve · reminders) + ручная сверка структуры, миграций, import-linter, тестов. Тесты не запускались (read-only) — оценка по чтению кода.
**SSOT при расхождении:** ТЗ. Этот файл — статус реализации, не правка.

---

## 1. Резюме

- **Этап кода:** MVP — срезы **S0–S4 выполнены** + **Nutrition R1**. Отгружено на VPS, тестируется. Git HEAD — пост-MVP фиксы + `ecebaab` (правки доков).
- **Вердикт:** ядро (kernel) и **5 из 7 доменов** реализованы полно и точно по ТЗ. **2 домена — Lists и Reminders — застыли на v0.3:** дельты MVP-фидбэка партии №1 (2026-06-18, «Поток 2») в коде ещё **не построены**. Nutrition R2 (web/Compound) и barcode отложены **по самому ТЗ** (§16). Блокеров в реализованном нет.
- **Главный разрыв:** документация доведена до **v0.4**, код — на **v0.3** по Lists/Reminders. Это согласованный «долг Потока 2», а не баг.

---

## 2. Статус по этапам / слоям

| Срез | Содержание | Статус кода |
|---|---|---|
| S0 | Скаффолд `app/`, перенос инфры, import-linter, generic `scheduler/` | ✅ выполнено |
| S1 | kernel (Identity v0.5): User/Family/Membership/Invite, Shareable/visible_to, auth, pairing, REST | ✅ выполнено, полное соответствие |
| S2 | write-side: Lists, Finance, Nutrition | ✅ выполнено (Lists — на базе v0.3) |
| S3 | read-side/delivery: Analytics, Notifications, scheduler-job, main-склейка | ✅ выполнено |
| S4 | фронт Mini App (React UMD) | ✅ присутствует по всем доменам (lists/finance/reminders/goals/screens.jsx…) |
| R1 | Nutrition retrieval (OFF + estimate, провенанс, кэш) | ✅ выполнено, полное соответствие |
| «Поток 2» | Lists v0.4 + Reminders v0.4 (MVP-фидбэк 2026-06-18) | ⛔ **НЕ начато в коде** |
| R2 / barcode | web-ступень Compound / чтение штрихкода | ⏸ отложено по ТЗ §16 |

---

## 3. Conformance по контекстам

| Контекст | Версия кода | Полнота vs ТЗ | Ключевое |
|---|---|---|---|
| **kernel (Identity)** | v0.5 | ✅ полно | Membership-таблица ЕСТЬ (`kernel/models/family.py:31`, не упрощена к `User.family_id`); Shareable=Scoped+Hideable, `visible_to` = WHERE + NOT EXISTS (H3-a); auth (HMAC `WebAppData`, max-age 30д); pairing-гварды I1–I6, атомарный `try_consume` (compare-and-swap); REST 5/5 |
| **Notifications** | v0.3 | ✅ полно | `deliver/alert_admin`, политика успех/Forbidden/BadRequest/транзиент-1-ретрай-алерт, recipients по scope, callback `rem:ack/snooze`, усечение >4096 |
| **Scheduler** | — | ✅ полно | generic top-level, poll-loop 60с, ноль app-зависимостей; reminder-poll + digest как 2 независимые job (F2.1) |
| **delivery + main** | — | ✅ полно | один event-loop (aiogram polling + aiohttp + scheduler); Notifier(bot) DI; обе job в `main.py:174,176`; lists-change listener (инверсия зависимости) |
| **Analytics** | v0.4 | ✅ полно | `DigestSchedule` мульти-каденс (UQ family+cadence, дефолт 23:59/вс 23:00); чтение только через `contexts.*.read`; digest без напоминаний; не зависит от reminders (линтер) |
| **Finance (+Import ACL)** | v0.4 | ✅ полно | модель/алиасы/NEEDS_REVIEW; ACL: заливка `FFF9CB9C` в кол.E, I3 (родитель=Σдетей И ребёнок=Σдней, ≤1₽, дневная строка родителя не валидируется), идемпотентный upsert, fail-loud; `metric` снят; экспорт xlsx; FAMILY по `WHERE family_id`; openpyxl заперт в importing/exporting |
| **Nutrition (+resolve R1)** | v0.2.3 / R1 | ✅ полно | source=провенанс + `source_ref`, `SharedFood.family_id`, `NutritionGoals` 1:1, DishIngredient CHECK+CASCADE; resolve лестница label→OFF→estimate, extractor 1 LLM-вызов мульти, кэш в SharedFood; resolve — leaf (инжектится в FoodService) |
| **Lists** | **v0.3** ⛔ | ⚠ структура полна, **v0.4-дельта нет** | List/ListItem/History/hidden_from, двухуровневая видимость, кап истории 50, REST 12/12, поиск по названиям, инварианты I1–I9 — **всё v0.3**. v0.4 (priority-enum, LINK comment, tags) **не реализовано** |
| **Reminders** | **v0.3** ⛔ | ⚠ ядро полно, **v0.4-дельта нет** | poll-loop/misfire-грейс/recurrence/ack/snooze/scope-доставка/REST 7/7 — **всё v0.3**. v0.4 (max_occurrences/until/fired_count/delivery_target, I9, I10) **не реализовано** |

---

## 4. Главные разрывы код ↔ ТЗ («Поток 2» не построен)

### 4.1 Lists v0.4 — НЕ реализован (`specific_attrs.py:14,19`)
- 🔴 TASK `priority` — свободный `str` вместо enum `{LOW,MEDIUM,HIGH,URGENT,CRITICAL}`; enum-класса `TaskPriority` в `models/enums.py` нет.
- 🔴 LINK — ключ `title` вместо `comment` (v0.4-семантика «text=Заголовок + comment»).
- 🟡 `tags: [str]` для GIFT/IDEA/LINK (Q1-А9) — отсутствует.
- Сопутствующее: `tests/test_lists.py` (17 тестов) и схема `specific_attrs` в openapi — тоже на v0.3.

### 4.2 Reminders v0.4 — НЕ реализован (grep по app/tests/alembic = 0 совпадений)
- 🔴 Поля модели: `max_occurrences`, `until`, `fired_count`, `delivery_target` + enum `DeliveryTarget` — отсутствуют (`models/reminder.py`/`enums.py`).
- 🔴 I9 (остановка повторений) и инкремент `fired_count` — нет в `reminder_poll._advance_after_fire` (повторяемое всегда остаётся ACTIVE).
- 🔴 I10 / `delivery_target`-маршрутизация — `recipients.resolve_recipients` маршрутизирует только по `scope` (нет ветки PARTNER).
- 🔴 REST: POST/PATCH не парсят новые поля; `_dto` без них; S1/S2-валидаций (422) нет.
- 🔴 Alembic-ревизии «+4 колонки» нет (схема создана `933845dd7c61`, 12 v0.3-колонок).
- 🔴 Тесты A8/A9 — отсутствуют.

### 4.3 openapi ↔ код (производный разрыв)
`openapi.yaml` в ходе док-ревью 2026-06-20 уже дополнен полями Reminders v0.4 (`delivery_target`/`max_occurrences`/`until`/`fired_count`), а код REST/DTO — на v0.3. То есть **контракт (openapi) впереди реализации** — ожидаемое следствие того, что доки на v0.4, код на v0.3. Сверять при приёмке Потока 2.

---

## 5. Отложено по ТЗ (НЕ разрыв)

- **Nutrition R2** (web-ступень Groq Compound): `compound_web.py` нет, конфигов `groq_compound_model`/`compound_timeout_s` нет — строго по ТЗ §16 (R2 добавляется кодом позже). `source="web"` зарезервирован в enum.
- **Barcode** (fast-follow): не извлекается, OFF by-barcode не вызывается — по ТЗ §16.
- **PostgreSQL**: SQLite (ADR-0005, миграция по триггеру) — `asyncpg` в requirements закомментирован.
- **North Star** (ФНС/чеки, Huawei/медблок, standalone) — вне MVP.

---

## 6. Отклонения и мёртвый код (реализованного)

- 🟡 **Транзит-код S0 — мёртвый:** `app/handlers/` (269 loc), `app/repositories/` (136), `app/services/` (260), `app/db/models.py` (пустой стаб). Самозамкнут, ни один живой entrypoint не импортирует (подтверждено grep), намеренно исключён из import-linter (`pyproject.toml:6-8`). Функциональность давно в kernel. Под удаление (риск: случайный импорт устаревшего `User`/`UserRepository`).
- 🟡 **import-linter:** `notifications` и `scheduler` в `layers` стоят соседними слоями, а не siblings — реальных нарушений не маскирует (оба тянут только kernel), но менее точно отражает граф ADR-0004.
- ⚪ **Finance:** (а) UNIQUE на корне категорий не ловит дубль на SQLite/Postgres (NULL `parent_id`) — защищено логикой маппера, для North Star ручного ввода; (б) `AnalyticsDTO.plan_applicable` — полезное поле, не задокументировано в openapi; (в) `MonthDTO.imported_at` всегда `null`, per-month `categories` упрощён (помечено «North Star» в коде).
- ⚪ **kernel:** гонка двух `join` на последнее место даёт 410 (InviteUnusable) вместо буквального 409 из §12 — крайний кейс на 2 пользователей.
- ⚪ **Nutrition:** docstring `save_entries` упоминает `text|photo|db` как вокабуляр `source`, хотя по v0.2.3 способ ввода в `source` не кодируется (поведение верное, коммент отстаёт).

---

## 7. Тесты и миграции

- **Тесты:** ~232 тест-функции, ~28 файлов. Покрытие добротное и адресное (kernel: auth/pairing/visibility/dto; lists 17; analytics ~37; finance 3 файла на реальном `Бюджет общий 2026.xlsx`; nutrition 62; notifications/delivery/scheduler; `test_architecture.py` гоняет import-linter как pytest). **v0.4 (Lists/Reminders) не покрыт** — закономерно.
- **Миграции:** 6, линейная цепочка, **single head**: `237ca7b0e901` (S1 baseline) → `b1f2a3c4d5e6` (S2a Lists) → `8c1ef1bb5136` (S2 Finance+Nutrition) → `933845dd7c61` (S3 Reminders+Analytics) → `e7a1c0ffee01` (R1 провенанс) → `f4c2a9d7b1e3` (composite dish). **Миграции «Потока 2» (Lists/Reminders v0.4) нет.**
- **requirements:** aiogram3 / SQLAlchemy2 / aiosqlite / alembic / groq / openpyxl / tzdata / import-linter / pytest — соответствует ТЗ/ADR.

---

## 8. Вывод

Код находится на этапе **MVP (S0–S4) + Nutrition R1** и по реализованному объёму **точно соответствует ТЗ** (kernel + Finance + Nutrition/R1 + Analytics + Notifications + scheduler + delivery — без значимых отклонений; границы держит import-linter + ~232 теста).

**Чтобы код догнал документацию, в порядке приоритета:**
1. **«Поток 2»** — реализовать Lists v0.4 (priority-enum, LINK `comment`, `tags`) и Reminders v0.4 (4 поля + I9/I10 + REST/валидации + одна Alembic-ревизия «+4 колонки» + тесты A8/A9). Это единственный разрыв «док впереди кода».
2. Синхронизировать `openapi.yaml`/`test_lists.py` под v0.4 параллельно.
3. **R2/barcode** Nutrition (по ТЗ §16) — следующий инкремент.
4. Чистка: удалить мёртвый транзит-код (`app/handlers`, `app/repositories`, `app/services`, `app/db/models.py`).

_Код в этой проверке не изменялся (read-only)._
