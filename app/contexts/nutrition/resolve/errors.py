"""resolve/errors.py — ошибки ступеней-источников (spec-nutrition-retrieval §13).

Сбой ступени (таймаут/HTTP≠200/кривой ответ) НЕ роняет разбор: pipeline ловит
`TimeoutError`/`SourceError`, пишет warning и переходит к следующей ступени; в
пределе всегда отвечает `estimate`. Ошибки извлекателя (LLM) — отдельные, живут в
`services.llm_service` (LLMError и подклассы) и пробрасываются как сейчас (REST 502).
"""


class SourceError(Exception):
    """Ступень-источник не смогла ответить корректно → переход к следующей."""
