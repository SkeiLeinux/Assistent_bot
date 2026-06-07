# Build-roadmap — мост ТЗ → код

**Дата:** 2026-06-07
**Вход:** 7 принятых ТЗ (`docs/04_specs/`), аудит (`spec-audit.md`), ADR-0001…0005, requirements/domain.
**Метод:** вариант C — расширяем `lifeManager_bot` к target-архитектуре; backend-first; границы enforced (import-linter); приёмка по критериям ТЗ срез за срезом.
**Статус:** принят С. (2026-06-07). Решения по Срезу 0 зафиксированы в §0; исполнение S0+ — в Claude Code (см. `code-handoff.md`).

---

## 0. Решения по Срезу 0 (приняты С., 2026-06-07)

- **Стратегия переноса — инкрементальная.** S0 переносит весь код в `app/` так, чтобы приложение поднималось и `lint-imports` был зелёным, но **домен не решейпит**: модели/сервисы/хендлеры/еда переезжают в транзитные пакеты как есть и приводятся к specs в своём срезе (kernel — S1, write-side — S2, read/delivery — S3). Перенос файла+импортов (дёшево) отделён от решейпа под specs (дорого, один раз, в нужном срезе).
- **Дом для `Base`/engine/session — отдельный leaf `app/db/`** (не `kernel`): SQLAlchemy-проводка живёт вне домена; `app/db/` — нижний слой графа, импортируется всеми, сам не импортит внутреннее.
- **Корневой layout — пакет `app/` в корне репозитория** (рядом с `docs/`); `config.py` → `app/config.py`; `.env`/`alembic.ini`/`alembic/`/`requirements.txt`/`pyproject.toml` — в корне. Папка `lifeManager_bot/` **удаляется** после переноса.
- **Git — инициализируется на старте S0.** Локально репозиторий не инициализирован (история не выгружена); `git init` + первый коммит текущего состояния = точка отката **до** переноса. Уточняет §7: формулировка «git-история сохранится» неверна — истории нет.
- **Greenfield alembic-baseline — в S1, не S0.** В S0 4 текущие миграции остаются рабочими (`alembic upgrade head` крутится из `main.py`); сброс схемы — когда появляется новая схема (начало kernel).
- **Исполнение — в Claude Code**, не Cowork: S0+ это сквозной code-refactor с циклом edit→run→fix в реальном Windows-venv (boot/polling верифицируется только там). Гайд перехода — `docs/05_implementation/code-handoff.md`.

---

## 1. Принципы стройки
- **Вариант C:** переиспользуем существующий код (family/pairing/auth, модуль еды, дерево списков, инфра), переписываем под новые границы и переезд в Mini App. Данные greenfield (req §10) — новый alembic-baseline, старые тестовые данные сносятся.
- **Backend-first:** сначала домен+REST, затем фронт Mini App по контрактам.
- **Границы реальны:** `import-linter`-контракт из графа §2 — с первого среза, прогон в тесте/pre-commit (ADR-0004).
- **Шов под standalone:** Telegram только в `delivery` + `notifications` + `kernel.auth`; домены агностичны.
- **Каждый срез закрывается своими критериями приёмки** (A-критерии соответствующего ТЗ) + зелёным import-linter.

---

## 2. Target-скелет пакетов (ADR-0004 + scheduler из аудита F2.1)

```
app/
  config.py            # pydantic-settings (из current config.py)
  db/                  # leaf: Base, engine, async_session_maker, get_session — инфра, НЕ домен (решение §0)
  kernel/              # Identity & Sharing: User/Family/Membership/Invite; Shareable + visible_to(); auth(initData, whitelist); current_viewer; VisibilityDTO
  contexts/
    lists/             { models, repositories, services, api, read }
    finance/           { models, repositories, services, api, read, importing/ }   # importing = заменяемый ACL (openpyxl заперт здесь)
    nutrition/         { models, repositories, services, api, read, llm_service }   # llm провайдер-агностичен
    reminders/         { models, repositories, services, api }                      # services/reminder_poll = job (опрос созревших → доставка)
  analytics/           # read-side: read_composition, digest, schedule(DigestSchedule), api   (импорт: contexts.*.read)
  notifications/       # исходящий шов: service(deliver/alert), recipients
  scheduler/           # generic тик + реестр job (знает только kernel) — НОВОЕ (аудит F2.1)
  delivery/
    bot/               # вход: /start, запуск Mini App, generic-диспетчер callback'ов, error-alert
    miniapp_api/       # монтаж REST всех контекстов (/api/v1/*)
  main.py              # композиция: один event-loop (aiogram polling + aiohttp), DI Bot, регистрация job (reminder_poll + digest), старт/останов

(корень репо, вне app/: alembic.ini, alembic/, .env, requirements.txt, pyproject.toml, docs/, .gitignore)
```

**Реструктуризация current-state:** плоские `db/models.py`/`repositories/`/`services/`/`web/`/`handlers/`/`bot.py` → раскладываются по пакетам выше (переиспользование, не переписывание с нуля). В S0 (решение §0, инкрементально) инфра переезжает в финальные пакеты, а домен — в **транзитные** пакеты `app/{services,repositories,handlers,db/models.py}` как есть; решейп под specs — в своём срезе. `config.py` → `app/config.py`; `.env`/`alembic.ini`/`alembic/` — в корне. Папка `lifeManager_bot/` после переноса удаляется.

---

## 3. Контракт import-linter (рёбра графа §2)

| Слой | Может импортировать | Запрещено |
|---|---|---|
| `db` (leaf) | — (stdlib/SQLAlchemy) | всё внутреннее — его импортируют все, сам не импортит никого |
| `kernel` | `db` | contexts, analytics, notifications, delivery, scheduler |
| `contexts.<X>` | `kernel`, `db`, свой подпакет, `notifications`¹, `scheduler`¹ | другие `contexts.*`, `analytics`, `delivery` |
| `contexts.<X>.read` | `kernel`, `db` | всё прочее (чистая проекция) |
| `finance.importing` | `finance` (внутри) | импортируется только из `finance` |
| `analytics` | `kernel`, `db`, `contexts.{lists,finance,nutrition}.read`, `notifications`, `scheduler` | `contexts.*.services/models`, `reminders`, `delivery` |
| `notifications` | `kernel`, `db` (+ aiogram Bot via DI) | contexts, analytics, delivery |
| `scheduler` | `kernel` (+ asyncio) | contexts, analytics, notifications, delivery |
| `delivery` | `contexts.*.api`, `analytics`, `notifications`, `scheduler`, `kernel`, `db` | — |
| `main.py` | всё (композиция) | — |

¹ `reminders`/`analytics` → `notifications`/`scheduler` — легитимные supporting-рёбра. **Ребра `reminders→analytics` НЕТ** (digest-job регистрируется в `main.py`, аудит F2.1).
² **Транзитные пакеты S0** (`app/services`, `app/repositories`, `app/handlers`, `app/db/models.py` до дробления, `delivery/miniapp_api` с эндпоинтами еды) границы target нарушают по построению — они **вне контракта** (`importlinter` их не enforce'ит) до своего среза. Каждый слой добавляется в контракт, когда срез конформит код в `contexts/`/`kernel`.

---

## 4. Порядок реализации (срезы)

### Срез 0 — Скаффолд + инфра  *(закрывает F6.1, частично F6.2)*

Стратегия — инкрементальная (§0): переносим **весь** код в `app/`, чтобы поднималось и линтер был зелёным; **домен не решейпим** (это срезы S1–S3). Шаг 0 — `git init` + первый коммит (точка отката).

**Карта переноса (current → S0):**

| Сейчас (`lifeManager_bot/`) | → S0 (`app/` в корне) | Тип |
|---|---|---|
| `config.py` | `app/config.py` | перенос |
| `db/database.py` | `app/db/engine.py` (engine, session_maker, get_session) | перенос |
| `Base` из `db/models.py` | `app/db/base.py` | извлечение |
| `db/models.py` (модели целиком) | `app/db/models.py` — единый, не дроблён | транзит → дробится S1/S2 |
| `web/auth.py` | `app/kernel/auth.py` (initData HMAC) | перенос (шов kernel-auth) |
| `bot.py::main()` | `app/main.py` — композиция, один event-loop, DI Bot, `run_migrations`, старт web+scheduler | решейп |
| `bot.py` (роутеры, `@dp.error`, menu, `SessionMiddleware`) | `app/delivery/bot/` (зовёт `app/handlers`) | решейп-проводка |
| `web/` (api.py + static) | `app/delivery/miniapp_api/` — as-is, поднимает еду | транзит → распил S2 |
| `handlers/` (common, lists, keyboards, middleware) | `app/handlers/` | транзит → S1/S2 |
| `services/`, `repositories/` | `app/services/`, `app/repositories/` | транзит → разводятся по `contexts/`+`kernel` |
| — новое | `app/scheduler/` — generic тик + реестр job (только asyncio) | new |
| — новое | `app/notifications/` — generic `deliver`/`alert_admin`; старый `notification_service` остаётся в `app/services/` до S3 | new |
| — новое | `app/kernel/`, `app/contexts/{lists,finance,nutrition,reminders}/`, `app/analytics/` — пустые плейсхолдеры под контракт | new |
| `alembic.ini`, `alembic/`, `.env`, `requirements.txt`, `.gitignore` | корень репо (расширить `.gitignore`: `.env`, `*.db`, `__pycache__/`, `.venv/`) | остаются |
| `food_module.md`, `docs/UI макеты/` | → `docs/` (reference) или дроп | reference |

- **`import-linter`-контракт (§3) + pytest-тест** (`lint-imports`). Enforce'ятся слои с реальными границами (`db`, `kernel`, `scheduler`, `notifications`, `delivery`, пустые `contexts`/`analytics`); транзитные пакеты — вне контракта (§3 прим.²). Deps в requirements: `import-linter`, `pytest`, `pytest-asyncio`; контракт — в `pyproject.toml`.
- **alembic:** `env.py`/`target_metadata` → `app/db/base:Base`; 4 текущие миграции **остаются рабочими**; greenfield-baseline сброс — S1 (§0).
- **`scheduler/`** — generic тик (N сек) + реестр job; импортирует только `asyncio` (+ `kernel`-типы при необходимости, §3). **`notifications/`** — извлечь send-loop + admin-alert в generic `deliver`/`alert_admin` (полная retry/alert-политика ADR-0003 — S3). **`delivery/bot`** — вход `/start`, запуск Mini App, generic-диспетчер callback'ов, error→`notifications.alert_admin`. **`main.py`** — один event-loop, DI Bot, `run_migrations`, старт web+scheduler, точки регистрации job.
- **`lifeManager_bot/` удаляется** после переноса.
- **DoD:** `python -m app.main` поднимается (миграции → polling → мини-апа 8080 → menu-button); `lint-imports` зелёный; `initData`-auth еды работает; пустой тик планировщика логируется; `/start`,`/lists`,`/food` живые; `lifeManager_bot/` удалена. Ничего не решейплено.

### Срез 1 — kernel (Identity & Sharing)  *(фундамент, блокирует всё)*
- Реализовать по ТЗ Identity: `User`(+`timezone`)/`Family`/`Membership`/`Invite`; миксин `Shareable` + `visible_to` + `VisibilityDTO`; `current_viewer`(initData HMAC, whitelist, max-age 30д); FSM пейринга. Переиспользовать current `family`/pairing/auth, расширить.
- **Тест-харнесс видимости** (kernel A5/I10) — общий, переиспользуется доменами.
- **DoD:** критерии Identity A1–A7.

### Срез 2 — write-side домены  *(параллелизуемо после kernel)*
- **Lists** (первое применение `Shareable`): `List`/`ListItem`(самоссылочный)/`ListItemHistory`/`list_item_hidden_from`; `specific_attrs` JSON + валидация на тип; двухуровневая видимость; REST `/api/v1/lists`,`/items`; `read`-модуль. Переработка current дерева списков, переезд в Mini App.
- **Finance** (новый): `Category`-дерево/`BudgetPlan`/`ActualAmount`/`Income`/`IncomeSource`/`CategoryAlias`; **`importing/`** ACL — парсер реального xlsx (заливка→родитель, инвариант родитель=Σ, идемпотентный upsert, fail-loud); REST `/api/v1/finance`; `read`.
- **Nutrition** (переиспользование): перенести цели в `NutritionGoals`(`user_id`); `SharedFood.family_id`; применить `scope` (FoodEntry PERSONAL / SharedFood FAMILY); namespace API `/api/v1/nutrition/*`; `read`. Маппинг `owner_id`↔`user_id`/`created_by`.
- **DoD:** A-критерии каждого ТЗ + тест видимости (подарок скрыт от исключённого).

### Срез 3 — read-side / delivery-job  *(закрывает F2.1, F6.2)*
- **Reminders:** `Reminder`(SSOT) + `reminder_poll`-job (recurrence, misfire-грейс 3ч); ack/snooze.
- **Analytics:** read-композиция через `contexts.*.read`; `DigestSchedule`(мульти-каденс) + `compose_digest` + digest-job.
- **`main.py`:** зарегистрировать обе job (`reminder_poll`, `digest`) в `scheduler` — здесь связываются Reminders и Analytics (не импортом).
- **Notifications:** инлайн-кнопки + маршрутизация callback'ов; **change-уведомления Lists** эмитятся через `delivery` (F6.2).
- **DoD:** тесты misfire/рестарт/грейс/без-двойного-срабатывания (ADR-0003); дайджест по расписанию, видимость-фильтрован; политика доставки (ретрай/алерт/блокировка).

### Срез 4 — фронт Mini App  *(F6.3; можно параллелить по контекстам)*
- Переиспользовать фронт еды; новые экраны Lists/Finance/Reminders/Analytics по REST-контрактам. Без сборки (React UMD+Babel, как сейчас) или ввести сборку — решение фазы фронта.
- **DoD:** сквозные сценарии каждого домена в Mini App.

---

## 5. Субагенты (разбивка стройки)

Определения агентов — **конфиг** (`.claude/agents/`), не docs (CLAUDE §4). Здесь — границы и порядок.

| Агент | Срез | Спека-вход | Граница |
|---|---|---|---|
| infra-agent | 0 | этот roadmap §2-4, ADR-0004 | `app/`-скелет, scheduler, notifications, delivery, main.py |
| kernel-agent | 1 | ТЗ Identity | `app/kernel/` (блокирует остальных) |
| lists-agent | 2 | ТЗ Lists | `contexts/lists/` |
| finance-agent | 2 | ТЗ Finance | `contexts/finance/` (+importing) |
| nutrition-agent | 2 | ТЗ Nutrition | `contexts/nutrition/` |
| reminders-agent | 3 | ТЗ Reminders | `contexts/reminders/` + reminder_poll job |
| analytics-agent | 3 | ТЗ Analytics | `analytics/` + digest job |
| reviewer-agent | каждый | A-критерии ТЗ + import-linter + тест видимости | проверка перед merge среза |

**Handoff:** kernel сначала (публичная поверхность + `Shareable`); write-side параллельно после kernel; analytics — после того как `read`-модули write-side готовы; `main.py`-проводка job — на срезе 3. Reviewer гоняет приёмку каждого среза.

---

## 6. Тест-стратегия

- **Инвариант видимости** (kernel A5): каждая shareable-модель проходит `visible_to`; подарок/скрытый элемент невидим исключённому; чтения мимо фильтра не существует.
- **import-linter** — контракт §3 в тесте/pre-commit (ADR-0004); нарушение = красный.
- **Планировщик** (ADR-0003): misfire-догон в грейсе, skip сверх грейса, переживание рестарта (состояние в БД), без двойного срабатывания.
- **Import ACL** (Finance): round-trip реального `Бюджет 2026.xlsx` (Март–Июнь), инвариант родитель=Σ (факт+дни), идемпотентный повторный импорт, fail-loud на сломанной структуре.
- **Auth** (kernel): валидный/битый/просроченный `initData` → 200/401; не в whitelist → 403.
- **Приёмка по ТЗ:** A-критерии каждого домена = DoD-чеклист среза.
- **Интеграция:** пейринг → создание данных по доменам → дайджест → доставка, сквозняком.

---

## 7. Решения для стройки (мелкие, под нод С.)
- **Namespacing REST:** принять единое `/api/v1/<контекст>/` (аудит F3.1); kernel `/me`,`/family` и Lists `/items` — оставить как оправданные исключения.
- **Реструктуризация кода (решено, §0):** существующий код раскладываем в `app/`-дерево в корне репо в Срезе 0; `lifeManager_bot/` удаляется. Git локально не инициализирован → `git init` + первый коммит на старте S0 (точка отката); прежняя формулировка «git-история сохранится» снята.
- **Frontend-сборка:** оставить React-UMD-без-сборки (как сейчас) на MVP или ввести bundler — решить на Срезе 4.
- **Остаток аудита** (F4.2-чекбоксы ADR-0002/0005, F3.3 ErrorDTO-кросс-ссылка) — косметика, добить попутно.

---

## 8. Следующий шаг
Roadmap принят, решения S0 — §0, SSOT синхронизирован. **Исполнение Среза 0 переносится в Claude Code** (гайд `code-handoff.md`): `git init` → перенос по карте §4 → import-linter + generic `scheduler/`/`notifications/` + `main.py` → снос `lifeManager_bot/`. Затем Срез 1 (kernel).
