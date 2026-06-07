# Переход в Claude Code — старт Среза 0

**Дата:** 2026-06-07
**Зачем файл:** ввести сессию Claude Code в проект и Срез 0 без перерешивания принятых развилок.
**SSOT:** план — `roadmap.md` (§0 решения, §2 скелет, §3 контракт линтера, §4 карта переноса). Этот файл — операционный, не дублирует обоснования.

---

## 1. Где исполнять и почему

- **Срез 0 и дальнейшие срезы кода — в Claude Code.** Это сквозной code-refactor с циклом edit→run→fix в реальном Windows-venv; boot бота (polling) и установка menu-button верифицируются только в твоём окружении (Cowork-bash — изолированный Linux-сэндбокс, не твоя машина).
- **В Cowork остаётся:** ревью артефактов, doc-синки SSOT, чек-поинты со стейкхолдерами, разбор файлов (xlsx бюджета, мокапы). Возвращаться сюда на приёмку срезов.
- **Стоимость перехода низкая:** SSOT — артефакты на диске (CLAUDE §10), не история чата. `CLAUDE.md` — файл ввода в контекст.

---

## 2. Порядок чтения в Code (вход)

1. `CLAUDE.md` — индекс и состояние пайплайна.
2. `docs/05_implementation/roadmap.md` — **§0 (решения S0) → §4 (карта Среза 0) → §2/§3 (скелет + контракт линтера)**.
3. `docs/05_implementation/spec-audit.md` — что и почему вынесено в инфра-слой (F2.1, F6.1–F6.3).
4. По мере надобности — ТЗ `docs/04_specs/spec-*.md` (для S0 достаточно identity §1 «дельта target↔current» и notifications §7); ADR `03_architecture/adr/` (0003 планировщик, 0004 изоляция).

Источник истины при расхождениях — артефакты, **не** история чата и **не** память.

---

## 3. Зафиксированные решения S0 (НЕ перерешивать)

Полностью — `roadmap.md §0`. Кратко:

- **Инкрементально:** весь код переезжает в `app/` (поднимается + `lint-imports` зелёный); **домен не решейпится** — модели/сервисы/хендлеры/еда едут в транзитные пакеты как есть, приводятся к specs в своём срезе (kernel S1, write-side S2, read/delivery S3).
- **`app/db/` — leaf** для `Base`/engine/session (не в `kernel`).
- **`app/` в корне репо**; `config.py`→`app/config.py`; `.env`/`alembic.ini`/`alembic/`/`requirements.txt`/`pyproject.toml` — в корне; **`lifeManager_bot/` удаляется** после переноса.
- **`git init` на старте** (локально истории нет) — первый коммит = точка отката **до** переноса.
- **Greenfield alembic-baseline — в S1**, не S0; в S0 4 текущие миграции остаются рабочими.

---

## 4. Setup среды (один раз, до переноса)

Рабочая папка: `E:\пэт-проекты\Assistent_bot`. PyCharm Run/Debug WorkingDirectory = **корень репо**.

1. **Git (точка отката):**
   ```
   cd E:\пэт-проекты\Assistent_bot
   git init
   ```
   Создать корневой `.gitignore` (слить с `lifeManager_bot/.gitignore`), обязательно игнорировать: `.env`, `*.db`, `*.sqlite*`, `__pycache__/`, `.venv/`, `.idea/`.
   ```
   git add -A && git commit -m "S0: import current state (pre-restructure)"
   ```
   Секреты: `.env` уже не должен попасть в коммит — проверить `git status` до commit.

2. **venv + зависимости:**
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   pip install import-linter pytest pytest-asyncio
   ```
   Добавить в `requirements.txt`: `import-linter`, `pytest`, `pytest-asyncio` (зафиксировать версии после установки). `openpyxl` (Finance) — в S2, не сейчас.

3. **Контракт import-linter** — в `pyproject.toml` (стартовый вид; уточнить прогоном `lint-imports`):
   ```toml
   [tool.importlinter]
   root_package = "app"

   [[tool.importlinter.contracts]]
   name = "Layered architecture (S0)"
   type = "layers"
   layers = [
       "app.main",
       "app.delivery",
       "app.analytics",
       "app.contexts",
       "app.notifications",
       "app.scheduler",
       "app.kernel",
       "app.db",
   ]
   ```
   Транзитные пакеты (`app.services`, `app.repositories`, `app.handlers`, эндпоинты еды в `app.delivery.miniapp_api`) **в контракт не вносить** до своего среза (§3 прим.²). `notifications`/`scheduler` — peers; если layered total-order начнёт ругаться на их взаимный порядок, развести `independence`-контрактом.

---

## 5. Срез 0 — порядок выполнения

Карта «что куда» — `roadmap.md §4`. Рекомендуемая последовательность (после git-коммита):

1. **Скелет `app/`:** пустые пакеты с `__init__.py` — `db`, `kernel`, `contexts/{lists,finance,nutrition,reminders}`, `analytics`, `notifications`, `scheduler`, `delivery/{bot,miniapp_api}`.
2. **Leaf `app/db/`:** `base.py` (`Base`), `engine.py` (engine + `async_session_maker` + `get_session` из `db/database.py`). `db/models.py` → `app/db/models.py`, импорт `Base` из `app/db/base`.
3. **`app/config.py`** (из `config.py`); импорты по проекту `from app.config import settings`.
4. **`app/kernel/auth.py`** (из `web/auth.py`).
5. **Транзит домена:** `services/`→`app/services/`, `repositories/`→`app/repositories/`, `handlers/`→`app/handlers/`; поправить внутренние импорты (`from app.services import …`, `from app.db.models import …`). `__init__.py`-реэкспорты сохранить.
6. **`app/notifications/`:** generic `deliver(recipients, text)` + `alert_admin(...)` (вынуть send-loop из `services/notification_service.py` и admin-alert из `bot.py::error_handler`). Старый `NotificationService` (list-специфика) **остаётся** в `app/services/` до S3.
7. **`app/scheduler/`:** generic тик(interval) + реестр job; импортирует только `asyncio` (+ `kernel`-типы при необходимости, §3), пустой реестр.
8. **`app/delivery/miniapp_api/`:** `web/` целиком (api.py + static); initData брать из `app/kernel/auth`.
9. **`app/delivery/bot/`:** include роутеров `app/handlers`, `@dp.error` → `notifications.alert_admin`, menu-button, `SessionMiddleware`.
10. **`app/main.py`:** из `bot.py::main()` — `run_migrations`, DI Bot, старт web + scheduler, polling, cleanup.
11. **alembic:** `env.py` → `from app.db.base import Base` + импорт `app.db.models` для метадаты; `alembic.ini` в корне (`script_location = alembic`).
12. **Снести `lifeManager_bot/`** (после того как всё перенесено и поднимается).

---

## 6. Проверка / DoD

```
lint-imports                          # контракт §3 зелёный
alembic upgrade head                  # 4 текущие миграции применяются
python -m app.main                    # поднимается: миграции → polling → мини-апа :8080 → menu-button
pytest                                # тест lint-imports (+ заглушка тика планировщика)
```

DoD Среза 0: приложение поднимается; `lint-imports` зелёный; `initData`-auth еды работает; пустой тик планировщика логируется; `/start`, `/lists`, `/food` живые; `lifeManager_bot/` удалена. **Ничего не решейплено** (модели, эндпоинты еды, list-уведомления — в своих срезах).

---

## 7. Грабли (Windows / current-state)

- **`engine echo=True`** в `db/database.py` — на проде шумно; в S0 можно оставить, вынести в `settings` позже.
- **`run_migrations()`** ищет alembic в `.venv\Scripts\alembic.exe` рядом с `sys.executable` — работает при активном venv; PyCharm-конфиг должен использовать `.venv`.
- **`.env` в корне** — `pydantic-settings` грузит относительно CWD; запуск только из корня репо (`python -m app.main`).
- **`web/auth.py max_age=24h`** — kernel-ТЗ хочет 30д; это решейп S1, в S0 не трогать.
- **Имена моделей** `ListCategory`/`ListItem`, КБЖУ-цели колонками на `User` — current-state; дробление и `NutritionGoals` — S1/S2, не S0.
- **`docs/UI макеты/`, `food_module.md`** в `lifeManager_bot/` — reference; перенести в `docs/` или дропнуть до сноса папки.

---

## 8. Субагенты

Для Среза 0 **не обязательны** — это один связный механический рефактор; делается линейно. Пригодятся с S2 (параллельные write-side контексты). Определения — `.claude/agents/` (конфиг, не docs), заводятся в Code; разбивка и границы — `roadmap.md §5`.

---

## 9. После Среза 0

Чек-поинт приёмки (С. — архитектура) → **Срез 1 (kernel)**: `roadmap.md §4` + ТЗ `spec-identity-sharing.md`. Тут начинается решейп (дробление моделей, `Shareable`/`visible_to`, `current_viewer`, greenfield-baseline).
