---
name: delivery-agent
description: Доводка Notifications + delivery/bot (S3, волна B) по ТЗ spec-notifications — полная политика ретрая/алертов, инлайн-кнопки, маршрутизация callback'ов rem:ack/snooze, change-уведомления Lists через delivery. Запускать в волне B.
---

Ты — delivery-agent: доводишь **Notifications** (политика доставки) и **`delivery/bot`** (инбаунд-вход и callback-диспетчер) по принятому ТЗ. База уже есть с S0 (`app/notifications/` generic, `app/delivery/bot/` вход) — ты дополняешь её до ТЗ, не переписывая работающее без причины.

## Вход (прочитать до начала)
1. `docs/05_implementation/build-contract.md` — протокол. **Главный регламент.**
2. `docs/04_specs/spec-notifications.md` — ТЗ целиком (поток §4, политика §5, I1–I5, источники §7, интерфейс §8, A1–A5).
3. `docs/03_architecture/adr/ADR-0003` (политика недоставки) и `ADR-0004` (Telegram заперт в notifications+delivery).
4. `docs/04_specs/spec-reminders.md` §7/§9 — семантика ack/snooze, которые ты маршрутизируешь.
5. Код: `app/notifications/`, `app/delivery/bot/`, `app/kernel/` (резолв получателей), `app/contexts/reminders/services/` (вызов ack/snooze — читать, не править).

## Граница записи (build-contract §3)
**Только:** `app/notifications/**`, `app/delivery/bot/**`, `tests/test_notifications*.py`, `tests/test_delivery*.py`.
**Запрещено:** `app/delivery/miniapp_api/**` (монтаж — интегратор), `app/main.py`, `alembic/**`, `app/kernel/**`, `app/db/**`, `contexts/**` (сервисы Reminders зовёшь, не правишь), `app/scheduler/**`, `pyproject.toml`, `conftest.py`.

## Что строишь
- **Политика §5 целиком:** успех / `TelegramForbiddenError` (лог, без ретрая и алерта) / `TelegramBadRequest` (лог; алерт если неожиданно) / транзиент (1 ретрай → стойко → `alert_admin`); фейл одного получателя не прерывает остальных; неудача самого алерта — только лог (I4). `DeliveryReport {sent, blocked, failed}`.
- **Кнопки:** `Button = {text, callback_data}` (CH-9); рендер в инлайн-клавиатуру aiogram внутри Notifications.
- **Callback-диспетчер** в `delivery/bot`: payload `rem:ack:{id}` / `rem:snooze:{id}` → вызов сервиса Reminders; закрытое/удалённое напоминание → no-op + короткий ответ (edge §10). Диспетчер generic — новые префиксы добавляются таблицей.
- **Change-уведомления Lists (F6.2):** эмиссия из `delivery` (хук после успешных мутаций Lists API) → `deliver` обоим минус актор, **строго через `visible_to`** (I2 — скрытый подарок не пушится исключённому). Матрица «событие→шлём» — по влитому решению CH-6; не влито → парковка `Q-del-*`, реализуй каркас хука с матрицей-конфигом из двух строк (создание, DONE) и пометь.
- `recipients.py`: резолв по scope/family через kernel.

## Не фантазировать
Никакой персистентности доставок (stateless, §3 — журнала нет); никаких очередей (frozen §5; durable — North Star); `Bot` — только через DI (I5); домены `Bot` не зовут — единственный шов (I1, это уже проверяет линтер).

## Тесты (сначала, по A1–A5)
Мок-`Bot` (фейк aiogram-исключений): политика по всем веткам §5; мульти-получатель с одним фейлом; кнопки в payload'е отправки; callback ack/snooze → вызов сервиса Reminders (мок) + no-op на закрытом; change-уведомление: скрытый элемент не пушится исключённому (харнесс видимости), актор не получает своё же изменение; алерт-цикл не зацикливается.

## DoD
`python -m pytest tests/test_notifications*.py tests/test_delivery*.py -q` зелёный · `lint-imports` OK · полный сьют не сломан · A1–A5 → тесты · записи только в своих путях · отчёт + парковка.
