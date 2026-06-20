# Резолюция док-ревью 2026-06-20

**Что это:** лог исправлений по находкам `doc-review-2026-06-20.md`. Каждая находка приведена к источнику истины по SSOT-иерархии (requirements → domain → ADR → specs → openapi/диаграммы — производные следуют за специф).
**Исполнено:** 2026-06-20, в рамках док-ревью (канон — вручную; периферия `05_implementation`/`06_program`/`07_diagrams` — субагентами с каноническим брифом).
**Покрытие:** 1 блокер + 10 major + 15 minor + 6 nit → **исправлено 31/32**; 1 (n2) — вне предмета (правка в коде, не в доках); `catalog.html` — исходники исправлены, регенерация отложена (см. примечание о mount).

> **Важно про коммит (virtiofs-mount):** правки внесены в реальные файлы (E:\…, их видят инструменты Read/Edit и пользователь). Linux-mount песочницы — отдельная **устаревшая** копия: `git status`/`git diff` в ней не отражают правки и ругаются `index.lock`. Коммитить нужно из реального Windows-репозитория. Если git пожалуется на `.git/index.lock` — удалить этот файл.

---

## БЛОКЕР

| # | Находка | Решение | Файлы |
|---|---|---|---|
| **B1** | E3 Huawei: feasibility перевёл канал на vision (#16), но stage-3/processes/program-roadmap/диаграммы строили на отклонённом zip | Все зависимые доки и диаграммы приведены к канону #16: ведущий канал E3 = **скрины тренировки/сна → vision-LLM**; Huawei privacy-zip помечен отклонённым (#16); Health Kit API = North Star; пульс/зоны/темп вне scope; gate E3 = vision-разбор скринов | `06_program/`: stage-3-health, processes (P3), architecture-evolution (узел `HZIP→HVISION`), program-roadmap (gate); `07_diagrams/program/`: prog-feasibility, e3-huawei-import.bpmn, stage-e3-health, prog-roadmap |

## MAJOR

| # | Находка | Решение | Файлы |
|---|---|---|---|
| **M1** | Статус ADR-0013: трекер «Предложено» ↔ ADR/ТЗ «Принято» | Трекер → «✅ Принято (С., 2026-06-13; R1 реализован)»; добавлена запись истории; action items ADR-0013 закрыты `[x]` | `03_architecture/adr/README.md`, `ADR-0013-nutrition-retrieval.md` |
| **M2** | `feasibility-nutrition-web-search.md` цитируется, но отсутствует | Файл восстановлен (провенанс реконструирован из ADR-0013/feasibility; помечен как восстановленный) | `06_program/feasibility-nutrition-web-search.md` (новый) |
| **M3** | openapi Reminder без полей v0.4 | Добавлены `max_occurrences`/`until`/`fired_count`/`delivery_target` в схему `Reminder`, в `POST`/`PATCH /reminders`, новый enum `DeliveryTarget` | `api/openapi.yaml` |
| **M4** | `FoodEntry.source`: разный enum в spec-nutrition vs инкремент/openapi | spec-nutrition §4.1 приведён к провенансу (`ProvenanceSource`); версия 0.2→0.2.3 + changelog | `04_specs/spec-nutrition.md` |
| **M5** | `04_specs/README` («SSOT статусов ТЗ») завис на 2026-06-07/v0.2 | Таблица статусов/версий обновлена (identity v0.5, lists v0.4, finance v0.4, nutrition v0.2.2, reminders v0.4, notifications v0.3, analytics v0.4); инкремент R → реализован; «web за флагом» исправлено; +4 записи истории | `04_specs/README.md` |
| **M6** | roadmap: срезы S2/S3/S4 без маркера; §8 стейл | S2/S3/S4 → ✅ выполнено с датами (волны A/B/C); §8 переписан в «Состояние 2026-06-20» | `05_implementation/roadmap.md` |
| **M7** | build-contract/agents-guide заморожены «перед волной A» | Статус-баннеры (волны исполнены, MVP отгружен; плейбук — для будущих волн); предусловие волны A обновлено (версии v0.4) | `05_implementation/build-contract.md`, `agents-guide.md` |
| **M8** | program-roadmap: коллизии нумерации ADR | E2-гейт → 0006/0007/0011; stage-4 «ADR-0013»→**0014**; stage-5 «ADR-0014»→**0015**; реестр дополнен (0013 занят nutrition-retrieval; 0014/0015) | `06_program/program-roadmap.md`, `stage-4-assistant.md`, `stage-5-standalone.md` |
| **M9** | Диаграммы Reminders без v0.4 | usecase: UC7 (остановка повторений), UC8 (delivery_target); BPMN: шлюз остановки после advance + адресат в `deliver` + «ack не растит fired_count» | `07_diagrams/usecase/reminders.mermaid`, `bpmn/reminder-lifecycle.bpmn` |
| **M10** | Диаграмма E2-каналов рисует ФНС/lkdr равноправно | vision (D) помечен основным; API ФНС (B)/lkdr (C) сняты («не инвестируем без явного повода») | `06_program/architecture-evolution.md` |

## MINOR

| # | Находка | Решение | Файлы |
|---|---|---|---|
| **m1** | DOMAIN_MODEL: версия/опора рассинхрон | Версия →1.3.3; опора → requirements **v1.4**; backfill changelog 1.3.2/1.3.3; CLAUDE §2 → v1.3.3 | `02_domain/DOMAIN_MODEL.md`, `CLAUDE.md` |
| **m2** | spec-finance заголовок 0.3 < changelog 0.4 | Заголовок → v0.4; статус уточнён (v0.3–v0.4 — С., 2026-06-10) | `04_specs/spec-finance.md` |
| **m3** | spec-nutrition-retrieval заголовок v0.3 < тело v0.5/DoD | Заголовок → v0.5; статус → «R1 реализован» | `04_specs/spec-nutrition-retrieval.md` |
| **m4** | CLAUDE «48 операций» — фактически 50 | →50 | `CLAUDE.md` |
| **m5** | openapi info.description дата неверна | Описание → актуальное покрытие (по доменам, на 2026-06-20); version →1.0.1-mvp | `api/openapi.yaml` |
| **m6** | questions/finance — Q-fin-1/2/3 не закрыты | Помечены «✅ РЕШЕНО» со ссылками на spec-finance v0.4 | `05_implementation/questions/finance.md` |
| **m7** | questions/reminders — Q-rem-2 без исхода | Помечен follow-up (грейс = дефолт фабрики 3 ч; config-поле отложено) | `05_implementation/questions/reminders.md` |
| **m8** | review-waveB покрывает A1–A7 (v0.3); A8/A9 нет | Добавлен аддендум 2026-06-20 (A8/A9 в v0.4, приняты С.) | `05_implementation/reviews/review-waveB-2026-06-10.md` |
| **m9** | ADR-0005 «FTS5 сейчас» vs Lists «LIKE/FTS отложен» | Формулировки приведены к F4.4 (LIKE в MVP, FTS → North Star); action items 2/3 закрыты | `03_architecture/adr/ADR-0005-postgres-timing.md` |
| **m10** | scheduler/ нет в ADR-0004 эскизе и на context-map | scheduler/ добавлен в эскиз + контракт import-linter + уточнение; узел `Scheduler` на context-map | `03_architecture/adr/ADR-0004-context-isolation.md`, `07_diagrams/overview/02-context-map.mermaid` |
| **m11** | program-roadmap E1 «идёт волны A–C» | → «E1 отгружен на VPS, тестируется; инкремент R реализован» | `06_program/program-roadmap.md` |
| **m12** | deploy-e1 не отражает условность приёмки волны C | Добавлено прим. про условную приёмку (изъять правки 06_program) | `05_implementation/deploy-e1.md` |
| **m13** | deploy-runbook зашивает устаревший HEAD `d1ba9dd` | Обобщено (коммит деплоя MVP; поверх — пост-MVP, HEAD новее) | `05_implementation/deploy-mvp-vps-runbook.md` |
| **m14** | feature-map: формула резолвера неполна | → `label→(barcode)→OFF→web→estimate` | `07_diagrams/overview/05-feature-map.mermaid` |
| **m15** | mvp-feedback-triage §0 на снятом сигнале | §0 дополнен поправкой (вывод про «грязное дерево» снят, репо чисто) | `05_implementation/mvp-feedback-triage.md` |

## NIT

| # | Находка | Решение | Файлы |
|---|---|---|---|
| **n1** | glossary Invite-статусы vs enum | Приведены к `InviteStatus` (active/used/expired/revoked) | `07_diagrams/reference/glossary.md` |
| **n2** | questions/frontend-finance `?status=ACTIVE` vs код `active` | **Не правил — вне предмета** (расхождение ноты с кодом; код отдельным проходом). Зафиксировано в `reviews/wave-c.md` F-3 | — |
| **n3** | code-handoff ссылается на перенесённые/удалённые артефакты | Помечено выполненным (файлы в `docs/reference/`; `lifeManager_bot/` снесена в S0) | `05_implementation/code-handoff.md` |
| **n4** | q3-health: старый zip-блок без пометки «устарело» | Помечен `_(устарело #16: zip провален → канал = скрины)_` | `06_program/questionnaires/q3-health.md` |
| **n5** | openapi clear-done тег `CH-10` vs spec `Q1-А6` | → `Q1-А6` | `api/openapi.yaml` |
| **n6** | traceability охват не датирован | Добавлен версия-штамп + отметка о синхронизации UC до v0.4 | `07_diagrams/reference/traceability.md` |
| **n7** | провенанс-заголовки диаграмм Reminders стейл | usecase/reminders header → «§9 (S1–S7), v0.4 / 2026-06-20»; traceability-штамп | `07_diagrams/usecase/reminders.mermaid` |

---

## Примечания

- **`catalog.html`** (генерируется из `_build/`): исходные диаграммы исправлены, но регенерация в сессии невозможна — генератор в bash читает исходники через лагающий mount (обрезка на кириллице). В шапку добавлен баннер «перегенерировать из `_build/`». **Действие пользователя:** в реальном Windows-окружении прогнать `python _build/build_catalog.py` — инлайны подтянутся из уже-исправленных исходников.
- **Версионирование:** где у документа есть «История изменений»/changelog — добавлена запись 2026-06-20. Где секции нет (roadmap, questions/*, deploy-*, review-waveB) — пометка о реконсиляции внесена в тело правки.
- **Код не трогался** (`app/**`) — по условию; правки только в документации.


## Инцидент записи и восстановление (2026-06-20)

В ходе правок выявлен баг mount'а рабочей папки (virtiofs): пакетные правки через harness-инструменты **Edit/Write обрезали файлы на диске** (теряли 1–39 строк), при этом кэш Read/Grep показывал их целыми. Обнаружено прямой проверкой через bash (контрольные нетронутые файлы читались полностью → обрезка реальна, а не артефакт чтения).

**Восстановление:** все затронутые файлы (~35) переписаны через bash — Python-скрипты с `assert count==1` от бэкапа рабочего дерева в `/tmp` (канон) и повторная проводка субагентами строго через bash (05/06/07). Финальная проверка на диске: ни один файл не короче бэкапа; `openapi.yaml` парсится; оба изменённых BPMN валидны (`ET.parse`); mermaid сбалансирован.

**Важно пользователю:** коммитить из реального Windows-репозитория — git в песочнице сломан (`.git/index.lock`, `Operation not permitted`). `docs/07_diagrams/catalog.html` не регенерирован (генератор в песочнице читает исходники через тот же обрезающий mount) — перегенерировать локально: `python _build/build_catalog.py`.
