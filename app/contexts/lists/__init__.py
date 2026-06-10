"""Lists — типизированные списки (write-side, ТЗ spec-lists.md).

Первый контекст, применяющий kernel-Shareable (двухуровнево: scope на List,
hidden_from на ListItem). Публичная поверхность для delivery — api.register_routes
и services.ListService; для analytics (S3) — только read.* (ADR-0004, A6).

change_events — порт real-time push (F6.2): Lists эмитит нейтральный
ListChangeData; delivery подключает слушатель через set_change_listener (инверсия
зависимости, чтобы не тянуть ребро contexts→delivery).
"""

from app.contexts.lists.change_events import (
    ListChangeData,
    emit,
    set_change_listener,
)

__all__ = ["ListChangeData", "emit", "set_change_listener"]
