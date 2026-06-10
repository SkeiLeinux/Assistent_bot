"""kernel Shareable — контракт видимости (ТЗ Identity §3.5/§7, ADR-0001 H3-a).

Композируемые миксины видимости:
  Scoped    — owner_id / family_id / scope         (контейнерный дескриптор)
  Hideable  — hidden_from + таблица <table>_hidden_from (H3-a, поэлементное скрытие)
  Shareable — Scoped + Hideable                     (обе грани на одной сущности)

Зачем расщеплено: гранулярность `scope` и `hidden_from` может различаться (kernel §7).
Lists применяет это двухуровнево — `scope` на List (контейнер, Scoped), `hidden_from`
на ListItem (элемент, Hideable). Полный Shareable остаётся для сущностей, которым обе
грани нужны на одной таблице (синтетический харнесс видимости в тестах, A5).

Чтения идут ТОЛЬКО через kernel.sharing (`visible_to` / `scope_visible` / `not_hidden`) —
не переопределять в доменах (I10).
"""

import enum

from sqlalchemy import Column, Enum, ForeignKey, Table
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship


class Scope(str, enum.Enum):
    PERSONAL = "personal"
    FAMILY = "family"


class Scoped:
    """Контейнерный дескриптор видимости: владелец, семья, режим scope."""

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


class Hideable:
    """Поэлементное скрытие: hidden_from + ассоциативная таблица (H3-a)."""

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
        """Ассоциативная таблица исключений этой сущности (для not_hidden)."""
        return cls.metadata.tables[f"{cls.__tablename__}_hidden_from"]


class Shareable(Scoped, Hideable):
    """Обе грани видимости на одной сущности (scope + поэлементное скрытие)."""
