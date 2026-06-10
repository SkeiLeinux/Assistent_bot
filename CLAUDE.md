# CLAUDE.md — Семейный ассистент (эволюция lifeManager_bot)

> **Назначение файла:** быстрый ввод нового диалога Claude в контекст проекта.
> Читать вместе с `MVP_Requirements.md` и `DOMAIN_MODEL.md` — это канонические артефакты-SSOT.
> При расхождениях источник истины — те документы; этот файл — индекс, состояние и рабочие договорённости.

---

## 1. Что это и зачем
- **Проект:** общий бытовой ассистент для пары (Telegram-бот + Mini App). Эволюция существующего и развёрнутого `lifeManager_bot`.
- **Двойная цель:** (основная) пройти полный цикл LLM-ассистированной разработки ради опыта; (желательная) рабочий инструмент для двоих.
- **Метод-эксперимент (пайплайн):** интервью → требования → доменная модель → **ADR** → ТЗ по доменам → субагенты → код. Claude ведёт проект как ведущий; решения по продукту/архитектуре — за стейкхолдерами.

## 2. Где мы сейчас (состояние пайплайна)
- **Сделано:** discovery (`docs/00_discovery/discovery.md`); `MVP_Requirements.md` (v1.4) + `DOMAIN_MODEL.md` (v1.3) — анализ принят полностью (архитектура — Sergei; продукт — Катя). **Фаза архитектуры закрыта: 5/5 ADR приняты** (0001 видимость · 0002 импорт финансов · 0003 планировщик · 0004 изоляция · 0005 тайминг Postgres); статусы — `docs/03_architecture/adr/README.md`.
- **✅ Фаза ТЗ закрыта (2026-06-07): все 7 ТЗ приняты** — `docs/04_specs/`; трекер `docs/04_specs/README.md`. **Сквозной аудит пройден:** блокер F2.1 (generic-планировщик `scheduler/`) и док-синки устранены; инфра-пробелы (`delivery/bot`, `main.py`, фронт) → build-roadmap. Отчёт: `docs/05_implementation/spec-audit.md`. Сводка решений по доменам ниже.
- **Identity & Sharing (kernel) — принято (v0.5):** 5 OQ закрыты (H3-a, совместное владение, `auth_date` 1 мес, КБЖУ-цели в Nutrition, опц. имена). По сверке с кодом решено **НЕ упрощать**: Invite-сущность и Membership-таблица остаются как в ТЗ (current-state проще — `Family.invite_code` / `User.family_id`); добавлен `timezone` на User.
- **Код выгружен и почищен:** `lifeManager_bot/` в рабочей папке (исходники, alembic, конфиг; `.env` с секретами оставлен; доки/кэши удалены). Дельта target↔current — в ТЗ Identity §1.
- **ТЗ Lists — принято (v0.2, С./Катя):** первый write-side, первое применение `Shareable`. OQ-L1…L5 закрыты: общий `OPEN/DONE`; поэлементное `hidden_from` на `ListItem` + `scope` на `List` (двухуровневая видимость); action-лог истории с капом; самоссылочный `ListItem`; type-поля — JSON `specific_attrs` с валидацией схемы на тип. kernel уточнён до v0.4 (гранулярность видимости — `scope`/`hidden_from` на разных уровнях).
- **ТЗ Finance (+ Import ACL) — принято (v0.2, С./Катя):** реальный xlsx разобран, ADR-0002 уточнён по заливке. OQ закрыты: IncomeSource управляемой сущностью + опц. линк на User; переходящий остаток импортируется как есть (MVP); минимальный экран ревью категорий; дневной факт — разрежённые строки `(category, date)`.
- **ТЗ Nutrition — принято (v0.2, С./Катя):** зрелый домен; фиксирует существующее + дельта под kernel (цели → `NutritionGoals` по `user_id`; `scope` PERSONAL дневник / FAMILY каталог; `SharedFood.family_id`; API под `/api/v1/nutrition/*`). North Star: 2-стадийный разбор фото (vision→поиск).
- **ТЗ Reminders — принято (v0.3, С./Катя):** generic-напоминания; `Reminder`=SSOT, планировщик poll-loop в одном event-loop (ADR-0003), misfire-грейс (дефолт 3 ч, конфиг), recurrence none/daily/weekly/interval, ack+snooze (обе кнопки); состояние только на `Reminder` (без журнала); доставка через Notifications.
- **ТЗ Notifications — принято (v0.2, С./Катя):** supporting-шов; stateless best-effort доставка (синхронно в MVP, durable-очередь — следующий шаг после MVP), политика лог→ретрай→алерт (ADR-0003), инлайн-кнопки + маршрутизация callback'ов; real-time change-уведомления сохранены (с учётом видимости). Источники Rem/Analytics/error/Lists.
- **Analytics & Digest — принято (v0.3):** read-side через `contexts.*.read`; `DigestSchedule` мульти-каденсный (ежедневный 23:59 + еженедельный вс 23:00, per-семья), дайджест Списки+Финансы+Питание (без напоминаний — Катя), триггер через общий scheduler + Notifications.
- **Аудит + build-roadmap готовы** (`docs/05_implementation/spec-audit.md`, `roadmap.md`): порядок стройки (срезы 0–4), target-скелет `app/`, import-linter контракт, разбивка на субагентов, тест-стратегия. Определения субагентов — конфиг (`.claude/agents/`), не docs (§4).
- **План Среза 0 принят (С., 2026-06-07):** инкрементальный перенос — весь код в `app/` (поднимается + линтер зелёный), домен решейпится в свой срез. Решения — `roadmap.md §0`: `app/` в корне репо, `app/db/` как leaf для `Base`/engine/session, `git init` на старте (локально истории нет), greenfield-baseline в S1, `lifeManager_bot/` удаляется после переноса. Карта переноса — `roadmap.md §4`.
- **Исполнение Среза 0 переносится в Claude Code** (не Cowork): это сквозной code-refactor с циклом edit→run→fix в реальном Windows-venv. Гайд перехода — `docs/05_implementation/code-handoff.md`.
- **✅ Срез 0 выполнен в Claude Code (2026-06-07):** скелет `app/` в корне поднят, код перенесён (инфра — в финальные пакеты, домен — в транзитные `services/repositories/handlers/db/models.py`), `lifeManager_bot/` удалён; import-linter-контракт (`pyproject.toml`) зелёный; `tests/` = `test_architecture.py` (границы) + `test_scheduler.py` (пустой тик). **2 инфра-фикса (не решейп):** (1) merge-миграция `c5e6eca700bb` — латентный multi-head исходных 4 миграций (две головы от `51b617b93453`) ронял `upgrade head`; слит пустым узлом, оригиналы целы, схема та же (S1 baseline сотрёт); (2) `tzdata` в requirements — `zoneinfo` на Windows без IANA-базы давал 500 в модуле еды. Коммит S0 — за С. (см. ниже).
- **✅ Срез 1 выполнен (kernel, 2026-06-09):** `app/kernel/` по ТЗ Identity v0.5 — `User` (reshape: `telegram_user_id/first_name/is_admin/timezone`, без `family_id`/КБЖУ) · `Family`+`Membership` · `Invite`(+`InviteStatus`) · миксин `Shareable` + per-entity `<table>_hidden_from` (H3-a); `sharing` (`visible_to`/`Viewer`/`VisibilityDTO`/`ShareableRepository`, чтения только через фильтр — I10); `auth` (`resolve_viewer`=`current_viewer`, max-age 30д); сервисы pairing/identity (гварды I1–I6, атомарный захват инвайта); REST `/api/v1/{me,family,family/join,family/invites}`; бот-пейринг (`/start /newfamily /join /family`). **Greenfield-baseline `237ca7b0e901`** (5 миграций → 1, схема с нуля). **DoD A1–A7 закрыты** (20 тестов + сквозной REST на реальном initData). Стратегия **«чистый kernel»** (С., 2026-06-09): транзит Lists/Food снят с проводки и **дормант** — содержит устаревшие ссылки на переехавшие `User`/`Family`, рантаймом/тестами/линтером не импортируется; переписывается под `contexts/` в S2. Menu-button и `food api.py` сняты до S2/S4. Коммиты S0+S1 — за С. Следующее — **Срез 2 (write-side: Lists/Finance/Nutrition)**. **S2a (Lists) реализован в Code** (`app/contexts/lists/`, тесты, миграция; не закоммичен, приёмка — с Q1/А6–А8).
- **2026-06-10 — пакет «аудит + автономная стройка + программа»:** сырые ответы стейкхолдеров заархивированы (`00_discovery/discovery-raw-answers.md`); **адверсариальный аудит цепочки** — `05_implementation/chain-audit.md` (26 находок; нерешённые P1 сведены в опросник Q1); **build-слой для параллельных агентов** — `05_implementation/build-contract.md` (волны, ownership, единая миграция волны, DoD) + 9 брифов `.claude/agents/` + `agents-guide.md`; **программа этапов E2–E5** — `docs/06_program/` (program-roadmap с гейтами и ADR-кандидатами 0006–0012, stage-доки, feasibility с веб-проверкой интеграций: Huawei privacy-zip ✅ / чеки 4 канала / Fit-HealthConnect ❌, опросники Q1–Q4, architecture-evolution, processes P1–P5); **REST E1** — `docs/api/openapi.yaml` (48 операций, производный от ТЗ). **Q1 заполнен (2026-06-10), решения влиты** (finance v0.3 · lists v0.3 · notifications v0.3 · analytics v0.4 + openapi + build-contract); гейт волны A — только **приёмка S2a** (сверка Q1-А6/А7, дельта A7-поиск).

## 3. Стейкхолдеры и как с ними работать
- **Sergei (С.):** системный аналитик/архитектор в банке, спец по микросервисам и REST. Python (PyCharm/Windows), читает Java/C#. Ведёт архитектуру.
- **Партнёр (Катя):** бизнес-аналитик, учится на продакта. Ведёт продукт/требования с бизнес-стороны.
- **Язык:** русский. **Стиль:** прямо, плотно, по шагам, с объяснением «что и зачем»; честная оценка сложности до начала; верификация (имена сервисов/пути/состояние) перед командами; прямой пушбек на слабые места приветствуется; без филлера и смягчений.
- В интервью разводить стейкхолдеров на **независимые проходы** — их ответы расходятся, и это ценный сигнал, а не шум.

## 4. Архив артефактов (структура проекта)
Артефакты хранятся по фазам пайплайна; дерево растёт по мере проекта до реализации.

```
docs/
  00_discovery/      discovery.md           — результаты интервью, карта расхождений, фиксация объёма
  01_requirements/   MVP_Requirements.md    — SSOT по объёму (MVP, North Star, NFR, границы)
  02_domain/         DOMAIN_MODEL.md        — bounded contexts, модель видимости, маппинг на код
  03_architecture/   adr/ADR-NNNN-*.md      — архитектурные решения (следующая фаза)
  04_specs/          spec-<домен>.md        — ТЗ по доменам (после ADR)
  05_implementation/                        — заметки реализации, roadmap, аудиты (spec-audit, chain-audit), build-contract, agents-guide, code-handoff
  06_program/                               — программа этапов E2–E5: program-roadmap, stage-доки, feasibility, архитектура/процессы, questionnaires/
  api/               openapi.yaml           — консолидированный REST-контракт E1 (производный от ТЗ; SSOT — ТЗ)
CLAUDE.md (root)                            — этот индекс
app/ (root)                                 — код (с фазы реализации; структура — roadmap.md §0/§2)
```

Определения субагентов — это **конфиг**, не docs (репозиторный `.claude/agents/` или аналог), хранить отдельно от артефактов.

**Читать первыми:** `discovery.md` → `MVP_Requirements.md` → `DOMAIN_MODEL.md`. Источник истины при расхождениях — эти документы, **не** история чата и **не** память.

## 5. Замороженные решения (не пересматривать без явного повода)
- **Вариант C:** дизайн полным пайплайном к чистой target-архитектуре; на коде переиспользуем существующее, новое пишем с нуля.
- **Стиль:** модульный монолит. Микросервисы/брокеры/multitenancy вне рассмотрения (2 пользователя, 1 сервер).
- **UX:** весь интерактивный UI — в Mini App. Бот = точка входа (запуск, `initData`) + push (напоминания, дайджест).
- **Данные:** текущие тестовые данные сносятся, схема строится с нуля (миграций со старого формата нет).
- **Приватность:** принято в **ADR-0001** — унифицированная модель `scope` (`personal|family`) + `hidden_from`; `hidden_from` персистится join-таблицей (под-решение H3). Подарки = `family` + `hidden_from=[одаряемый]`.
- **Финансы MVP:** только импорт Google-таблицы → аналитика (план/факт, иерархия, доходы). Ввод транзакций и автоматизация (ФНС) — North Star. Импорт = **заменяемый anti-corruption layer** (таблицу скоро переработают из-за переезда; домен не зависит от формы таблицы).
- **Аналитика:** on-the-fly, без предрасчёта (объём данных крошечный).
- **Подарки — в MVP** (третий режим приватности проектируется сразу, не ретрофитом).

## 6. MVP в одном экране (детали — в requirements §4)
Домены: **Identity & Sharing** (kernel), **Lists**, **Finance**, **Nutrition**, **Reminders**, **Analytics & Digest**, **Notifications**.
- **Lists:** типизированные (дела/покупки/идеи/фильмы/подарки/ссылки), иерархия, метаданные (даты/статус/история), поиск, аналитика по типу, приватность подарков. → Mini App.
- **Finance:** бюджет vs факт, иерархия категорий, доходы по источникам/партнёрам + переход остатка, каденс D/W/M/Q/Y + произвольный период. → новый.
- **Nutrition:** существующий дневник (текст/фото/база, КБЖУ, цели, история, SharedFood). → переиспользуется.
- **Reminders:** generic (текст+время+recurrence), витамины/задачи, push. → новый.
- **Analytics & Digest:** read-side, аналитика по сферам + ретро-дайджест (push). → новый.

## 7. Current-state кода (вариант C)
Модульный монолит, развёрнут на VPS, **данные тестовые**.
- **Есть:** модуль списков (chat, family-scoped, дерево `parent_id`, FSM, инвайт-коды); модуль еды (Mini App: React + REST 13 эндпоинтов, 3 способа ввода, дневник, цели, история, SharedFood, экспорт MD); инфра (aiogram polling + aiohttp в одном процессе/event-loop, SQLite + async SQLAlchemy + Alembic, `initData` HMAC auth, whitelist `ALLOWED_USER_IDS`, глобальный error-handler с алертом админу).
- **Приватность сейчас бинарна:** списки family, дневник еды personal.
- **Нет:** финансов, напоминаний, сквозной аналитики/дайджеста, метаданных/поиска/типизации списков, режима hidden-from-partner, UI вне еды в Mini App, планировщика напоминаний (LLM-слой задействован под еду, не под reminders).

## 8. Открытые вопросы для ADR
Единый live-трекер — `docs/03_architecture/adr/README.md` (SSOT по статусам ADR и открытым вопросам). Здесь не дублируется. Кратко: приняты ADR-0001, ADR-0004; в работе ADR-0002; открыты ADR-0003, ADR-0005.

## 9. Стек и инфра
- Python, aiogram 3, SQLAlchemy 2.0 (async), Alembic, pydantic-settings, **aiohttp** (web Mini App; FastAPI по факту не используется — возможный шаг при standalone, не сейчас). Фронт Mini App — React (UMD, без сборки).
- **Кросс-платформа / dev-deps:** `tzdata` в requirements обязателен (Windows `zoneinfo` без системной IANA-базы; на Linux-VPS есть). Dev: `import-linter` (контракт границ, `pyproject.toml`), `pytest`/`pytest-asyncio`.
- БД: SQLite (→ PostgreSQL планово). Аутентификация Mini App: Telegram `initData` (HMAC).
- LLM: Groq (free tier, `meta-llama/llama-4-scout-17b-16e-instruct`, мультимодальная) — разбор питания (текст+фото).
- VPS: Ubuntu, ~3 GB RAM / 1 CPU; systemd (`lifeManager_bot.service`), деплой одним процессом; `.venv` указан в unit.
- Репозиторий: GitHub `SkeiLeinux/lifeManager_bot`. Локальная рабочая копия **без `.git`** (история не выгружена) → `git init` на старте Среза 0. Target-layout: пакет `app/` в корне рабочей папки рядом с `docs/`; `lifeManager_bot/` удаляется после переноса (см. `roadmap.md §0`).

## 10. Рабочие договорённости с Claude
- Веду проект; на развилках даю варианты с обоснованием, **не диктую** — выбор за стейкхолдерами.
- **Артефакты на диске = single source of truth;** состояние не держать в истории чата.
- **Чек-поинт перед постройкой следующего слоя** (не строить архитектуру на неподтверждённых требованиях).
- Версионировать артефакты + changelog при изменениях.
- **Не оверинженерить** (NFR простоты). Standalone-приложение — мягкое ограничение (чистая граница API), не цель MVP.
