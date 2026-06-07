# Аудит фазы ТЗ — кросс-консистентность (2026-06-07)

**Метод:** независимый субагент перечитал 7 ТЗ + 5 ADR + requirements + DOMAIN_MODEL свежими глазами; параллельно — извлечение сшивок (REST-префиксы, импорт-рёбра, read-модули, употребление scope/hidden_from). Цель — поймать расхождения до фазы кода.

**Вердикт:** 7 ТЗ зрелые и кросс-консистентны по сути — семантика видимости, покрытие требований (без scope-крипа), REST-аутентификация и read-модульный контракт Analytics согласованы. Найдено: **1 блокер · 7 минор · 7 док-синк · остальное OK**. К коду готовы **с условиями** (ниже).

---

## 🔴 Блокер (закрыть до кода соответствующего среза)

### F2.1 — Триггер дайджеста нарушает граф импортов (ADR-0004)
**Суть.** Планировщик описан внутри `contexts/reminders/services` (Reminders §6/§10) и «дёргает Analytics-композицию» — проверяет `DigestSchedule` и вызывает `compose_digest` (Analytics §7). Но:
- Reminders §10 импортирует **только** `kernel + notifications` (analytics — нет);
- ADR-0004 запрещает `contexts.* → analytics` (и вообще sibling-контексты);
- в графе `DOMAIN_MODEL §2` ребра Reminders↔Analytics **нет**.

Как написано → нужен импорт `reminders → analytics`, который падает в import-linter. **Это реальное противоречие, а не формулировка.**

**Рекомендуемое решение (требует нода С.):** инвертировать через композиционный слой.
- Выделить **generic-планировщик** (тонкий top-level `scheduler/`, peer к `notifications/`): периодический тик + реестр job; знает только про `kernel`, не про контексты.
- `contexts/reminders` отдаёт job-callable «опросить созревшие напоминания → доставить» (зависит kernel+notifications).
- `analytics` отдаёт job-callable «проверить `DigestSchedule` → compose → доставить» (зависит kernel+`*.read`+notifications).
- `main.py` (композиция) создаёт scheduler и **регистрирует обе job**. Рёбер `reminders→analytics` нет; оба зависят от scheduler-инфры.

Совместимо с ADR-0003 (один poll-loop в общем event-loop, `Reminder`=SSOT остаётся) — это уточнение размещения, не смена решения. Затронет Reminders §6/§10, Analytics §7/§10 + по строке в ADR-0003/0004.

---

## 🟡 Минор (уточнения механизма; не блокируют старт write-side)

| # | Измерение | Находка | Файлы |
|---|---|---|---|
| F1.2 | Видимость | Finance не несёт литеральный `Shareable`: `Category`/`IncomeSource` — только `family_id`; `BudgetPlan`/`ActualAmount`/`Income` видимость наследуют через FK. `visible_to` к ним прикладывается не напрямую — `finance.read` скоупит по семье через join. Механизм применения видимости к Finance до-специфицировать в коде. | finance §4, §11; identity §3.5/§7 |
| F1.3 | Видимость | Nutrition использует current-state-колонки (`user_id`/`created_by`) вместо `owner_id` миксина; `scope`-колонки нет (PERSONAL/FAMILY заданы неявно). Нужна явная привязка миксин-поле ↔ колонка. | nutrition §4 |
| F4.4 | ADR-0005 | ADR-0005 (action #2) хотел поиск списков за абстракцией (SQLite FTS5→Postgres FTS); Lists §9/§14 даёт обычный `LIKE` без сменяемой абстракции. Портируемо, но намерение ADR не перенесено. | ADR-0005; lists §14 |
| F6.1 | Пробел (зонтичный) | `delivery/bot` (вход: `/start`, запуск Mini App, generic-диспетчер callback'ов) и `main.py` (композиция event-loop, DI `Bot`, старт scheduler+Notifications) **референсятся многими ТЗ, но не специфицированы ни одним**. Осознанный инфра-пробел → явный пункт роадмапа (здесь же решается F2.1 и F6.2). | kernel §6; notif §1/§7; rem §6/§10 |
| F6.2 | Пробел | Real-time change-уведомления списков (Notifications OQ-No1) **не упомянуты в Lists**; Lists импортирует только kernel и сам эмитить не может. Легальный путь — через `delivery` (`delivery→lists` + `delivery→notifications`), но шов не назначен. | notif §7/§9; lists (отсутствует) |
| F6.3 | Пробел | Фронтенд Mini App (новые экраны Lists/Finance/Reminders/Analytics) не специфицирован — есть только REST-контракты (backend-first by design). Сборка фронта — пункт роадмапа. | — |
| F6.4 | Тайминг | (a) Analytics `DigestSchedule.time_of_day` «в таймзоне семьи», но `timezone` — атрибут **User** (kernel §3.1); `Family.timezone` не определён. (b) Reminders `next_fire_at` в UTC, продвижение recurrence фиксированной дельтой, без ссылки на `user.timezone`/DST. Для двух юзеров в одной зоне безвредно, но семантика не зафиксирована. | analytics §3.1; rem §3.1/§6; identity §3.1 |

---

## 🔵 Док-синк (привести SSOT к уже принятым решениям; до раздачи субагентам)

| # | Находка | Файлы |
|---|---|---|
| F4.1 | **ADR-0001 + DOMAIN_MODEL §4** всё ещё описывают H3 как единую таблицу `shareable_hidden_from`; kernel выбрал **H3-a** (таблица-на-сущность) и явно отверг единую форму. ADR одобряет вариант, который kernel отклонил → синхронизировать на H3-a. | ADR-0001 (9/123-125/138); DOMAIN_MODEL §4; identity §7/§15; lists §4.4 |
| F1.1 | **kernel §10** описывает `VisibilityDTO` как единый бандл `{scope,hidden_from}` с централизованной валидацией, но §7 (v0.4) уже допускает раздельные уровни, и Lists передаёт `scope` и `hidden_from` раздельно. §10 не синхронизирован с §7; валидация I7/I8 для элемента — кросс-сущностная (нужен сервис), не чистый DTO. | identity §10 vs §7; lists §6/§10 |
| F4.5 | **requirements §5** всё ещё указывает `llama-3.1-8b-instant`; исправлено только в CLAUDE §9 и Nutrition (`llama-4-scout-17b`). | MVP_Requirements.md §5 |
| F4.3 | **ADR-0003 §под-решения / adr-README** рекомендуют включить сводку напоминаний в дайджест; продукт решил **исключить** (Analytics I5). Консистентно (делегировано продукту), но финальное «нет» не зафиксировано в ADR-0003. | ADR-0003 (63); adr/README (37) |
| F2.3 | **kernel §11** формулирует контракт как «contexts.* → только kernel», опуская легитимные рёбра `reminders/analytics→notifications`, `analytics→*.read` (они есть в ADR-0004 и в самих ТЗ). Формулировка недо-полна. | identity §11 |
| F3.3 | `ErrorDTO {code,message(ru)}` определён только в kernel §10; прочие ТЗ его не кросс-ссылают (подразумевается переиспользование). | все §10 |
| F4.2 | Чекбоксы Action Items в ADR-0001(#3/#4)/0002(#5)/0003(#2/#3/#4)/0004(#2/#3)/0005(#2/#3) фактически выполнены, но стоят `[ ]` — трекер вводит в заблуждение. | секции Action Items ADR |

---

## ✅ OK (проверено, расхождений нет)
- **Покрытие требований** §4.1–4.5/§6 — каждая фича в своём ТЗ; **out-of-scope §7 не нарушен** (ФНС, ввод транзакций, сравнение цен, разбивка по картам — все North Star, не строятся).
- **read-модульный контракт:** Lists/Finance/Nutrition объявляют `read/`; Analytics импортирует ровно `contexts.{lists,finance,nutrition}.read`; Reminders/Notifications read-модуля не имеют, Analytics их не читает — дайджест-без-напоминаний согласован end-to-end.
- **Аутентификация** — везде kernel `current_viewer`; коды ошибок семантически едины.
- **ADR-0002** (импорт) и уточнение по заливке — синхронизированы в обе стороны. **ADR-0004/0005** корректно отражены во всех ТЗ.
- **REST-префиксы** — `/api/v1/<контекст>/` (минорные оправданные отклонения: kernel `/me`,`/family`; Lists `/lists`+`/items`).

---

## Что сделать до кода (чеклист)

1. **[решение С.]** Закрыть **F2.1**: generic-scheduler + регистрация digest-job в `main.py` (расписать в роадмапе как инфра-срез вместе с F6.1).
2. **[роадмап]** Специфицировать инфра-слой: `delivery/bot` + `main.py` (F6.1), путь эмиссии change-уведомлений Lists через delivery (F6.2), фронт Mini App (F6.3), `Family.timezone`/DST-семантику (F6.4).
3. **[док-синк, перед субагентами]** F4.1 (H3-a в ADR-0001/DOMAIN_MODEL), F1.1 (kernel §10), F4.5 (req §5 модель), F4.3 (дайджест-без-напоминаний), F2.3 (kernel §11), F3.3 (ErrorDTO), F4.2 (чекбоксы ADR).
4. **[вход фазы кода]** F1.2/F1.3 (применение `Shareable`/`visible_to` к Finance и Nutrition на уровне колонок), F4.4 (поиск-абстракция или сознательно `LIKE`).

**Старт write-side (Lists/Finance/Nutrition) от kernel блокеров не имеет** — F2.1 и инфра-пробелы касаются среза Reminders/Analytics/Notifications/delivery.

---

## Статус устранения (2026-06-07)

**Закрыто правками артефактов:**
- **F2.1 (блокер)** — generic-планировщик top-level `scheduler/`; Reminders→v0.3 / Analytics→v0.3 переразведены, ребро `reminders→analytics` устранено; kernel §11 + ADR-0004 фиксируют легитимные рёбра.
- **F4.1** — H3-a синхронизирован: ADR-0001 (уточнение) + DOMAIN_MODEL §4 (v1.3.2).
- **F1.1** — kernel §10 (VisibilityDTO на разных уровнях); **F2.3** — kernel §11 (рёбра импортов). kernel→v0.5.
- **F4.3** — «дайджест без напоминаний» зафиксировано в ADR-0003 + adr/README.
- **F4.5** — модель Groq (`llama-4-scout`) в requirements §5.
- **F4.2** — чекбоксы Action Items ADR-0001/0003/0004 отмечены.
- **F1.2 / F1.3 / F4.4 / F6.4** — поясняющие ноты: Finance §11 (видимость по `family_id`), Nutrition §3 (маппинг `owner_id`), Lists §14 (`LIKE`, FTS→North Star), Analytics §3.1 + Reminders §12 (таймзона).

**Передано в build-roadmap (инфра-слой, не доменный ТЗ):**
- **F6.1** — спецификация `delivery/bot` + `main.py` + generic `scheduler/`.
- **F6.2** — путь эмиссии real-time change-уведомлений Lists через `delivery`.
- **F6.3** — сборка фронта Mini App (новые экраны Lists/Finance/Reminders/Analytics).

**Остаток:** F4.2-чекбоксы ADR-0002/0005, F3.3 (кросс-ссылка `ErrorDTO`) — косметика, не блокирует код.
