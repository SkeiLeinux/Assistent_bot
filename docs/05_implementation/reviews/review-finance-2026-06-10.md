# Review Finance (волна A, S2b) — 2026-06-10

**Вердикт: ✅ принять**

Независимая приёмка против `docs/04_specs/spec-finance.md` (v0.4), критерии A1–A7 построчно. Я read-only: код не правил, найденное зафиксировано.

## Механика (прогнал сам, .venv)
- **pytest (полный):** 75 passed. Finance-файлы: `test_finance_import.py` + `test_finance_analytics.py` + `test_finance_isolation.py` зелёные.
- **lint-imports:** 7 kept / 0 broken. Контракт «Finance Import ACL is imported only within Finance» KEPT; «Analytics reads Finance only via read-module» KEPT.
- **alembic heads:** ровно одна (`8c1ef1bb5136`).

## Границы записи
Чисто. Правки агента — только `app/contexts/finance/**` и `tests/test_finance*.py`. Общие файлы (`alembic`, `app/db`, `app/delivery/miniapp_api/web.py`, `pyproject.toml`, `requirements.txt`, `tests/conftest.py`, `alembic/env.py`) — изменены интегратором (его зона, build-contract §6), не finance-агентом. Нарушений нет.

## A-критерии (построчно, тест прочитан — не вырожден)
- **A1 ✅** `test_real_file_invariant_holds` + `test_real_file_roundtrip_to_domain` + `test_march_parent_fact_equals_sum_children_in_domain`. Импорт реального `docs/Бюджет общий 2026.xlsx` (4 листа Март–Июнь), 10 корней, 2-уровневое дерево (I1), 5 источников дохода, факт на листьях (у родителя `ActualAmount` нет — проверено явно). Инвариант I3 в новой формулировке (см. ниже) проходит на всех 4 листах. Не вырожден: домен-агрегат факта родителя сверяется с Σ детей.
- **A2 ✅** `test_reimport_is_idempotent`. Повторный импорт того же файла — счётчики `Category/ActualAmount/BudgetPlan/Income` неизменны, `created_for_review=[]`. I6 в коде: `delete_month` для budgets/actuals/incomes перед записью (`mapper._apply_month`).
- **A3 ✅** `test_unseen_categories_marked_needs_review` (все категории первого импорта → `NEEDS_REVIEW`) + `test_resolve_accept_activates` (accept → ACTIVE) + `test_resolve_alias_merges_and_records` (alias регистрирует `CategoryAlias`, дубль удалён, **не** fuzzy) + `test_alias_rejects_cross_level` (I1). Полный цикл состояний §8 покрыт.
- **A4 ✅** `test_broken_invariant_fails_loud_without_write` (родитель G=999999 → `ImportStructureError`, `Category/ActualAmount/Income`=0) + `test_broken_header_fails_loud` (мусор в шапке → fail-loud). Fail-loud до маппера подтверждён в `importing/__init__.py`: `validate_all(months)` до `DomainMapper.apply`. Частичной записи нет.
- **A5 ✅** `test_analytics_plan_fact_diff_pct_month` (итог факт 143586.24 / план 111000 — совпадает со сводными листа, не вырожденный assert), `test_analytics_drilldown_subtree` (drill-down по `category_id`), `test_quarter_aggregates_months` (Q=каденс), `test_arbitrary_range_by_days` (произвольный период по дням), `test_income_report_whole_month` (доход 144328.31, дельта 742.07, carryover 92.31 — против сводных строк реального листа). Каденс D/W/M/Q/Y + custom покрыт.
- **A6 ✅** `test_openpyxl_confined_to_importing_and_exporting` (AST-скан: openpyxl только в `importing/`+`exporting/`) + `test_importing_not_referenced_outside_finance_package` + import-linter-контракт. `analytics_service.export_xlsx` импортирует `exporting` **лениво внутри метода** — статическая изоляция api/чистая. Двойная страховка (AST + линтер).
- **A7 ✅** `test_export_xlsx_two_sheets_matches_analytics`. Загружает произведённый xlsx через openpyxl: листы `["Сводка","По дням"]`, шапка Сводки `[Категория,План,Факт,Разница,%]`, строка ИТОГО сверена с `analytics()` (план+факт в допуске ≤1 ₽). Лист «По дням» — матрица с датами.

## Инварианты с зубами
- **I3 (новая формулировка v0.4) — подтверждена в коде.** `importing/validate.py`: (a) `parent.fact == Σ child.fact` и (b) для каждого ребёнка `child.fact == Σ child.days`, оба с `TOLERANCE=Decimal("1.00")`. Дневная строка **родителя** не валидируется и не хранится (родитель — агрегат, §4.4). Тест A1 проверяет ровно эти две сверки; реальный лист Май (где родительская дневная строка непересчитана) проходит. Формулировка ТЗ и реализация совпали.
- **I6** — идемпотентность через snos-перед-записью по месяцу (mapper).
- **I9** — `test_partial_period_has_no_plan`: D/частичный → `plan_applicable=False`, `total_plan/total_diff=None`, все строки `plan=None`. В `read.finance_aggregate` план берётся только при `plan_applicable` по `whole_months`. Pro-rata нет.
- **I10** — `test_income_excluded_for_partial_period`: период без целого месяца → `by_source=[]`, `total_income=0`. В `read._income` доходы гейтятся `whole_months`.
- **Видимость/FAMILY** — весь домен FAMILY: `WHERE family_id=…` в read и репозиториях; `visible_to`/`scope`/`hidden_from` не применяются (соответствует §11). Нет чтений мимо family-фильтра.
- **Fail-loud без частичной записи** — `validate_all` до любой мутации; вызывающий делает rollback (REST-middleware / тест).

## Findings
1. **[инфо, не блокер]** `AnalyticsDTO` в коде (`api/routes.py::_analytics_dto`) несёт поле `plan_applicable`, которого нет в ТЗ §10 (`AnalyticsDTO = {period, range, total, lines}`). Поле полезно для UI (I9: показывать только факт), но это поверхность сверх ТЗ — фиксирую как расхождение контракта. Семантически безвредно; синк ТЗ §10 либо удаление поля — на усмотрение С.
2. **[инфо, не блокер]** `CategoryAlias` несёт `family_id` и unique-констрейнт `(alias_name, family_id)`, тогда как ТЗ §4.2 заявляет `alias_name` глобально Unique и `family_id` не упоминает. Реализация строже/корректнее (мульти-семейная изоляция), но ERD/§4.2 расходятся с кодом. Рекомендую синк ТЗ.
3. **[низкий]** `export_xlsx` делает три отдельных чтения (`analytics()` → `_read()` → `daily_rows()`), частично дублируя выборку фактов/категорий. Объём крошечный (NFR §14), функционально верно; кандидат на упрощение, не препятствие приёмке.
4. **[инфо]** `GET /finance/months` отдаёт `imported_at=None` (таймстемп импорта не хранится) и `categories` = общее число категорий семьи, а не за месяц (в коде помечено «North Star»). `MonthDTO` в ТЗ §10 заявляет `imported_at`/`categories` без оговорки — мелкое недотягивание DTO, не критерий приёмки.

## Парковки (оценка)
- **Q-finance-1 (I3)** — легитимна: реальная самопротиворечивость ТЗ (строгий дневной чек родителя отклонял бы реальный файл, противореча A1). Закрыта корректно — С. влил v0.4, код соответствует. Не блокер.
- **Q-finance-2 (`metric` игнорируется)** — легитимна и **не блокер**. `metric` в ТЗ §10 без специфицированной семантики; `AnalyticsDTO` и так несёт все метрики. В v0.4 параметр снят из контракта; код его просто не парсит (де-факто no-op) — поведение «показать всё» консистентно. Рекомендация: считать закрытой (параметр изъят из спеки).
- **Q-finance-3 (`carryover`)** — легитимна и **не блокер**. Реализовано: `carryover` = Σ `actual` источников `kind=CARRYOVER` по целым месяцам периода (I10), и те же источники дублируются в `by_source` — ровно как зафиксировано в ТЗ §10 DTO (v0.4). Семантика «остаток на начало» (вариант 2) сознательно не делалась — для MVP агрегат достаточен. Согласовано с I10. Закрыта.

## Условия приёмки
Нет (вердикт ✅). Findings 1–4 — косметические синки ТЗ↔код, на усмотрение С., приёмку не блокируют.
