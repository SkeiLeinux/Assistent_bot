# Review — волна C (S4, фронт Mini App) — 2026-06-11

**Вердикт: ⚠️ принять с условиями** (рекомендация; финальное слово — за С.)

Read-only приёмка. Reviewer ничего не правил. Фронт построен качественно, против ФАКТИЧЕСКИХ
шейпов `routes.py`/`read`/`digest` (а не против устаревшего openapi) — все ключевые контракты
сошлись. Условие приёмки — одно и не-фронтовое: вне-границевые правки в `docs/06_program/**`.

---

## Механика (прогнал сам, не из отчёта)

- **pytest (полный):** `1 failed, 188 passed` (.venv, дата прогона 2026-06-11).
  - Единственный фейл: `tests/test_analytics_api.py::test_get_digest_preview` — `assert "finance" in spheres` падает на пустом наборе секций.
  - **Это НЕ регресс волны C.** Тест дёргает чистую бэкенд-композицию дайджеста (`get_digest_preview`) с окном, считаемым от **реального `today()`** (`period_bounds(daily, utcnow_naive())`), против фикстур, засеянных фиксированными датами июня-2026 (см. `test_get_overview` → `2026-06-01..2026-06-30`). На дату прогона окно daily-расписания не попадает в засеянные дни факта → секции finance/nutrition пусты. Классический date-flake A/B-сьюта (фикс-даты vs `today()`), уже отмеченный интегратором как латентный дефект качества тестов.
  - Волна C **не трогала** ни одного backend `.py`, кроме `web.py` (статика) и `handlers.py` (бот-кнопка): `git diff --stat HEAD -- 'app/**/*.py' 'tests/**'` = только эти два файла, оба scaffold-интегратора, ни строчки в analytics/finance/nutrition/digest. Фейл воспроизводится из бэкенда независимо от фронта.
  - Расхождение с отчётом интегратора («183 passed, 6 failed в test_nutrition::macros_total»): мой прогон на 06-11 поднял ДРУГОЙ date-flake (digest_preview) вместо nutrition::macros_total. Корень тот же (фикс-даты против `today()`); конкретный падающий тест плавает по дате прогона. Сигнал тот же — флаг С. по качеству A/B-тестов; к волне C не относится.
- **lint-imports:** 7 kept, 0 broken (141 файл, 298 зависимостей). OK.
- **Boot-smoke (`create_app()`):** 76 маршрутов; `GET /` → 200 text/html; `/static/{lists,finance,reminders,shell}.jsx` → 200; `GET /api/v1/me` без initData → 401 (auth-гвард только на `/api/`). OK.

---

## Границы записи (git status + diff)

**Файлы агентов/интегратора — чисто и по раскладке плана:**

| Владелец | Файл | OK |
|---|---|---|
| frontend Lists | `static/lists.jsx` (new) | ✅ |
| frontend Finance | `static/finance.jsx` (new) | ✅ |
| frontend Reminders+Analytics | `static/reminders.jsx` (new) | ✅ |
| интегратор | `static/{api.js,app.jsx,index.html,screen-kit.jsx,shell.jsx}`, `web.py`, `bot/handlers.py` | ✅ |
| reviewer/докинг волны | `questions/frontend-{lists,finance}.md`, `wave-c-plan.md` | ✅ |

Каждый frontend-агент — ровно свой один `.jsx` (window-глобал), общие файлы только у интегратора (build-contract §3). Внутри `static/` пересечений нет.

**❌ НАРУШЕНИЕ границы (вне раскладки волны C):** в рабочем дереве модифицированы три дока, не относящихся к фронту Mini App:
- `docs/06_program/feasibility.md` (+2 строки: Google Calendar / ICS-чтение)
- `docs/06_program/questionnaires/q4-assistant.md` (вопрос В8 про календарь, +В9)
- `docs/06_program/stage-4-assistant.md` (+F4.7 «Календарь-вид (+GCal-чтение)»)

Это идея этапа E4 (календарь, идея С. от 2026-06-10), не имеющая отношения к волне C. Ни один путь волны C (`static/**`, `web.py`, `handlers.py`, `questions/frontend-*`, `wave-c-plan.md`) их не покрывает. Финдинг F-1 (severity: средняя — содержательно безвредно, но это «грязь» в дереве волны: коммит волны C затащит несвязанные правки программы). Решение — за С.: либо вынести в отдельный коммит «E4 календарь-идея», либо откатить из коммита волны C.

---

## Покрытие сценариев ТЗ → код

### Lists (spec-lists §9 / §15)
| Сценарий ТЗ | Реализация в коде | Статус |
|---|---|---|
| S1 создать список (`POST /lists {type,name,scope}`) | `ListEditSheet`→`ListsAPI.createList` | ✅ |
| S2 иерархия/breadcrumb (`parent_id`) | `buildTree` + рекурсивный `ItemNode` (раскрытие/«подпункт») | ✅ (breadcrumb как inline-дерево, не хлебные крошки — допустимо для MVP-UX) |
| S3 отметить DONE (`PATCH /items/{id}`) | `toggleStatus`→`patchItem({status})` + рефетч counts с бэка | ✅ |
| S4 скрыть подарок (`hidden_from=[партнёр]`) | `ItemEditSheet` тумблер (только family + есть partner); badge «🎁 скрыт» при непустом `hidden_from`; партнёру бэк элемент не отдаёт | ✅ |
| S5 / A7 поиск (элементы + списки по названию, `kind`) | `SearchView` рендерит обе ветки `kind:"list"`/`kind:"item"` с раздельными бейджами | ✅ |
| S6 аналитика по типу (`done` за период) | `AnalyticsView`: `data.done/total/by_period` (DTO сошёлся) | ✅ |
| история элемента | `HistoryView`→`/items/{id}/history` | ✅ (доп. к ТЗ, в границах) |

A2 (видимость) — соблюдена в UI: см. раздел «Видимость» ниже.

### Finance (spec-finance §9 / §15)
| Сценарий | Реализация | Статус |
|---|---|---|
| S1 импорт xlsx (`file`+`year`) + отчёт | `ImportView`/`ImportReport`; прямой `fetch` чтобы показать `errors[]` (парковка Q-fe-fin-1) | ✅ |
| S1 fail-loud 422 (нераспознанное, ничего не записано) | `failure`-блок: message + `errors[{sheet,row,reason}]` + «ничего не записано» | ✅ |
| S2 повторный импорт (идемпотентно) | тот же поток; идемпотентность — на бэке | ✅ |
| S3 план/факт/разница/% + drill-down | `AnalyticsView`/`AnalyticsLine` (рекурсивное дерево), `plan_applicable` ⇒ «—» | ✅ |
| S5 каденс D/W/M/Q/Y + произвольный | `PeriodSelector` + `rangeForPeriod` (D/W диапазон считает клиент — парковка Q-fe-fin-3) | ✅ |
| S4 доходы по источникам/партнёрам + carryover + Дельта | `IncomeView`: `by_source`, partner→имя из `/me`, `carryover`, `delta` | ✅ |
| S6 ревью категорий (accept/alias) | `ReviewView`/`CategoryPicker`→`resolve` | ✅ |
| A7 выгрузка xlsx за период | `ExportButton`→`/finance/export` raw blob → download | ✅ |

### Reminders (spec-reminders §8 / §13)
| Сценарий | Реализация | Статус |
|---|---|---|
| S1 витамины recurring (`POST` daily personal) | `ReminderEditor` (recurrence/scope/interval) | ✅ |
| S2 разовая | `recurrence:none` | ✅ |
| S3 общая FAMILY | scope-селектор + бейдж «семья» | ✅ |
| S5 snooze | `SnoozeSheet` (15м/1ч/3ч/завтра)→`/snooze {minutes}` | ✅ |
| ack | `doAck`→`/ack` | ✅ |
| CRUD + статус-фильтры | `RemindersTab` чипы active/done/missed/cancelled, edit/delete | ✅ |
| A4 misfire (`MISSED`) | отображается как статус «Пропущено» (срабатывание — бэк/scheduler, не фронт) | ✅ (фронт-часть) |
| A2/A3 (срабатывание/рестарт) | вне фронта (планировщик); UI лишь показывает результат | n/a для экрана |

### Analytics (spec-analytics §8 / §13) — внутри reminders.jsx
| Сценарий | Реализация | Статус |
|---|---|---|
| S4 обзор (`/overview?from&to`) кросс-сфера | `OverviewTab`/`OverviewBody`: lists.by_type, finance(plan/fact/diff/top_deviations), nutrition(КБЖУ/goal); `plan_applicable` ⇒ «—» | ✅ |
| S3 превью дайджеста (`/digest/preview`) | `DigestTab`/`DigestPreview`/`DigestSection` (структурные секции lists/finance/nutrition сошлись с `digest.py`) | ✅ |
| S1 настроить расписания (daily+weekly, `PUT /{cadence}`) | `SchedulesEditor`/`ScheduleCard` (time/weekday/enabled) | ✅ |
| A3 состав без напоминаний | дайджест рендерит только lists/finance/nutrition — секции reminders нет | ✅ |
| A6 enabled-выкл | чекбокс enabled → PUT | ✅ |

---

## Видимость в UI (инвариант I10 / kernel — фронт не дорисовывает скрытое)

Проверено статически по коду — **держится**:
- **lists.jsx**: список элементов = `data.items` из `getList` (бэк уже отфильтровал по `scope`+`hidden_from`). Badge «🎁 скрыт от партнёра» рисуется ТОЛЬКО из непустого `item.hidden_from`, который бэк отдаёт лишь владельцу; у исключённого элемента в выдаче нет вовсе. Нет нигде «N скрытых»/плейсхолдеров под скрытое. `counts` (open/done) и «Очистить отмеченные (N)» берутся из `lst.counts` с бэка, не пересчитываются на клиенте. Тумблер подарка доступен только для `family`-списка и при наличии partner; `hidden_from=[partner.user_id]` — поэлементно.
- **reminders.jsx (обзор/дайджест)**: `OverviewBody`/`DigestSection` рендерят ровно те агрегаты, что отдал бэк (`read_composition`/`digest.py`, visibility-фильтрованные read-проекции). Клиент НЕ реконструирует скрытое/чужое; top_deviations/top_expenses/КБЖУ — как пришли. Финансовая «разница» (plan−fact) трактуется консистентно с бэком (комментарий в коде фиксирует конвенцию).
- **finance.jsx**: весь домен FAMILY, hidden_from нет; gate «нет семьи» (`/me` family===null) корректно блокирует запросы.

Живых внешних вызовов (Groq/Telegram) в коде экранов нет; сеть — только через `apiV1`/прямой `fetch` к своему backend.

---

## Обработка ошибок

- Единый `ApiError.message` (ru) из `{code,message}` бэка; `ErrorState` (screen-kit) даёт человекочитаемый текст + спец-подсказки для 401/403 + «Повторить». 422/404/409 проходят как `message` бэка.
- **Мутации не теряют ввод**: во всех листах (`ListEditSheet`/`ItemEditSheet`/`ReminderEditor`/`ScheduleCard`) ошибка submit ловится, `busy`/`saving` сбрасывается, поля формы остаются; родитель показывает тост, шит не закрывается до успеха. ✅
- Импорт finance: 422 fail-loud показывает список нераспознанных строк (а не просто `message`) — обход потери тела в `apiV1` сделан локальным прямым `fetch` (парковка Q-fe-fin-1, в границах файла). ✅

---

## Соответствие фактическому контракту routes.py (точечная сверка)

Сверил wire-шейпы и enum-регистр напрямую по коду — **сходится**:
- **Lists**: POST list/item → **201** (`post_list`/`post_item` `status=201`); DELETE → **204**; clear-done → `{deleted:int}`; SearchHitDTO (`kind`/`list`/`item?`); analytics `{type,from,to,total,done,by_period}` (`read/queries.py`). Enum на проводе — **lowercase** (`task/open/family`, `enums.py`/`Scope`), экран построен под факт. ✅
- **Finance**: income `by_source[].partner = s.user_id` (int|null), `kind = src.kind.value` (lowercase `carryover/partner/…`) — finance.jsx нормализует `kindNorm().toUpperCase()`; analytics `range={from,to}` + `plan_applicable` (объект, не строка — как в коде, не в openapi); `categories('active')` lowercase = `CategoryStatus.ACTIVE`. ✅
- **Reminders**: `_dto` отдаёт `status = .value` lowercase (`active/done/missed/cancelled` — `ReminderStatus`), recurrence/scope lowercase; POST → **201**, DELETE → **204**, snooze `{minutes:int}`. Экран сравнивает в lowercase — **корректно** (несмотря на устаревший uppercase-комментарий в шапке reminders.jsx — F-2 косметика). ✅
- **Analytics**: overview `{range,lists.by_type,finance.{plan,fact,diff,plan_applicable,top_deviations},nutrition.{kcal,protein,fat,carbs,days_with_data,goal}}` (read_composition) — сошлось; digest sections структурные `{sphere, …}` (`digest.py`) — `lists.by_type`, `finance.{fact,plan,top_expenses[{category,amount}]}`, `nutrition.{kcal,…,goal}` — сошлось; PUT schedule lowercase cadence. ✅
- **kernel `/me`,`/family`**: `{user,family}` (family=null вне семьи), members `[{user_id,first_name,username,is_self,…}]` — finance.jsx (`me.family.members`, `family===null`) и lists.jsx (`fam.members.find(!is_self)`, `partner.user_id/first_name`) и shell.jsx — все читают корректно. ✅

**Расхождение openapi↔код по enum-регистру (верхний в openapi, нижний на проводе)** — реальное и сквозное по всем доменам; экраны верно построены против кода. Запарковано как Q-fe-lists-1 с рекомендацией владельцу openapi (С.). Это не дефект фронта — это долг openapi.

---

## Findings

1. **F-1 (средняя):** вне-границевые изменения `docs/06_program/{feasibility.md, questionnaires/q4-assistant.md, stage-4-assistant.md}` в дереве волны C (идея E4-календаря). Нарушение раскладки волны (build-contract §3): не относятся к фронту Mini App. Содержательно безвредны, но загрязняют коммит волны. → условие приёмки.
2. **F-2 (косметика):** шапка `reminders.jsx` (комментарий, стр. 12) всё ещё описывает `status('ACTIVE|DONE|MISSED|CANCELLED')` в верхнем регистре, тогда как код и сравнения — lowercase. Только комментарий, поведение корректно.
3. **F-3 (косметика):** `questions/frontend-finance.md` (стр. 41) упоминает alias-picker по `?status=ACTIVE`, а код зовёт `categories('active')` (lowercase, корректно). Стейл-нота в доке, не в коде.
4. **F-4 (инфо, НЕ волны C):** date-flake A/B-тестов (`test_analytics_api::test_get_digest_preview` / ранее `test_nutrition::macros_total`) — фикс-даты фикстур против реального `today()`. Латентный долг качества тестов A/B; флаг С. Полный сьют «зелёный по сути» — единственный фейл не вызван волной C.

Парковок-«заглушек», прячущих недоделанный сценарий, не обнаружено.

---

## Парковки (оценка)

- **Q-fe-lists-1** (enum-регистр openapi↔код) — **легитимна**: реальная недоспецификация openapi (а не лень). Корректно адресована владельцу openapi (С.), фронт править openapi не вправе. Обход = строить против кода — верное решение для «рабочее приложение — цель».
- **Q-fe-fin-1** (apiV1 теряет тело 422 → импорт через прямой fetch) — **легитимна и обоснованна**: альтернатива (правка общего `api.js`) вне границы агента; локальный обход в одном файле корректен. Интегратор может позже централизовать — не блокер.
- **Q-fe-fin-2** (openapi↔код shape analytics/income) — информативная, не блокер; фронт построен под код.
- **Q-fe-fin-3** (D/W период не выводится бэком из «сегодня») — **легитимна**: клиент считает диапазон, шлёт валидные границы; forward-совместимо.

Все парковки — про контракт/недоспецификацию, не про срезанные сценарии.

---

## Условия приёмки (для ⚠️)

1. **F-1:** изъять `docs/06_program/{feasibility.md, questionnaires/q4-assistant.md, stage-4-assistant.md}` из коммита волны C (отдельный коммит «E4 календарь-идея» либо откат). Решение — за С.
2. (необязательно, косметика) F-2/F-3: подровнять стейл-комментарии (uppercase) в `reminders.jsx`/`frontend-finance.md` — на усмотрение, поведение корректно.
3. (вне волны C, флаг) F-4: завести задачу на починку date-flake A/B-тестов (фикс-даты vs `today()`), чтобы полный сьют был детерминированно зелёным.

При снятии F-1 — волна C готова к коммиту: фронт функционально полон, контракты сошлись, видимость и обработка ошибок держатся, границы внутри `static/`/scaffold чистые.
