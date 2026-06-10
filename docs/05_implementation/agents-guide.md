# Гайд по агентам — кто, с чем на входе, что на выходе

**Дата:** 2026-06-10. Брифы — `.claude/agents/*.md` (Claude Code подхватывает их как субагентов автоматически). Протокол волн/границ/DoD — `build-contract.md` (SSOT; здесь — операционная шпаргалка «как этим пользоваться»).

---

## 1. Реестр агентов

| Агент | Когда | Вход | Выход | Кто проверяет |
|---|---|---|---|---|
| `finance-agent` | волна A | spec-finance + kernel-ТЗ + ADR-0002 + xlsx-фикстура `docs/Бюджет общий 2026.xlsx` | `contexts/finance/**` + тесты (A1–A6) | reviewer → С. |
| `nutrition-agent` | волна A | spec-nutrition (§3 дельта!) + дормант-код еды + food_module.md | `contexts/nutrition/**` + тесты; сырьё удалено | reviewer → С. |
| `reminders-agent` | волна B | spec-reminders + ADR-0003 + API `scheduler/`/`notifications/` из кода | `contexts/reminders/**` + job-callable + тесты (misfire/restart) | reviewer → С. |
| `analytics-agent` | волна B | spec-analytics + код `contexts/*/read` (его вход = код после волны A!) | `analytics/**` + digest-job + тесты | reviewer → С. |
| `delivery-agent` | волна B | spec-notifications + spec-reminders §7/§9 | `notifications/**` + `delivery/bot/**` (политика, кнопки, callback'и, change-уведомления) | reviewer → С. |
| `integrator-agent` | после каждой волны | отчёты агентов + build-contract §6 | общие файлы: единая миграция, монтаж, линтер, deps; smoke; сводный отчёт | reviewer → С. |
| `reviewer-agent` | после интеграции (или точечно) | ТЗ (A-критерии) + отчёты + код | вердикт в `docs/05_implementation/reviews/` — ничего не чинит | С. |
| `frontend-agent` | волна C, per-экран | **openapi.yaml** + ТЗ §сценарии + фронт еды как образец | экран в `miniapp_api/static/` | reviewer → С. |
| `spec-writer-agent` | вход этапов E2+ | stage-doc + **заполненный** опросник + ADR этапа + feasibility + эталон spec-lists | черновик `spec-<x>.md` v0.1 «на ревью» | С. + Катя (принятие) |

## 2. Запуск волны (порядок рук)

1. **Предусловия волны** — `build-contract.md §1` (жёсткие сняты? решения влиты в ТЗ?).
2. Запустить агентов волны **параллельно** (Claude Code: каждому — свой субагент; в промпте запуска: «работай по своему брифу, волна X»). Для frontend-agent указывать экран: «экран Finance».
3. Вернулся через полдня → у каждого: отчёт по форме build-contract §7 + парковка `questions/<ctx>.md`.
4. `integrator-agent` (или С. вручную по его чеклисту) — общие файлы, единая миграция, полный прогон, smoke.
5. `reviewer-agent` — вердикт. 6. С.: разбор парковок, коммит волны, выход-гейт.

**Анти-паттерны:** запускать analytics-agent до приёмки волны A (его вход не существует) · давать двум агентам пересекающиеся пути · разрешать агенту «быстренько поправить» чужой пакет/миграцию · принимать волну без reviewer-прогона A-критериев.

## 3. Как добавить агента под новый контекст (E2+: receipts, health, assistant)

Скопировать ближайший по типу бриф (новый домен → `finance-agent`; решейп → `nutrition-agent`; read-side → `analytics-agent`) и заменить 5 секций: **Вход** (своё ТЗ + свои ADR) · **Граница записи** (`contexts/<новый>/**` + свои тесты) · **Что строишь** (из ТЗ) · **Не фантазировать** (OQ/решения своего ТЗ) · **DoD** (свои A-критерии). Затем: добавить строку в реестр §1, путь — в таблицу ownership `build-contract.md §3`, линтер-план — интегратору.

## 4. Конвейер этапа целиком

`processes.md` P5: гейт → опросник → gate-тест → ADR → **spec-writer-agent** → ревью/принятие ТЗ → (при необходимости новый агент по §3) → волны по build-contract → reviewer → выход-гейт. Доки этапов — `06_program/`.

## История изменений
- **2026-06-10:** первичная версия (9 агентов, запуск волны, шаблон клонирования, конвейер).
