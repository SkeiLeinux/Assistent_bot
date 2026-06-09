"""kernel Shareable — контракт видимости (ТЗ Identity §3.5/§7, ADR-0001 H3-a).

Миксин, примешиваемый к каждой разделяемой модели любого контекста. Даёт:
  owner_id / family_id / scope            — дескриптор видимости
  hidden_from: list[User]                 — исключения при FAMILY (подарок)
  per-entity таблица <table>_hidden_from  — персистенция исключений (H3-a)

Чтения идут ТОЛЬКО через kernel.sharing.visible_to (не переопределять в доменах, I10).
В Срезе 1 реальных доменных Shareable нет (Lists — S2); миксин проверяется
синтетической моделью в тестах (харнесс видимости, A5).
"""

import enum

from sqlalchemy import Column, Enum, ForeignKey, Table
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship


class Scope(str, enum.Enum):
    PERSONAL = "personal"
    FAMILY = "family"


class Shareable:
    @declared_attr
    def owner_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    @declared_attr
    def family_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("families.id"), nullable=False, index=True)

    @declared_attr
    def scope(cls) -> Mapped[Scope]:
        # Свежий Enum на каждый класс — не делим тип между таблицами.
        return mapped_column(
            Enum(
                Scope,
                values_callable=lambda e: [m.value for m in e],
                native_enum=False,
                length=20,
            ),
            nullable=False,
            default=Scope.FAMILY,
        )

    @declared_attr
    def hidden_from(cls) -> Mapped[list["User"]]:  # noqa: F821 — резолв по registry
        # Ассоциативная таблица на каждую сущность (H3-a): <table>_hidden_from.
        table = Table(
            f"{cls.__tablename__}_hidden_from",
            cls.metadata,
            Column(
                "entity_id",
                ForeignKey(f"{cls.__tablename__}.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            Column(
                "user_id",
                ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            keep_existing=True,
        )
        return relationship("User", secondary=table)

    @classmethod
    def hidden_from_table(cls) -> Table:
        """Ассоциативная таблица исключений этой сущности (для visible_to)."""
        return cls.metadata.tables[f"{cls.__tablename__}_hidden_from"]
