import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    allowed_user_ids_raw: str = ""
    # Telegram ID администратора — сюда приходят уведомления об ошибках.
    # Узнать свой ID можно у @userinfobot в Telegram.
    admin_telegram_id: int | None = None

    # Ключ Groq API для расчёта КБЖУ (модуль "Еда").
    # Бесплатный ключ: https://console.groq.com/keys
    groq_api_key: str | None = None
    # Имя модели вынесено в конфиг — легко переключить на новую версию
    # или подменить на локальную модель, не трогая код.
    #
    # ВНИМАНИЕ (2026-07-25): llama-4-scout отключён Groq 17.07.26, до него так же
    # ушли Maverick (03.26) и llama-guard-4 (03.26). На Groq осталась РОВНО ОДНА
    # vision-модель — qwen/qwen3.6-27b, и она в статусе **preview** («может быть
    # снята с коротким предупреждением»). Системное решение по провайдеру —
    # ADR-0014; здесь только разблокировка модуля.
    groq_model: str = "qwen/qwen3.6-27b"
    # Базовый URL LLM-провайдера (ADR-0017 §6). Дефолт — Groq (origin: SDK Groq
    # сам дописывает путь /openai/v1/chat/completions, поэтому здесь именно origin,
    # без версии). Прод ходит в Groq через AsyncGroq. Раннер eval подставляет свою
    # цель на других провайдеров и берёт для них универсальный OpenAI-совместимый
    # клиент (openai SDK уважает base_url С версией пути; см. make_client). Так
    # подтвердился вывод ADR-0017 §14.1: base_url на клиенте Groq — недостаточная
    # абстракция, шов проходит по выбору клиента на провайдера, а не только по URL.
    llm_base_url: str = "https://api.groq.com"
    # Ключи прочих провайдеров для замера eval (ADR-0017 §8, T2). В проде не
    # используются (прод ходит в Groq), но объявлены здесь, чтобы pydantic не падал
    # на их наличии в .env и чтобы раннер брал их из settings, а не парсил .env.
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    claude_api_key: str | None = None  # Anthropic (кандидат ADR-0017 §2, Haiku)
    gemini_api_key: str | None = None  # закрыт по гео (ADR-0017 §14); держим ключ
    # Лимит изображений на один запрос — свойство ПРОВАЙДЕРА, не домена.
    # qwen3.6-27b: model card говорит 3, страница /docs/vision — 5 (расхождение
    # в доках Groq). Берём меньшее: 3 проходит при любом из двух лимитов.
    # У scout было 5 — расхождение с ТЗ/openapi (`images[≤5]`) осознанное, снимается
    # в рамках ADR-0014 (см. §Открытые вопросы: ошибка vs обрезка vs два вызова).
    groq_max_images: int = 3
    # qwen3.6 — двухрежимная (thinking / non-thinking). Для расчёта КБЖУ нужен
    # non-thinking: reasoning-токены съедают бюджет max_tokens и ломают JSON-парсер.
    # Пустая строка = не передавать параметр (для моделей без reasoning).
    groq_reasoning_effort: str = "none"

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