## Матрица трассировки

Показывает покрытие: требование → архитектурное решение (ADR) → ТЗ → диаграммы. Источник истины — `docs/01_requirements`, `docs/03_architecture`, `docs/04_specs`.

> **Версия / дата:** диаграммы синхронизированы 2026-06-20. Use Case `reminders` и `lists` обновлены до v0.4 (2026-06-18) — диаграммы приведены в соответствие 2026-06-20.

### Домены

| Домен | Требования | ADR | ТЗ | Use Case | BPMN-процесс |
|---|---|---|---|---|---|
| Identity & Sharing | §1.3, §3.4, §5, §6 | 0001, 0004 | spec-identity-sharing | usecase/identity | onboarding |
| Lists | §4 (Lists) | 0001 | spec-lists | usecase/lists | lists-item-lifecycle |
| Finance | §4 (Finance) | 0002 | spec-finance | usecase/finance | finance-import |
| Nutrition | §4 (Nutrition) | 0004, 0013 | spec-nutrition, spec-nutrition-retrieval | usecase/nutrition | nutrition-resolver |
| Reminders | §4 (Reminders) | 0003 | spec-reminders | usecase/reminders | reminder-lifecycle |
| Notifications | §5 (доставка) | 0003 | spec-notifications | (в системном UC + reminders) | notification-delivery |
| Analytics & Digest | §4 (Analytics) | 0003 | spec-analytics | usecase/analytics | digest |

### Сквозные представления

| Тема | ADR | Диаграмма |
|---|---|---|
| Контекст системы | — | overview/01-context |
| Карта bounded contexts | 0004 | overview/02-context-map |
| Модель видимости | 0001 | overview/03-visibility |
| Контейнеры / компоненты | 0004, 0005 | overview/04-containers |
| Карта фич MVP | — | overview/05-feature-map |
| Системный обзор Use Case | — | usecase/00-system |

### Архитектурные решения (ADR)

| ADR | Тема | Статус |
|---|---|---|
| 0001 | Модель видимости (scope + hidden_from) | Принято |
| 0002 | Импорт финансов (anti-corruption layer) | Принято |
| 0003 | Планировщик напоминаний (poll-loop) | Принято |
| 0004 | Изоляция контекстов (пакет-на-контекст + kernel) | Принято |
| 0005 | Тайминг перехода на Postgres | Принято |
| 0013 | Nutrition retrieval (resolver-ладдер) | Принято |

### Сводка покрытия

- **7 доменов** — у каждого ТЗ + Use Case + BPMN-процесс.
- **6 обзорных диаграмм** — контекст, карта контекстов, видимость, контейнеры, фич-карта, системный UC.
- **6 ADR** — все отражены в диаграммах (видимость → overview/03; импорт → finance-import; планировщик → reminder/digest/notification; изоляция → overview/02,04; retrieval → nutrition-resolver).
- **Охват:** MVP / текущее состояние. Этапы программы E2–E5 (`docs/06_program/`) — следующей итерацией.
