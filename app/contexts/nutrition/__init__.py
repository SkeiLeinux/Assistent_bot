"""Nutrition — дневник питания с расчётом КБЖУ (write-side, ТЗ spec-nutrition.md).

Самый зрелый контекст: перенос дормант-модуля еды под kernel (вариант C —
переиспользование). Дельта (ТЗ §3): цели → NutritionGoals (1:1 по user_id),
SharedFood.family_id (FAMILY-каталог), FoodEntry PERSONAL, current_viewer/timezone
из kernel, REST под /api/v1/nutrition/*.

Видимость: дневник (FoodEntry) PERSONAL — по владельцу; каталог (SharedFood)
FAMILY — по семье; цели (NutritionGoals) PERSONAL. LLM-специфика заперта в
services.llm_service (провайдер-агностично, I7).

Публичная поверхность для delivery — api.register_routes и services.FoodService;
для analytics (S3) — только read.* (ADR-0004, A6).
"""

