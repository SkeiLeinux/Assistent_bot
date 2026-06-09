"""app/db/base.py — Declarative Base (leaf).

Единая точка metadata для всех моделей и для alembic. Вынесен из db/models.py,
чтобы engine и alembic импортировали Base, не таща определения моделей, и наоборот.
app/db/ — нижний слой графа (контракт import-linter): его импортируют все,
сам внутреннее не импортит.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
