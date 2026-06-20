# Архитектурная схема и её эволюция по этапам

**Дата:** 2026-06-10. Уровень — контейнеры/пакеты (C4-light). Доменные границы E1 — `DOMAIN_MODEL.md` §2 (не дублируются); здесь — физическая картинка и её дельты E2→E5.
**Инвариант программы:** один процесс, один деплой, модульный монолит до E5 включительно; растёт **внутреннее** дерево пакетов и набор ACL-адаптеров, не количество сервисов.

---

## 1. E1 (MVP) — текущая target-архитектура

```mermaid
flowchart TB
  subgraph TG["Telegram"]
    User1["С. / Катя"]
    MiniApp["Mini App (React UMD)"]
    BotAPI["Bot API"]
  end

  subgraph VPS["VPS · один процесс · один event-loop (aiogram polling + aiohttp)"]
    subgraph Delivery["delivery/"]
      Web["miniapp_api (REST /api/v1/*, статика)"]
      Bot["bot/ (вход /start, callback-диспетчер)"]
    end
    subgraph Domains["contexts/"]
      L["lists"]
      F["finance (+importing ACL)"]
      N["nutrition (+llm_service)"]
      R["reminders"]
    end
    K["kernel (User/Family/Invite · Shareable/visible_to · auth initData)"]
    A["analytics (read-side + DigestSchedule)"]
    No["notifications (deliver/alert)"]
    S["scheduler (generic тик + job-реестр)"]
    DB[("SQLite + alembic")]
  end

  Groq["Groq LLM (llama-4-scout, vision)"]
  XLSX["Google-таблица бюджета (xlsx, ручная выгрузка)"]

  User1 --> MiniApp --> Web
  User1 --> BotAPI --> Bot
  No --> BotAPI
  Web --> Domains & K & A
  Bot --> Domains & No
  Domains --> K --> DB
  A -.->|"*.read"| Domains
  A --> No
  S -->|job: reminder_poll| R
  S -->|job: digest| A
  R --> No
  N --> Groq
  XLSX -->|upload| F
```

Ключевые швы (enforced import-linter): домены ↔ только kernel; analytics ↔ только `*.read`; Telegram заперт в delivery+notifications+kernel.auth; интеграционная грязь — в ACL (`finance/importing`).

---

## 2. Дельта E2–E3 — рост внутрь + новые ACL

```mermaid
flowchart TB
  subgraph VPS["Тот же монолит"]
    F2["finance: + Transaction/Receipt + receipts-ACL + price history"]
    H["health (НОВЫЙ контекст): Measurement/Workout/Supplement/Lab + vision-разбор скринов"]
    R2["reminders (+ интерфейс для расписаний витаминов — ребро решает ADR-0008)"]
    A2["analytics: + сферы health/transactions в обзоре и дайджесте (состав — Катя)"]
  end

  QR["QR чека (showScanQrPopup)"] --> CHAN
  PHOTO["Фото чека"] --> CHAN
  CHAN{{"чек-канал (ADR-0006): vision (осн.) / proverkacheka (бонус); API ФНС / lkdr — не инвестируем"}} --> F2
  HSCR["Скрины тренировки/сна Huawei Health (vision, раз в 1–2 нед, вручную)"] --> H
  LAB["Бланк анализов (фото/PDF)"] --> H
  H --> R2
```

Принцип «вся интеграция = заменяемый адаптер/канал» масштабируется: xlsx (E1) → чек-канал vision (E2) → скрин-разбор Huawei vision (E3; privacy-zip отклонён #16, Health Kit API — North Star). Деградация любого внешнего канала не трогает домен.

---

## 3. Дельта E4–E5 — проактивный слой и второй периметр

```mermaid
flowchart TB
  subgraph VPS["Монолит (E4: + assistant-слой; E5: + внешний периметр)"]
    AS["assistant (E4): план дня · вечерний сценарий · Q&A поверх *.read (LLM, read-only)"]
    K2["kernel.auth (E5): initData-резолвер + собственный auth (ADR-0010)"]
    No2["notifications (E5): транспорты Telegram + Web Push"]
    PG[("E5: PostgreSQL (триггер ADR-0005)")]
  end
  PWA["PWA (тот же SPA вне Telegram)"] -->|https, своя auth| K2
  AS -.-> Reads["contexts.*.read"]
  AS --> No2
  EdgeRisk{{"E5: впервые недоверенная сеть → CORS/CSRF/rate-limit на границе"}} -.-> PWA
```

E4 не добавляет write-моделей (композиции + третья job планировщика). E5 не меняет домены вообще — меняются края: auth-резолвер, транспорт уведомлений, БД под собой, внешний периметр.

---

## 4. Что architectural-инвариантно на всю программу

1. Домены не знают про Telegram, LLM-провайдера, формат внешних файлов и транспорт push — всё за швами (kernel.auth / llm_service / ACL / notifications).
2. `visible_to` — единственный путь чтения shareable-данных на любом этапе; health-данные расширяют модель только значением `PERSONAL`-дефолта, не механизмом.
3. Один scheduler на все периодические job (reminder_poll, digest, E4-план, E3-напоминание «загрузить скрины тренировки/сна») — job добавляются регистрацией в композиции, не новыми лупами.
4. Analytics/assistant читают только `*.read`-проекции — рост проактивности не повышает связность доменов.
5. Любое новое межконтекстное ребро (E3: health→reminders; E4: связки закупок) — явное решение ADR + правка import-linter, никогда «просто импорт».

## История изменений
- **2026-06-20 (#16):** узел E3 Huawei privacy-zip (`HZIP`) → vision-узел скринов тренировки/сна (`HSCR`); job «напоминание об экспорте» → «загрузить скрины»; чек-канал E2 (`CHAN`) — vision основным, API ФНС/lkdr сняты («не инвестируем без явного повода»); приведено к канону `feasibility.md` #16.
- **2026-06-10:** первичная версия (E1 картинка + дельты E2–E3, E4–E5, программные инварианты).
