# Парковка вопросов — Analytics & Digest (S3, волна B)

Формат — build-contract §4. Здесь: пункты-контракты, требующие доработки **чужих**
read-модулей (зона агентов доменов / интегратора, build-contract §3). Граница записи
analytics-агента — только `app/analytics/**` + `tests/test_analytics*.py`; чужие
`contexts.*.read` не дописываются (см. бриф). Не «улучшено молча» — вынесено на
интеграционный шаг.

**Статус (волна B, точечный решейп 2026-06-10):** Q-an-1, Q-an-2 — ✅ РЕШЕНЫ
(read-проекции `lists.read.recent_changes` и `nutrition.read.goals` добавлены, обходы
сняты). Q-an-5 — снят (не требуется). Q-an-3 (top-3 трат), Q-an-4 (усечение >4096)
остаются как **принятые обходы в коде** Analytics (доработки read не требуют).

---

## Q-an-1: recent-changes списков по полям ListItem (CH-5) — ✅ РЕШЕНО
- **Статус (2026-06-10):** решено. Целевой вариант (B) реализован: `lists.read`
  получил проекцию `recent_changes(session, viewer, date_from, date_to) ->
  list[{type, added, done}]` (тип — строка `.value`, visibility-фильтровано, только
  типы с ненулевой активностью). Обход через per-type `analytics_by_type` снят.
- Где (исходно): ТЗ §7 «источник — recent-changes-проекция `lists.read` по полям
  `ListItem` (`created_at`/`completed_at` в периоде)»; влитое Q1-А10/CH-5: источник
  изменений = поля `ListItem`, не `ListItemHistory`.
- Что теперь зовётся: `app/analytics/read_composition.py::lists_overview` →
  `recent_changes(...)`, возвращает `by_type=[{type, added, done}]` за период
  `[last_sent_at.date(), now.date()]`. `app/analytics/digest.py::_lists_section`
  фильтрует типы с `added>0 or done>0` (recent_changes уже отдаёт только активные);
  рендер показывает «добавлено N, выполнено M» (тип только с добавлением — тоже
  выводится). Видимость (I3/A4) — внутри проекции (kernel-клаузы). `analytics_by_type`
  больше не зовётся из Analytics. Тесты: `test_analytics_overview.py`
  (`test_overview_aggregates_three_spheres`, `..._excludes_hidden_list_item`),
  `test_analytics_digest.py::test_digest_lists_section_carries_added_and_done`.

---

## Q-an-2: цели КБЖУ (goal в OverviewDTO/дайджесте) — ✅ РЕШЕНО
- **Статус (2026-06-10):** решено. Целевой вариант (B) реализован: `nutrition.read`
  получил проекцию `goals(session, viewer) -> {kcal,protein,fat,carbs}|None`
  (PERSONAL по `viewer.id`; отдельные поля могут быть `None`). Обход `goal=None` снят.
- Где (исходно): ТЗ §6 «Питание: КБЖУ за период vs цели (личное) — через
  `nutrition.read`»; §9 DTO `nutrition:{kcal,goal,…}`; §7 «личная сводка КБЖУ/цели».
- Что теперь зовётся: `read_composition.py::nutrition_overview` и
  `digest.py::_nutrition_section` → `goals(session, viewer)`. Есть строка целей →
  `goal={kcal,protein,fat,carbs}` (рендер «факт / цель ккал»); нет строки → `goal=None`
  (поведение как раньше — только факт). PERSONAL-фильтр: партнёрская цель не
  подтягивается. Тесты: `test_analytics_overview.py`
  (`test_overview_nutrition_goal_present`, `..._goal_none_when_absent`,
  `..._goal_personal_not_partner`), `test_analytics_digest.py`
  (`test_digest_nutrition_carries_goal_and_renders`,
  `..._goal_none_renders_fact_only`).

---

## Q-an-3: «крупные траты» (CH-7) считаются по daily_rows без имён категорий
- Где: ТЗ §7 «крупные траты = top-3 расходов периода по сумме, без порога»
  (Q1-Б4 влит).
- Проблема: «расход» в Finance — это `ActualAmount` строки `(category_id, date,
  amount)` на листьях. `finance.read.daily_rows` отдаёт именно их, но **без имён**
  категорий; имена есть в `finance_aggregate().categories`. Отдельной проекции
  «топ-N трат периода» в `finance.read` нет.
- Варианты:
  - (A, выбран как обход) top-3 считаются в analytics: берём `daily_rows`, группируем
    по `category_id`, суммируем, сортируем по убыванию, берём top-3; имена
    подтягиваем из `finance_aggregate().categories` (тот же read). «Трата» здесь =
    суммарный факт по категории-листу за период (а не одна транзакция — модель не
    хранит отдельные покупки, только разрежённый дневной факт `(category, date)`).
  - (B, целевой) проекция `finance.read.top_expenses(session, family_id, from, to, n)`
    с именами — если потребуется единичная транзакция «велосипед», нужна модель
    позиций трат (её нет в MVP).
- Обход в коде: реализован — `app/analytics/digest.py::_finance_section` (top-3 по
  сумме факта на листьях). Зафиксировано как «Обход в коде» (бриф: CH-7 не влит
  отдельной моделью — top-3 по сумме).

---

## Q-an-4: усечение длинного дайджеста >4096 (CH-8) — обход в коде
- Где: ТЗ — прямого правила нет; spec-notifications §10 edge «>4096 → усечение по
  строкам/секциям с хвостом «…и ещё N»; на несколько сообщений не разбиваем»
  (Q1-А5). Бриф: «длина >4096 (CH-8) — если не влиты: усечение секций с «…и ещё N»».
- Проблема: композиция дайджеста — в Analytics, а лимит/усечение — поведение
  доставки (Notifications). Кто режет: Analytics (на этапе текста) или Notifications
  (на доставке)?
- Варианты:
  - (A, выбран как обход) Analytics строит текст и сам усекает до лимита Telegram
    (4096) по строкам с хвостом «…и ещё N строк», чтобы дайджест был самодостаточным
    и не зависел от того, режет ли Notitifications. Если Notifications тоже режет —
    двойного вреда нет (идемпотентно: уже короткий текст не трогается).
  - (B, целевой) усечение — единая ответственность Notifications.deliver (по §10);
    Analytics отдаёт полный текст. Снять обход после согласования.
- Обход в коде: реализован — `app/analytics/digest.py::_truncate_telegram`.

---

## Q-an-5: ListType не ре-экспортирован из lists.read — ✅ НЕ ТРЕБУЕТСЯ
- **Статус (2026-06-10):** снято с повестки. После закрытия Q-an-1 секция списков
  идёт через `recent_changes`, который отдаёт `type` строкой (`.value`) — enum
  `ListType` в Analytics больше не нужен. Прежний обход (`typing.get_type_hints`
  поверх `analytics_by_type` для извлечения enum без импорта `lists.models`) удалён
  вместе с per-type циклом. `analytics_by_type` из Analytics не импортируется;
  прямого импорта `lists.models` нет (линтер A5 зелёный).
