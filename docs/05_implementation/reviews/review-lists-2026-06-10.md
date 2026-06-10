# Review Lists (S2a, формальная приёмка + дельта A7) — 2026-06-10

**Вердикт: ✅ принять**

Формальный артефакт приёмки S2a против `docs/04_specs/spec-lists.md` (v0.3), критерии A1–A7 построчно. Особое внимание — **A7 (поиск, Q1-А8)**, добавленный после первичной приёмки S2a с пометкой «*Дельта к S2a-коду — проверить/доделать на приёмке*». Я read-only.

## Механика (прогнал сам, .venv)
- **pytest (полный):** 75 passed. `test_lists.py` — 16 тестов зелёные.
- **lint-imports:** 7 kept / 0 broken. «Analytics reads Lists only via read-module» KEPT; «Contexts are mutually independent» KEPT.

## Границы записи
Чисто. Изменения Lists — в `app/contexts/lists/**` и `tests/test_lists.py` (S2a, ранее принят). В волне A lists-агент не запускался; код Lists в diff волны A не трогался. Нарушений нет.

## A-критерии (построчно, тест прочитан — не вырожден)
- **A1 ✅** `test_crud_and_hierarchy`. CRUD типизированного списка/элементов, иерархия (папка→ребёнок, `parent_id`/`list_id`), breadcrumb-структура через дерево, update/delete.
- **A2 ✅** `test_gift_hidden_from_excluded` (первый Shareable: элемент `hidden_from=[партнёр]` не виден исключённому в выдаче, истории→404, поиске; виден владельцу) + `test_personal_list_invisible_to_partner` (PERSONAL-список не виден целиком). Композированная видимость (контейнер `scope_visible(List)` + элемент `not_hidden(ListItem)`) через kernel-клаузы.
- **A3 ✅** `test_status_writes_completed_at_and_history` (DONE⟺completed_at, история STATUS_CHANGED×2) + `test_history_is_capped` (история = `HISTORY_CAP=50`, inline-обрезка в `item_repository.add_history`). I4 + кап (L3) подтверждены.
- **A4 ✅** `test_cross_list_nesting_rejected` (вложение между списками → CrossListNesting), `test_max_depth_enforced` (глубина > MAX_DEPTH=10 → MaxDepthExceeded), `test_specific_attrs_validated_by_type` (валидный `{year}` ок, `{bogus}` → InvalidSpecificAttrs). I2/I3/I5.
- **A5 ✅** `test_analytics_counts_only_visible_done`. Аналитика считает только видимые зрителю DONE (владелец: done=2; партнёр: done=1 — секрет с `hidden_from` исключён). Не вырожден.
- **A6 ✅** import-linter-контракт «Analytics reads Lists only via read-module» KEPT (проверка в `test_architecture.py`/линтере; в комментарии теста зафиксировано).
- **A7 ✅ (РЕАЛИЗОВАН — дельта закрыта).** `test_search_matches_list_names_respecting_visibility`: `/lists/search` возвращает И элементы (по тексту), И списки по совпадению названия (`kind:"list"`), видимость соблюдена.

## A7 — детальная проверка (особое внимание брифа)

**Вопреки ожиданию брифа («вероятно НЕ реализован»), A7 реализован end-to-end и покрыт неразвёрнутым тестом.**

Прочитан весь путь поиска:
- **Репозитории:** `list_repository.search_by_name(viewer, query)` — `select(List).where(scope_visible(List, viewer), List.name.ilike(...))`; `item_repository.search(viewer, query)` — элементы с `scope_visible`+`not_hidden`.
- **Сервис:** `ListService.search` собирает `SearchHit("item", list, item)` для текстовых совпадений элементов **и** `SearchHit("list", lst)` для совпадений по названию списка. Оба вида возвращаются.
- **API:** `get_search` (`GET /api/v1/lists/search?q=`) сериализует `{kind, list:{id,type,name}, item?}` — list-хит без `item`, item-хит с полным `_item_dto_full`. Соответствует `SearchHitDTO` ТЗ §10.

**Тест не вырожден:** `test_search_matches_list_names_respecting_visibility` создаёт FAMILY-список «Кино семейное» и PERSONAL «Кино личное». Владелец (v1) по запросу «Кино» получает list-хиты `{fam, personal}`; партнёр (v2) — только `{fam}` (PERSONAL-список **не** виден в поиске по названию). То есть проверяется и наличие list-хитов, и соблюдение видимости. Item-хиты отдельно покрыты в `test_gift_hidden_from_excluded` (`kind=="item"`, скрытый элемент не в выдаче партнёра).

## Инварианты с зубами
- **Видимость (kernel I10)** — все чтения списков/элементов идут через `scope_visible`/`not_hidden` kernel-клаузы; нет прямых выборок мимо фильтра. Композиция контейнер+элемент подтверждена в репозиториях.
- **clear-done (Q1-А6)** — `test_clear_done_skips_items_hidden_from_viewer` + `test_clear_done_cascades_children`: партнёр чистит DONE и удаляет только видимые ему (скрытый подарок цел); дети DONE-узла уходят каскадом по FK.
- **hidden_from-инварианты I7/I8/I9** — `test_hidden_from_invariants` (PERSONAL не допускает hidden_from; владелец не в hidden_from; только члены семьи) + `test_move_prevents_cycle` (защита от циклов дерева).

## Findings
1. **[низкий, портируемость]** `List.name.ilike(...)` на SQLite case-insensitive только для ASCII (`lower()` не трогает кириллицу), поэтому тест A7 намеренно использует совпадающий регистр в названии и запросе (комментарий в тесте это фиксирует). На Postgres `ilike` корректно работает с кириллицей; на SQLite-MVP поиск по русским названиям регистро-чувствителен по факту. Не противоречит критерию (видимость и оба `kind` соблюдены), но даёт неполное UX-совпадение по регистру на текущей БД. Кандидат на учёт при переезде на Postgres (ADR-0005); FTS-абстракция уже отложена в North Star (§14).
2. **[инфо]** `SearchHitDTO.item` для list-хита отсутствует (как и предписано ТЗ §10 — list-хит несёт только `list`). Зафиксировано как соответствие, не дефект.

## Парковки (оценка)
Открытых парковок по Lists в `docs/05_implementation/questions/` нет (S2a закрыт ранее; A7 был помечен «доделать на приёмке» — доделан). Открытый вопрос по cascade-скрытию из памяти проекта к критериям A1–A7 не относится и приёмку не блокирует.

## Условия приёмки
Нет (вердикт ✅). A7 (главный риск брифа) — реализован и покрыт неразвёрнутым тестом. Finding 1 — портируемость регистра поиска на SQLite, низкий, не блокер.
