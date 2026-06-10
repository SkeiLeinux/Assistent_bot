"""Exporting — выгрузка периода в xlsx (ТЗ A7, Q1-Б1). openpyxl заперт здесь.

Как и `importing/`, это xlsx-граница: openpyxl и форма выгрузки изолированы (A6).
Публичная функция `export_period` собирает книгу из результата аналитики.
"""

from .exporter import export_period

__all__ = ["export_period"]
