import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    allowed_user_ids_raw: str = ""
    # Telegram ID администратора — сюда приходят уведомления об ошибках.
    # Узнать свой ID можно у @userinfobot в Telegram.
    admin_telegram_id: int | None = None

    # ── LLM-провайдер расчёта КБЖУ (ADR-0017 §16, ТЗ контракта §10) ─────────
    # Победитель замера 25 эталонов яруса A (2026-07-27): OpenAI `gpt-5.4-mini`
    # — 92% kcal-hit ≤2%, 1.3 с, $0.41/мес. Прежний провайдер удалён из кода целиком
    # (история — ADR-0017): его единственная vision-модель была в статусе preview,
    # и её отключение 17.07.26 положило разбор КБЖУ немо. Имена `llm_*`, а не
    # `openai_*`, потому что шов проходит по OpenAI-совместимому
    # маршруту (base_url + ключ), а не по вендору — победитель и вторичный таргет
    # ADR-0018 ходят по нему нативно, поэтому адаптера под вендора не нужно.
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_vision_model: str = "gpt-5.4-mini"
    # Текстовая ветка отдельно НЕ замерялась (ADR-0017 §17) — до замера та же
    # модель. Поле заведено, чтобы расщепление text/vision не требовало правки
    # конфига; маршрутизация текстовых путей на него — вместе с промптами (блок 2-4).
    llm_text_model: str = "gpt-5.4-mini"
    # Лимит изображений на ОДИН запрос — свойство ПРОВАЙДЕРА, не домена.
    # Превышение больше не обрезается молча: снимки уходят батчами по этому
    # лимиту, позиции конкатенируются и дедуплицируются (ТЗ §5.2/§5.5).
    llm_max_images: int = 5
    # Сколько батчей одного разбора идут параллельно (ТЗ §5.2). 2 — компромисс:
    # латентность падает вдвое, а лимиты провайдера по RPM не выедаются.
    llm_batch_concurrency: int = 2
    # "" = не передавать reasoning_effort. Замер показал вред для извлечения:
    # у сильных моделей 100% → 94/88% при ×3 выходных токенов и ×2 задержке.
    llm_reasoning_effort: str = ""
    llm_temperature: float = 0.2
    # GPT-5.x отвергают `max_tokens` и требуют `max_completion_tokens`
    # (проверено 2026-07-27). Имя параметра — свойство провайдера, не кода.
    llm_max_tokens_param: str = "max_completion_tokens"
    # Таймаут и ретраи 429 — блок 11 (ADR-0018), ЗАВЕДЕНЫ, НО ЕЩЁ НЕ ИСПОЛЬЗУЮТСЯ.
    llm_timeout_s: float = 20.0
    llm_retry_429: int = 2
    # Бюджет вывода (ТЗ §5.3): base + per_item × число снимков в батче, но не
    # меньше 1500. Основание — фактический расход на ярусе A 1802–2174 токена
    # на вызов. Обрезка по бюджету видна через finish_reason="length" и лечится
    # одним ретраем с удвоенным бюджетом, а не выглядит как «кривой JSON».
    llm_output_tokens_base: int = 700
    llm_output_tokens_per_item: int = 220
    llm_output_tokens_min: int = 1500

    # ── Проверки ответа модели (ТЗ §6) ──────────────────────────────────────
    # Откалиброваны на 25 эталонах и 4 сохранённых прогонах:
    # 05_implementation/llm-checks-calibration-2026-07-27.md. Не подбирать заново
    # «на глаз»: у порогов есть обоснование в данных, а у 0.20 — коридор 13–40%,
    # где любое значение даёт один и тот же результат.
    # Ни одна проверка НЕ отклоняет ответ (§2.2) — только маршрут и предупреждения.
    check_atwater: float = 0.20          # один порог; пара 0.20/0.35 на данных неразличима
    check_kj_lo: float = 3.6             # полоса kcal/Атуотер, подтверждённая
    check_kj_hi: float = 4.8             # тремя реальными кДж-утечками, 0 ложных
    check_kcal_max: float = 900          # чистый жир = 900 ккал/100 г — физический потолок
    check_macros_sum_max: float = 102    # детектор ловушки per-portion, не только санити
    check_portion_g_max: float = 5000    # на данных не проверялось — санити
    # C6: расхождение Σ граммовок ингредиентов с ЗАЯВЛЕННЫМ весом блюда.
    # Знаменатель — заявленный вес (ТЗ §6.4), не Σ: на C-05 разница между
    # формулами — это разница между 42.9% (срабатывает) и ровно 30.0% (молчит).
    check_weight_mismatch: float = 0.30

    # ── Лестница фолбэков (ТЗ §7) ───────────────────────────────────────────
    # label-preferred вместо label-short-circuit: числа с этикетки остаются
    # основными, но при наличии бренда сверяются с источником, и расхождение
    # показывается пользователю обеими версиями.
    resolver_verify_label: bool = True
    resolver_disagree_pct: float = 0.15

    # Ключи прочих провайдеров для замера eval (ADR-0017 §8, T2). В проде не
    # используются, но объявлены здесь, чтобы pydantic не падал на их наличии
    # в .env (extra_forbidden) и чтобы раннер брал их из settings, а не парсил .env.
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    claude_api_key: str | None = None  # Anthropic (кандидат ADR-0017 §2, Haiku)
    gemini_api_key: str | None = None  # закрыт по гео (ADR-0017 §14); держим ключ

    # ── Резолвер КБЖУ (ADR-0013 / spec-nutrition-retrieval §12) ─────────────
    # Open Food Facts: бесплатно, без ключа, но обязателен кастомный User-Agent.
    # Фича-флагов нет — резолвер всегда включён, надёжность даёт деградация
    # лестницы (источник не ответил → следующая → в пределе estimate).
    off_base_url: str = "https://world.openfoodfacts.org"
    # User-Agent обязателен по правилам OFF (имя приложения + реальный контакт).
    # Дефолт уже валиден для прода; при необходимости переопределить в .env.
    off_user_agent: str = "lifeManager_bot (skyline987650@gmail.com)"
    off_lc: str = "ru"            # локаль поиска OFF (RU-выдача)
    off_timeout_s: float = 2.5    # таймаут одного запроса к OFF
    # Мягкий бюджет на ВСЕ сетевые ступени одного ввода: при превышении
    # оставшиеся позиции отдаём как estimate без сети (числа уже есть).
    resolver_total_budget_s: float = 12.0

    # ── Мини-апа "Еда" (Telegram Web App) ──────────────────────────────────
    # Внутренний адрес, на котором aiohttp слушает в одном процессе с ботом.
    # За HTTPS отвечает внешний слой (cloudflared-туннель или nginx+TLS).
    webapp_host: str = "127.0.0.1"
    webapp_port: int = 8080
    # Публичный HTTPS-URL мини-апы (туннель/домен). Кнопка запуска появляется
    # в боте только если он задан. Пример: https://eda.example.com
    webapp_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    @property
    def allowed_user_ids(self) -> list[int]:
        if not self.allowed_user_ids_raw.strip():
            return []
        return [
            int(x.strip())
            for x in self.allowed_user_ids_raw.split(",")
            if x.strip().isdigit()
        ]


settings = Settings()