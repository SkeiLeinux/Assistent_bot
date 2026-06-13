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
    # llama-4-scout — мультимодальная (текст + изображения), есть JSON-режим.
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"

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