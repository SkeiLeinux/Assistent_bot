"""Контекст Finance — бюджет vs факт + Import ACL (ТЗ spec-finance v0.3).

Публичная граница для delivery — `finance.api`; для Analytics — `finance.read`.
Внутри: модели, репозитории, сервисы аналитики/таксономии, заменяемый ACL
`importing/` (xlsx, openpyxl заперт) и `exporting/` (xlsx-выгрузка). Зависит
только от kernel и db (import-linter). Весь домен — FAMILY (скоупинг family_id).
"""

