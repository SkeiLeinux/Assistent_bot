"""Ступени-источники резолвера (адаптеры за NutritionSource)."""

from app.contexts.nutrition.resolve.sources.estimate import EstimateSource
from app.contexts.nutrition.resolve.sources.openfoodfacts import (
    OpenFoodFactsSource,
    close_shared_session,
)

__all__ = ["OpenFoodFactsSource", "EstimateSource", "close_shared_session"]
