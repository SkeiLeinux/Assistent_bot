"""Bounded contexts (write-side домены).

Плейсхолдеры Среза 0; наполняются по ТЗ в своих срезах: lists/finance/nutrition — S2,
reminders — S3. Контракт import-linter: contexts.<X> → kernel, db, свой подпакет,
notifications, scheduler; запрещено импортировать другие contexts.* / analytics / delivery.
"""
