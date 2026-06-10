# Парковки фронта — Lists (волна C)

## Q-fe-lists-1: регистр enum-значений openapi ↔ код
- Где: `docs/api/openapi.yaml` (схемы ListType/ItemStatus/Scope) vs `app/contexts/lists/models/enums.py`, `app/kernel/models/shareable.py:Scope`.
- Проблема: openapi (и первоначальные брифы волны C) объявляют enum-значения в ВЕРХНЕМ регистре (`TASK`, `OPEN`, `FAMILY`), а фактический бэк сериализует/принимает `.value` в НИЖНЕМ (`task`, `open`, `family`). Фронт, построенный по openapi буквально, ловил бы 422 на фильтрах/мутациях.
- Обход в коде: lists.jsx построен против факта (lowercase) — рабочее приложение. Интегратор отдельно починил то же расхождение в finance.jsx (`?status=active`) и reminders.jsx (status-фильтр + cadence).
- Решение/рекомендация (владельцу openapi — С.): привести значения enum в `openapi.yaml` к нижнему регистру по всем контекстам. Это касается **всех** доменов (см. ниже), не только Lists. openapi я (волна C) не правлю — это его SSOT-владельца.

## Сводно по волне C — расхождение распространённое
Нижний регистр на проводе фактически у ВСЕХ enum: ListType, ItemStatus, HistoryAction, Scope (`personal/family`), CategoryStatus (`active/needs_review`), IncomeKind (`carryover/partner/...`), Recurrence (`none/daily/...`), ReminderStatus (`active/done/missed/cancelled`), Cadence (`daily/weekly`). openapi.yaml везде даёт верхний регистр — расходится с кодом. Финальные экраны построены против кода.
