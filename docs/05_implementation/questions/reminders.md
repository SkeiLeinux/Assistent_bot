# Парковка вопросов — Reminders (волна B)

## Q-rem-1: текущий `notifications.deliver` не принимает кнопки / получателей-User / kind
- Где: ТЗ Notifications §8 («`deliver(recipients: list[User], text, *, buttons: list[Button] | None, kind) -> DeliveryReport`») vs фактический `app/notifications/__init__.py` (`deliver(bot: Bot, chat_ids: list[int], text: str) -> None`) — S0-примитив.
- Проблема: Reminders по ТЗ §6.3/§7 (notifications §7, R1) должен слать push с **двумя** инлайн-кнопками `✅ Выполнено` (payload `rem:ack:{id}`) и `⏰ Отложить 1 ч` (`rem:snooze:{id}`) и резолвить получателей по scope как `list[User]`. Текущая публичная функция этого не умеет (нет buttons/recipients/kind, принимает `Bot` + `chat_ids`). Расширение `app/notifications/**` — НЕ моя граница записи (это delivery-agent/интегратор).
- Варианты:
  1. **(выбран как обход)** Reminders зависит от инъектируемого порта `Notifier` с **целевой** сигнатурой ТЗ §8 (`app/contexts/reminders/services/delivery_port.py`: `Notifier`, `Button`, `reminder_buttons`); прод-адаптер к расширенному `notifications.deliver` строит delivery-agent/интегратор при композиции в `main.py`. Домен Telegram-агностичен (ADR-0004), в тестах — мок-счётчик. Trade-off: до расширения `notifications` прод-доставка кнопок не работает — нужен адаптер от delivery-agent.
  2. delivery-agent расширяет `notifications.deliver` до сигнатуры §8 (buttons/recipients/kind + DeliveryReport) и маршрутизацию callback'ов `rem:ack`/`rem:snooze` в `delivery/bot` → `ReminderService.ack/snooze`. Это его волна-B бриф; согласовать форму `Button` (моя — `{text, callback_data}`) с aiogram `InlineKeyboardButton`.
  3. Положить общий тип `Button`/`Notifier` в `notifications` и импортировать его из reminders. Trade-off: создаёт ребро `reminders → notifications` на типах; ADR-0004 ребро Rem→Notif допускает, но порт в домене (вариант 1) держит домен полностью агностичным и тестируемым без notifications.
- Обход в коде: реализован вариант 1. Порт `Notifier` + `Button` + хелперы `ack_button/snooze_button/reminder_buttons` в `app/contexts/reminders/services/delivery_port.py`; `reminder_poll` принимает `notifier` снаружи; callback-payload'ы `rem:ack:{id}`/`rem:snooze:{id}` уже сформированы. Прод-адаптер `Notifier`→`notifications.deliver` и маршрутизация нажатий — за delivery-agent/интегратором (НЕ в моей границе).

## Q-rem-2: регистрация job `reminder_poll` и DI грейса/часов — композиция в main.py — ⏳ FOLLOW-UP (не блокер)
> **Статус (приёмка волны B, `reviews/review-waveB-2026-06-10.md` finding 1):** функционально закрыто вариантом 1 — грейс берётся из **дефолта фабрики `DEFAULT_GRACE_SECONDS = 3 ч`** (= спека), `make_reminder_poll(async_session_maker, notifier)` зарегистрирован в `main.py`. Буква OQ-R2 («грейс в конфиг») **отложена**: поля в `app/config.py` нет → вынесено в follow-up, приёмку не блокирует.

- Где: ТЗ §6 («Регистрация job — в `main.py`»), build-contract §6.5 (волна B: регистрация job `reminder_poll` + `digest` — интегратор).
- Проблема: Reminders экспортирует `make_reminder_poll(session_factory, notifier, *, now_fn, grace_seconds)` (callable `() -> Awaitable[None]`, совместимый со `scheduler.Scheduler.register`). Где берётся грейс-конфиг (дефолт 3 ч, R2) — сейчас литерал `DEFAULT_GRACE_SECONDS` в коде; ТЗ §6/OQ-R2 говорит «в конфиг». Добавление поля в `app/config.py` — НЕ моя граница.
- Варианты:
  1. **(обход)** грейс — параметр `grace_seconds` фабрики с дефолтом `DEFAULT_GRACE_SECONDS = 3ч`; интегратор передаёт значение из `settings` при регистрации, если/когда поле появится в `config.py`.
  2. интегратор добавляет `reminder_grace_seconds`/`scheduler_interval_seconds` в `app/config.py` и пробрасывает в `make_reminder_poll` (рекомендация — закрыть OQ-R2 «в конфиг» по букве).
- Обход в коде: вариант 1 — дефолт в коде, параметр фабрики для DI. Регистрация в `main.py` и проброс конфига — интегратор (вне границы).
