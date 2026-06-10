# Волна C (S4, фронт Mini App) — план оркестрации

**Дата:** 2026-06-11 · **Роль:** оркестратор по `agents-guide.md` · **Сборка фронта:** React UMD без бандлера (roadmap §7 default; брифы 1:1 со стеком еды).

## Предусловия (сняты)
- REST волн A–B принят и закоммичен (`7594e94 S3 - волна B`, дерево чистое).
- Контракт фронта — `docs/api/openapi.yaml` (SSOT); фактические шейпы — `contexts/*/api/routes.py` + `analytics/api/routes.py` (сверены оркестратором, см. ниже).

## Что обнаружено при разведке (расширяет «3 экрана»)
1. **Mini App не отдаётся**: `create_app()` (web.py) монтирует только `/api/*`, статика не сервится. → интегратор добавляет `add_static('/static/')` + `/` → index.html.
2. **Нет шелла**: текущий `app.jsx` = приложение питания целиком. → интегратор строит верхнеуровневый шелл (хаб + роутинг), питание становится одним экраном.
3. **Питание бьёт в мёртвые пути** `/api/day` (старый food api удалён; бэк теперь `/api/v1/nutrition/*` с теми же короткими полями `name/w/p/f/c`). → интегратор перепроводит `api.js` (чистый ремап путей; шейпы сохранены бэком намеренно).
4. **Формат ошибок** бэка = `{code, message}` (openapi ErrorDTO); старый `api.js` читал `{error}`. → общий `apiV1()`-хелпер читает `.message`.
5. **Нет кнопки запуска Mini App** в активном боте (kernel pairing). → интегратор добавляет `/app` + кнопку WebApp (gated на `webapp_url`).

## Раскладка владения (без пересечений — параллель в одном дереве)
**Оркестратор/интегратор (общие файлы):** `api.js` (ремап nutrition + `apiV1`), `screen-kit.jsx` (новый — общие хуки/примитивы), `shell.jsx` (новый — root `App`, хаб, роутинг), `app.jsx` (App→`NutritionApp`+`onExit`), `index.html` (script-теги), `web.py` (статика), `delivery/bot/handlers.py` (кнопка запуска).

**Агенты (по одному файлу, ничего общего не трогают):**
| frontend-agent (экран) | Файл | Глобал |
|---|---|---|
| Lists | `static/lists.jsx` | `window.ListsScreen` |
| Finance | `static/finance.jsx` | `window.FinanceScreen` |
| Reminders+Analytics | `static/reminders.jsx` | `window.RemindersScreen` |

## Контракт экрана (для агентов)
- Компонент-глобал `window.<X>Screen`, проп `{ onExit }` (возврат в хаб).
- Свой `<TgHeader onBack=…>`; во внутреннем корне back = `onExit()`. BackButton — через `window.useTgBackButton`.
- API — через `window.apiV1(path, {method, body, query})` (бросает `ApiError` с `.message` ru). Свои методы определяет в своём файле (api.js не трогает).
- UI — `screen-kit` примитивы + классы `styles.css`; бесполезную самодеятельность сверх ТЗ-сценариев — парковать.
- Видимость: скрытое бэком фронт не дорисовывает (нет «N скрытых»); подарки просто отсутствуют у исключённого.
- Строить против ФАКТИЧЕСКИХ шейпов `routes.py` (рабочее приложение — цель); расхождения openapi↔код фиксировать в отчёте/парковке, но не блокировать ими реализацию.

## Известные расхождения openapi↔код (передать агентам)
- **Finance** `/analytics`: код добавляет `plan_applicable`; `range` — объект `{from,to}` (не строка); `income.by_source[].partner` = `user_id` (int), `kind` — из importing. `/categories/{id}/resolve` 201? нет — 200. POST list/item/reminder → **201** (не 200).
- **Analytics** `/overview`: `{range:{from,to}, lists:{by_type:[{type,added,done}]}, finance:{plan,fact,diff,plan_applicable,top_deviations:[{category,plan,fact,diff}]}, nutrition:{kcal,protein,fat,carbs,days_with_data,goal}}`. `/digest/preview`: `{period:{from,to}, sections:[…]}` (compose_digest — сверить форму секций в `analytics/digest.py`).
- **Reminders**: 1:1 c openapi; POST 201.
- **Nutrition** rewire: пути `/v1/nutrition/*`, поля `name/w/p/f/c/meal/time` (как в текущем фронте) — ремап путей достаточно.

## Шаги
0. Scaffold (оркестратор) — этот коммит-блок.
1. Спавн 3 frontend-agent параллельно (свои файлы).
2. Интеграция: script-теги, статика, кнопка бота, boot-smoke `create_app()` + статика, `pytest -q`, `lint-imports`.
3. `reviewer-agent` на волну C → вердикт в `docs/05_implementation/reviews/`.
4. Парковки + отчёт; коммит — за С. (git — вне моих действий).

## Итог интеграции (2026-06-11)
**Построено:** scaffold (api.js+apiV1, screen-kit.jsx, shell.jsx, app.jsx→NutritionApp, index.html, web.py статика, bot `/app`-кнопка) + 3 экрана агентами (`lists.jsx` `window.ListsScreen`, `finance.jsx` `window.FinanceScreen`, `reminders.jsx` `window.RemindersScreen`). Питание переиспользовано как экран хаба.

**Найдено и починено интегратором (правки в файлах агентов — кросс-срезовая консистентность):**
- **Регистр enum на проводе — нижний** во ВСЕХ контекстах (openapi/брифы давали верхний — ошибка). Lists-агент исправил сам; интегратор починил `finance.jsx` (`categories('active')`) и `reminders.jsx` (status-tabs/сравнения/дефолт + cadence `daily/weekly`). Парковка — `questions/frontend-lists.md` (Q-fe-lists-1, рекомендация владельцу openapi).
- **Глобальная коллизия `const {useState}=React`**: `shell.jsx` повторно объявлял хуки в глобальной области (как app.jsx) → SyntaxError → `App is not defined`. Починено: shell зовёт `React.useState/useEffect` напрямую (как goals/screens). Экраны агентов были корректно завёрнуты в IIFE — не задеты.

**Верификация:**
- Транспайл всех 11 .jsx пресетом `react` (тот же, что в браузере) — OK.
- `create_app()` — 76 маршрутов; статика отдаётся: `/`, `/static/*` → 200; `/api/v1/me` → 401 без initData (auth-гвард только на `/api/`).
- Headless-браузер (chrome-devtools) на живом сервере: хаб рендерится, все 4 плитки активны (глобалы зарегистрированы); экраны Списки/Финансы/Напоминания+Аналитика монтируются, табы/фильтры/Fab на месте, ErrorState 401 (ru) + «Повторить» работают; back→хаб работает.
- `pytest -q`: 183 passed, **6 failed** — все в `test_nutrition.py` (`macros_total`), причина — **tz-флак тестов** (машина UTC+3, локальная дата 06-11, а UTC/Amsterdam-хранение 06-10 → запись на «вчера»); НЕ регрессия волны C (бэкенд питания не трогался). Латентный дефект качества тестов A/B-волн (используют `date.today()` против UTC-хранения) — флаг для С.
- `lint-imports`: 7 kept, 0 broken.
