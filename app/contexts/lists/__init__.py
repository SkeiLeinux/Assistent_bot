"""Lists — типизированные списки (write-side, ТЗ spec-lists.md).

Первый контекст, применяющий kernel-Shareable (двухуровнево: scope на List,
hidden_from на ListItem). Публичная поверхность для delivery — api.register_routes
и services.ListService; для analytics (S3) — только read.* (ADR-0004, A6).
"""
