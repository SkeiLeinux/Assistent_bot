# Парковка — frontend Finance (волна C, экран finance.jsx)

Автор: frontend-agent (Finance). Дата: 2026-06-11.
Строилось против фактических шейпов `app/contexts/finance/api/routes.py` (SSOT кода) + `docs/api/openapi.yaml`.

---

## Q-fe-fin-1: apiV1 теряет тело при не-2xx, а 422-импорт обязан показать список нераспознанных строк
- Где: `api.js` `coreFetch` (на `!res.ok` бросает `ApiError(message,...)`, тело-объект отбрасывается); `routes.py:post_import` на `ImportStructureError` отдаёт **422** с телом `{status:'failed', errors:[{row,reason,sheet}], message}`; spec-finance §13/§9 S1 требует «fail-loud отчёт о нераспознанном».
- Проблема: общий клиент `apiV1` не отдаёт `errors[]` из тела ошибки — экран не смог бы показать конкретные строки/листы, только `message`.
- Варианты:
  1. (выбран, обход в коде) В `finance.jsx` импорт идёт **прямым `fetch('/api/v1/finance/import', …)`** с тем же заголовком `X-Telegram-Init-Data` (как в `api.js`), возвращая `{ok, status, data}` для обоих исходов (200 и 422). Это локально, в границах одного файла, не трогает `api.js`.
  2. Расширить общий `apiV1`: режим `{ tolerate422: true }` или `err.data = <тело>` на `ApiError` — но это правка общего файла (вне границы записи frontend-агента).
- Обход в коде: реализован вариант 1 — `FinanceAPI.importFile` (finance.jsx). На приёмке интегратор может предпочесть централизовать в `api.js` (тогда импорт переключить на `apiV1` с режимом, отдающим тело 422).

---

## Q-fe-fin-2: openapi vs код — расхождения в shape `/finance/analytics` и `/finance/income` (информативно, не блокер)
- Где: `openapi.yaml` schemas `FinanceAnalytics`/`IncomeReport` vs `routes.py`.
- Расхождения (строил против КОДА — он рабочий):
  - `analytics.range` в openapi = `{type:string}`, в коде = объект `{from,to}`. UI на `range` не опирается (период держит сам).
  - `analytics.plan_applicable` есть в коде, в openapi-схеме отсутствует. UI **использует код**: `plan_applicable=false ⇒ план/разница = «—»` (I9). Это центральный инвариант экрана.
  - `income.by_source[].partner` в openapi = `{type:string,nullable}`, в коде = `s.user_id` (**int|null**). UI трактует как `user_id` и резолвит имя из `/me` `family.members[].first_name`; при отсутствии совпадения показывает `ID <n>`.
  - `income.by_source[].kind` приходит из importing — регистр не гарантирован (`carryover` lower в сервисе vs `CARRYOVER` в DTO-enum). UI нормализует `kind` через `toUpperCase()`.
- Действие: парковка для синка openapi с кодом (не в границе frontend-агента — `openapi.yaml` править нельзя). На реализацию не влияет.

---

## Q-fe-fin-3: `period=D|W` бэк не выводит диапазон из «сегодня» — требует from/to
- Где: `analytics_service.resolve_period` — для `D`/`W` падает в ветку «нужны from и to»; брифы/openapi описывают D/W как «относительно сегодня».
- Проблема: чистый `?period=D` без from/to вернёт 422.
- Обход в коде (выбран): экран сам считает диапазон — `D` = сегодня (from==to==today), `W` = текущая неделя Пн–Вс — и шлёт `period=D|W` + `from`/`to`. Семантика «относительно сегодня» сохранена на клиенте; бэк получает валидные границы. Метка периода (`label`) у бэка остаётся `D`/`W`.
- Если бэк позже начнёт сам выводить D/W из «сегодня» — клиентский расчёт останется совместимым (те же границы).

---

## Локальные решения (не контрактные, зафиксированы для прозрачности)
- Денежный формат: целые рубли, разрядность пробелом, минус «−», суффикс «₽». `null ⇒ «—»` (не 0) — инвариант I9.
- Цвет разницы: `diff<0` (перерасход / дельта в минус) — `--danger`; `diff>0` — `--success`; `0`/`null` — нейтральный.
- Вкладки экрана: План/факт · Доходы · Ревью · Импорт. Период-бар и кнопка выгрузки xlsx — только на План/факт и Доходы. Бейдж со счётчиком на «Ревью» (из `/finance/categories/review`).
- Alias-выбор канона: bottom-sheet с деревом активных категорий (`GET /finance/categories?status=ACTIVE`), плоский рекурсивный список с отступом по глубине.
- Гейт «нет семьи»: если `/me` вернул `family===null` — подсказка «создай семью в боте /newfamily», запросы Finance не шлём (весь домен FAMILY).
