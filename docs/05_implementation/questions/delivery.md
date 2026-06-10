# Парковка вопросов — delivery / notifications (волна B)

Формат — build-contract §4.

## Q-del-1: монтаж change-хука Lists на конкретные мутации — ✅ РЕШЕНО (S3.1)
- Где: spec-notifications §7 (источник Lists: «элемент добавлен / DONE / перенесён / удалён; список создан / удалён»); build-contract §3 (граница записи delivery-агента: `app/delivery/miniapp_api/**` — НЕ его; монтаж REST/хуков — интегратор).
- Проблема: матрица «событие → шлём» **влита в ТЗ v0.3** (Q1-Б3) и реализована (`app/delivery/bot/list_changes.py`: `NOTIFY_MATRIX`, `notify_list_change`, `ListChange`). Но точка вызова хука — после успешной мутации в `app/contexts/lists/api` — лежит выше delivery по слоям: Lists НЕ может звать delivery напрямую (import-linter: `app.delivery → app.contexts`, не наоборот).
- **Решение (вариант, близкий к #2, но без шины): порт-инверсия зависимости.**
  - Lists владеет нейтральным `ListChangeData` + модуль-левел портом-слушателем (`app/contexts/lists/change_events.py`: `set_change_listener`/`emit`; дефолт — no-op). Импортирует только kernel `Scope` (ниже по слоям — границы целы).
  - Роуты Lists (`app/contexts/lists/api/routes.py`) после успешной мутации зовут `await emit(session, ListChangeData(...))` (ДО commit auth_middleware, best-effort). Маппинг: `post_list→list_created`, `delete_list→list_deleted` (снапшот меты ДО удаления), `post_item→item_added`, `patch_item`: status→DONE→`item_done` / parent_id→`item_moved` / text→`item_text_edited` (последний хук отфильтрует матрицей, дубля нет), `delete_item→item_deleted` (снапшот текста+hidden_from ДО удаления), `clear_done` — НЕ эмитит (массовая уборка).
  - delivery даёт фабрику `make_lists_change_listener(notifier)` (`app/delivery/bot/list_changes.py`): мапит `ListChangeData`→`ListChange` (str-event→`ListChangeEvent`) и зовёт `notify_list_change`. delivery→contexts по слоям ОК.
  - Интегратор (`app/main.py`) на старте: `set_change_listener(make_lists_change_listener(notifier))`.
  - Эффект на границы: **ни одного нового ребра `contexts→delivery`** — Lists знает только свой порт, delivery подписывается. `lint-imports` — 7 kept / 0 broken.
- Тесты: `tests/test_list_change_wiring.py` (эмиссия из роутов: event/scope/family/hidden_from/item_text; clear_done не эмитит; фабрика-листенер мапит и шлёт; скрытый подарок не пушится исключённому; emit без слушателя — no-op); прежний `tests/test_delivery_changes.py` (матрица/видимость/рендер) — без изменений.

## Q-del-2: связывание callback-префикса `rem` → reminder_service
- Где: spec-notifications §7 (маршрутизация callback'ов в сервис источника); spec-reminders §9 (ack/snooze доступны и из push-кнопки); build-contract §3 (контексты delivery-агент не правит; `main.py` — интегратор).
- Проблема: generic callback-диспетчер (`app/delivery/bot/callbacks.py`: `CallbackDispatcher`, таблица префиксов) готов и протестирован на моке reminder-сервиса. Но привязка `register("rem", {"ack": ..., "snooze": ...})` к реальному `reminder_service` должна делаться в композиции (`main.py`), которая импортирует `contexts.reminders` — это вне границы записи delivery-агента (и reminders строится параллельно в этой же волне).
- Варианты:
  1. Интегратор в `main.py`: строит `CallbackDispatcher`, регистрирует `rem`→reminder_service.ack/snooze (внутри обработчика создаёт сервис на сессии из middleware), передаёт диспетчер в `build_dispatcher(callback_dispatcher=...)`. Trade-off: единственная точка склейки, delivery не импортирует reminders на загрузке (шов держится). **Рекомендуется.**
  2. delivery импортирует reminder_service напрямую. Trade-off: ломает изоляцию (delivery→contexts.reminders жёстко на загрузке) и порядок волн (reminders ещё может строиться).
- Обход в коде: диспетчер + router (`build_callback_router`, `build_dispatcher(callback_dispatcher=...)`) готовы; `ReminderClosed` → no-op + короткий ответ (edge §10) реализован. Реальная привязка `rem` — на интеграторе (вариант 1). Сигнатуры, которые ожидает обработчик: `reminder_service.ack(viewer, reminder_id)` и `reminder_service.snooze(viewer, reminder_id, minutes)`; если фактический API reminders отличается — адаптировать в обёртке-обработчике при склейке, диспетчер не трогать.
