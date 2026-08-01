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
    # Лимит изображений на один запрос — свойство ПРОВАЙДЕРА, не домена.
    # 5 = контракт `images[≤5]` из ТЗ/openapi. ВНИМАНИЕ: до появления батчей
    # (ТЗ §5.2, блок 6) превышение по-прежнему даёт молчаливую обрезку в
    # `_cap_images`, просто на другом пороге (было 3 — лимит qwen3.6).
    llm_max_images: int = 5
    # "" = не передавать reasoning_effort. Замер показал вред для извлечения:
    # у сильных моделей 100% → 94/88% при ×3 выходных токенов и ×2 задержке.
    llm_reasoning_effort: str = ""
    llm_temperature: float = 0.2
    # GPT-5.x отвергают `max_tokens` и требуют `max_completion_tokens`
    # (проверено 2026-07-27). Имя параметра — свойство провайдера, не кода.
    llm_max_tokens_param: str = "max_completion_tokens"
    # Ниже — поля под блоки 11/13 волны 1 (таймаут, ретраи, бюджет вывода по
    # числу позиций). ЗАВЕДЕНЫ, НО ЕЩЁ НЕ ИСПОЛЬЗУЮТСЯ: поведение приходит
    # вместе с ADR-0018 (отказоустойчивость) и §5.3 ТЗ (finish_reason).
    llm_timeout_s: float = 20.0
    llm_output_tokens_base: int = 700
    llm_output_tokens_per_item: int = 220
    llm_retry_429: int = 2

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