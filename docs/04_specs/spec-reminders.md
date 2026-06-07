# ТЗ — Reminders (напоминания + планировщик)

**Версия:** 0.3 (принято; учтён аудит F2.1)
**Статус:** ✅ Принято (С./Катя, 2026-06-07)
**Дата:** 2026-06-07
**Опирается на:** `MVP_Requirements.md` §4.4/§5 · `DOMAIN_MODEL.md` §2/§3.5/§3.7/§4 · **`ADR-0003`** (планировщик: poll-loop, `Reminder`=SSOT, misfire, recurrence) · `ADR-0004` (изоляция) · `ADR-0005` (SQLite→Postgres) · ТЗ Identity (`Shareable`)
**Закрывает:** контекст «Reminders» + action item ADR-0003 №3 (поля `Reminder`, переходы, грейс-окно, ack/snooze).
**Грунт:** ADR-0003, req §4.4, DOMAIN_MODEL §3.5; существующий `services/notification_service.py` (доставка в Telegram — переиспользуется как шов к Notifications). Код напоминаний — **новый** (current-state нет).

---

## 1. Назначение и границы контекста

Reminders — создание, просмотр и **срабатывание** напоминаний с push-доставкой. В MVP — **generic** (текст + время/recurrence + владелец), без жёсткой привязки к сущностям других доменов (DOMAIN_MODEL §3.5; глубокие связи — North Star). Зависит от kernel (идентичность/видимость) и от Notifications (доставка).

**Входит:** доменная сущность `Reminder` (SSOT расписания); **планировщик** — async poll-loop в общем event-loop (ADR-0003); recurrence (`none/daily/weekly/interval`); политика misfire (грейс-окно); ack/snooze; REST Mini App (CRUD + ack/snooze). Тот же loop предоставляет **периодический триггер ретро-дайджеста** (состав/каденс — за Analytics/Катей).

**НЕ входит:** сама доставка push (контекст Notifications — ретрай, алерт админу); композиция дайджеста (Analytics); RRULE/сложные правила, привязка к спискам/финансам/еде (North Star).

**Маппинг:** новый домен. Планировщик и LLM-разбора напоминаний в current-state нет; доставка переиспользует механику `notification_service` (Telegram-отправка с обработкой блокировок).

---

## 2. Глоссарий контекста
- **Reminder** — напоминание: текст + расписание (`next_fire_at` + recurrence) + владелец. **SSOT** расписания.
- **Срабатывание (fire)** — момент, когда `next_fire_at <= now`: доставка push + продвижение/закрытие.
- **Recurrence** — повторяемость: `none | daily | weekly | interval(N)`.
- **Misfire** — пропуск срабатывания (процесс лежал); политика — грейс-окно.
- **Грейс-окно** — интервал, в пределах которого просроченное срабатывает один раз («догнать»); за окном — `MISSED`.
- **ack / snooze** — «выполнено» / «отложить» из push.
- scope, viewer — см. ТЗ Identity.

---

## 3. Доменная модель

### 3.1 Reminder — напоминание (SSOT, Shareable)
| Поле | Тип | NULL | Описание / инвариант |
|---|---|---|---|
| `id` | int PK | нет | — |
| `owner_id` | FK→User | нет | **Shareable.** Создатель/владелец. |
| `family_id` | FK→Family | нет | **Shareable.** Семья. |
| `scope` | enum | нет | **Shareable.** `PERSONAL` (витамины — по умолчанию) / `FAMILY` (общая задача). |
| `text` | text | нет | Что напомнить. |
| `next_fire_at` | datetime | да | **Ядро расписания** (UTC). Время следующего срабатывания; NULL = завершено/одноразовое отработало. Index. |
| `recurrence` | enum | нет | `NONE` / `DAILY` / `WEEKLY` / `INTERVAL`. |
| `interval_seconds` | int | да | Для `INTERVAL` — шаг. Для `WEEKLY` день недели берётся из `next_fire_at`. |
| `status` | enum | нет | `ACTIVE` / `DONE` / `MISSED` / `CANCELLED`. |
| `last_fired_at` | datetime | да | Последнее фактическое срабатывание. |
| `created_at` | datetime | нет | — |
| `updated_at` | datetime | нет | `onupdate`. |

> Состояние срабатывания держится **на самом `Reminder`** (`next_fire_at`/`status`/`last_fired_at`) — отдельной таблицы-журнала в MVP **нет** (решено R3: минимум, `Reminder`=SSOT; fire-log добавляется тривиально при необходимости аудита). Переживает рестарт/деплой (ADR-0003).

---

## 4. ERD

```mermaid
erDiagram
  USER ||--o{ REMINDER : "владеет (owner_id)"
  FAMILY ||--o{ REMINDER : "принадлежит"

  REMINDER {
    int id PK
    int owner_id FK
    int family_id FK
    string scope "PERSONAL|FAMILY"
    string text
    datetime next_fire_at
    string recurrence "NONE|DAILY|WEEKLY|INTERVAL"
    int interval_seconds
    string status "ACTIVE|DONE|MISSED|CANCELLED"
    datetime last_fired_at
  }
```

Намеренно одна сущность (generic-напоминания MVP). Связей с List/Finance/Food **нет** (North Star).

---

## 5. Инварианты и бизнес-правила
- **I1. (SSOT)** `Reminder.next_fire_at` — единственный источник расписания; планировщик не держит состояние в памяти → рестарт/деплой переживается без потерь/дублей (ADR-0003).
- **I2. (одноразовое)** `recurrence=NONE`: после срабатывания → `status=DONE`, `next_fire_at=NULL`.
- **I3. (повторяемое)** после срабатывания `next_fire_at` продвигается по recurrence (daily +1д, weekly +7д, interval +`interval_seconds`); `status` остаётся `ACTIVE`.
- **I4. (без двойного срабатывания)** один процесс + переход состояния (`claim → fired`/advance) в **одной транзакции**.
- **I5. (misfire)** просроченное в пределах грейс-окна срабатывает **один раз** («догнать»); за окном — `status=MISSED` (повторяемое затем продвигается к ближайшему будущему слоту, без «шторма»).
- **I6. (видимость/доставка)** `PERSONAL` → push владельцу; `FAMILY` → push обоим. Видимость в Mini App — kernel `visible_to` (PERSONAL — владельцу; FAMILY — обоим).
- **I7. (гранулярность)** минутной точности достаточно; опрос раз в N сек (конфиг 30–60).
- **I8. (доставка не здесь)** фактическая отправка, ретрай и алерт админу при стойком фейле — контекст Notifications (ТЗ Notifications); Reminders лишь инициирует доставку.

---

## 6. Срабатывание через generic-планировщик (ADR-0003 + аудит F2.1)

Механизм срабатывания — **job в общем generic-планировщике** (top-level `scheduler/` — инфра, peer к `notifications/`; знает только kernel). Reminders отдаёт callable «опросить созревшие → доставить»; тик планировщика в общем event-loop его вызывает. Своего loop/демона у Reminders нет; брокеров нет (ADR-0003, frozen §5). Регистрация job — в `main.py` (композиция). Сам `scheduler/` специфицируется в build-roadmap (инфра-слой).

**Тик (раз в N сек, конфиг 30–60):**
1. Выбрать «созревшие»: `SELECT … WHERE status=ACTIVE AND next_fire_at <= now()`.
2. Для каждого, в **одной транзакции**:
   - проверить грейс (now − next_fire_at): в окне → доставить через Notifications; за окном → пометить `MISSED` (без доставки);
   - `last_fired_at = now`;
   - продвинуть `next_fire_at` по recurrence (`I3`) либо закрыть одноразовое (`DONE`, `I2`);
3. Доставка через **Notifications** (recipients по scope, I6) — push с кнопками **✅ Выполнено** (ack) и **⏰ Отложить 1 ч** (snooze) — **обе** (решено R1).

**Догон при старте:** на первом тике после рестарта те же правила применяются к накопившимся просрочкам (грейс-окно отсекает старое).

**Дайджест — отдельная job** (владелец — Analytics), регистрируется в том же generic-планировщике из `main.py`. Reminders её **не знает** и Analytics **не импортирует** (исправление аудита F2.1: ребро `reminders→analytics` запрещено ADR-0004). Один планировщик на обе job — связывает их composition-слой, а не контексты. Каденс/состав дайджеста — ТЗ Analytics.

Грейс-окно — **конфиг, дефолт 3 ч** (решено R2).

---

## 7. FSM срабатывания

```mermaid
stateDiagram-v2
  [*] --> ACTIVE : создан (next_fire_at в будущем)
  ACTIVE --> ACTIVE : fire в грейсе + recurrence≠NONE (advance next_fire_at)
  ACTIVE --> DONE : fire + recurrence=NONE  (или ack одноразового)
  ACTIVE --> MISSED : просрочено сверх грейса
  MISSED --> ACTIVE : recurrence≠NONE (продвинут к будущему слоту)
  ACTIVE --> ACTIVE : snooze (next_fire_at = now + N)
  ACTIVE --> CANCELLED : пользователь удалил/отменил
```

- **ack** «✅ Выполнено»: одноразовое → `DONE`; повторяемое → подтверждает текущее срабатывание (расписание не трогает).
- **snooze** «⏰ Отложить N»: `next_fire_at = now + N`, `status=ACTIVE`.

---

## 8. Прикладные сценарии
- **S1. Витамины (recurring).** `POST /reminders {text:"Витамин D", time:09:00, recurrence:DAILY, scope:PERSONAL}` → каждый день в 9:00 push владельцу; «✅ Выполнено» подтверждает.
- **S2. Разовая задача.** `recurrence:NONE` → один push в срок → `DONE`.
- **S3. Общая (FAMILY).** «Оплатить аренду 1-го числа» (`scope:FAMILY`) → push обоим.
- **S4. Misfire.** Процесс лежал 20 мин в момент срабатывания → на старте догоняет в грейсе (один push). Лежал сутки при грейсе 3 ч → `MISSED`, следующее (если recurring) — к ближайшему будущему слоту.
- **S5. Snooze.** Push → «Отложить 1 ч» → `next_fire_at=now+1ч`.

---

## 9. REST-контракт

Под `/api/v1/reminders`, аутентификация — kernel `current_viewer`; видимость — `visible_to` (PERSONAL/FAMILY); ru. Ack/snooze доступны и из REST, и из callback push-кнопок (бот → тот же сервис).

| Метод | Путь | Назначение | Запрос | Ответ | Ошибки |
|---|---|---|---|---|---|
| GET | `/reminders` | Видимые напоминания (`?status=`) | — | `[ReminderDTO]` | 401,403 |
| POST | `/reminders` | Создать | `{text, fire_at, recurrence, interval_seconds?, scope?}` | `ReminderDTO` | 401,403,422 |
| GET | `/reminders/{id}` | Одно | — | `ReminderDTO` | 401,403,404 |
| PATCH | `/reminders/{id}` | Правка (текст/время/recurrence/scope) | `{…}` | `ReminderDTO` | 401,403,404,422 |
| DELETE | `/reminders/{id}` | Удалить (→ `CANCELLED`) | — | `204` | 401,403,404 |
| POST | `/reminders/{id}/ack` | Выполнено | — | `ReminderDTO` | 401,403,404 |
| POST | `/reminders/{id}/snooze` | Отложить | `{minutes}` | `ReminderDTO` | 401,403,404,422 |

**DTO:**
- `ReminderDTO` = `{id, text, next_fire_at, recurrence, interval_seconds?, scope, status, last_fired_at, owner_id, created_at}`
- Создание: `recurrence ∈ {none,daily,weekly,interval}`; для `interval` обязателен `interval_seconds`; `fire_at` задаёт первый слот (для weekly — и день недели).

---

## 10. Аутентификация, авторизация, изоляция
- Аутентификация — kernel (`current_viewer`).
- Видимость/мутации — `scope` (PERSONAL — владелец; FAMILY — оба, kernel OQ-2 «совместное владение»).
- Standalone-шов: домен и планировщик Telegram-агностичны; доставка вынесена в Notifications (ADR-0004).

**Структура пакета (ADR-0004):**
```
app/contexts/reminders/
  models/         # Reminder (+ Shareable из kernel)
  repositories/   # reminder_repo (выборка созревших по next_fire_at)
  services/       # reminder_service (CRUD, ack/snooze), reminder_poll (job: опрос созревших → доставка; recurrence, misfire)
  api/            # REST Mini App
```
**Импорты:** `contexts.reminders → kernel + notifications` (доставка; ребро Rem→Notif из графа §2) **+ generic `scheduler`** (регистрация job). `analytics`/`delivery` **не** импортируются. Generic-планировщик — top-level `scheduler/` (инфра); Reminders отдаёт job-callable, регистрирует `main.py` — поэтому ребра `reminders→analytics` нет (аудит F2.1).

---

## 11. Edge-cases и обработка ошибок
- **Рестарт/деплой в момент срабатывания** → состояние в БД (I1); на старте догон в грейсе (I5).
- **Долгий простой** (> грейс) → `MISSED`, без «шторма» повторов; recurring продвигается к будущему.
- **Недоставка push** (бот заблокирован / Telegram-ошибка) → обрабатывает Notifications (лог + ретрай + алерт админу при стойком фейле, ADR-0003); срабатывание не зацикливается.
- **Снуз за пределы следующего слота** (recurring) → ближайшее из (snooze-время, штатный слот).
- **Двойной тик / гонка** → транзакционный claim (I4), один push.
- **scope=FAMILY, один из партнёров заблокировал бота** → второму доставляется; фейл логируется (Notifications).

---

## 12. Нефункциональные аспекты
- **Один процесс/loop, без брокеров** (frozen §5); планировщик — часть приложения.
- **Переживание рестарта** — состояние только в БД; тесты обязательны (пропуск/повтор/рестарт/грейс — ADR-0003).
- **DB-агностичность** (`next_fire_at` — datetime; без БД-специфики) — переезд SQLite→Postgres не ломает (ADR-0005).
- **Объём крошечный**, минутная точность; опрос N сек. Локализация ru; standalone-шов.
- **Таймзона (аудит F6.4):** `next_fire_at` в UTC; локальное время задаётся в `owner.timezone` (kernel) при создании; recurrence в MVP продвигается фиксированной дельтой — оба пользователя в одной зоне, DST-коррекция — North Star.

---

## 13. Критерии приёмки
- **A1.** Создание/просмотр/правка/удаление напоминаний через REST; recurrence `none/daily/weekly/interval`.
- **A2. (срабатывание)** Созревшее доставляется один раз; recurring продвигает `next_fire_at`; одноразовое → `DONE`.
- **A3. (рестарт)** Перезапуск процесса между созданием и сроком не теряет и не дублирует срабатывание (состояние в БД).
- **A4. (misfire)** Просрочка в грейсе догоняется один раз; сверх грейса → `MISSED`; recurring продвигается к будущему слоту.
- **A5. (ack/snooze)** «Выполнено» закрывает одноразовое; «Отложить N» сдвигает `next_fire_at`; работает и из push-кнопки, и из REST.
- **A6. (видимость/доставка)** PERSONAL → владельцу, FAMILY → обоим; доставка идёт через Notifications.
- **A7. (изоляция)** домен не импортирует `delivery`; планировщик в одном event-loop; import-linter зелёный.

---

## 14. Решённые вопросы (закрыты С./Катя, 2026-06-07)
- **OQ-R1 — push-кнопки:** **обе** — `✅ Выполнено` (ack) + `⏰ Отложить 1 ч` (snooze).
- **OQ-R2 — грейс-окно misfire:** в **конфиг**, дефолт **3 ч**.
- **OQ-R3 — журнал срабатываний:** **только состояние на `Reminder`** (без отдельной таблицы); fire-log — при необходимости аудита позже.

> Решено инлайн (ADR-0003): механизм — poll-loop, `Reminder`=SSOT; recurrence `none/daily/weekly/interval` (RRULE — North Star); доставка — через Notifications. Каденс/состав дайджеста — вход ТЗ Analytics (Катя), не здесь.

---

## 15. История изменений
- **0.3 (2026-06-07):** аудит F2.1 — планировщик вынесен в generic top-level `scheduler/`; Reminders отдаёт job-callable, дайджест-job (Analytics) регистрируется в `main.py`; ребро `reminders→analytics` устранено. §6/§10 обновлены.
- **0.2 (2026-06-07):** закрыты OQ-R1/R2/R3 — обе push-кнопки (ack+snooze 1 ч); грейс-окно в конфиг, дефолт 3 ч; состояние только на `Reminder` (без журнала). Статус → принято.
- **0.1 (2026-06-07):** первый черновик ТЗ Reminders. Зафиксированы: `Reminder` (SSOT — `next_fire_at`/recurrence/status), планировщик-poll-loop в одном event-loop (ADR-0003), misfire-грейс, FSM срабатывания (ACTIVE/DONE/MISSED/CANCELLED), ack/snooze, REST + DTO, шов доставки к Notifications, структура пакета. 3 OQ (push-кнопки, грейс-дефолт, журнал срабатываний). Триггер дайджеста предоставляется здесь; состав — ТЗ Analytics.
