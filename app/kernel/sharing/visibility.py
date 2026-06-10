"""kernel.sharing.visibility — механизм видимости (ТЗ Identity §5/§7, ADR-0001).

Единственная точка определения фильтра видимости. Домены НЕ переписывают его у себя —
композируют из этих клауз (I10). Фильтр — WHERE с NOT EXISTS (горячий путь §13).

Две композируемые грани (под разную гранулярность scope/hidden_from, kernel §7):
  scope_visible(model, v)  — контейнерный фильтр (Scoped):
      PERSONAL → owner_id == v.id
      FAMILY   → family_id == v.family_id
  not_hidden(model, v)     — поэлементный фильтр (Hideable):
      v.id NOT IN hidden_from(entity)
  visible_to(model, v)     — обе грани на одной сущности (Shareable): scope_visible ∧ not_hidden

Двухуровневые контексты (Lists) композируют сами: контейнер — scope_visible(List),
элемент — not_hidden(ListItem), join по принадлежности.
"""

from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, exists, or_
from sqlalchemy.sql.elements import ColumnElement

from app.kernel.models.shareable import Scope


@dataclass(frozen=True)
class Viewer:
    """Резолвнутый зритель: идентичность + его семья (если спарен)."""

    id: int
    family_id: int | None = None


def scope_visible(model: type, viewer: Viewer) -> ColumnElement[bool]:
    """Контейнерный фильтр Scoped-модели: personal→владелец, family→семья зрителя."""
    personal = and_(model.scope == Scope.PERSONAL, model.owner_id == viewer.id)
    if viewer.family_id is None:
        # Без семьи виден только personal-контент самого зрителя.
        return personal
    family = and_(model.scope == Scope.FAMILY, model.family_id == viewer.family_id)
    return or_(personal, family)


def not_hidden(model: type, viewer: Viewer) -> ColumnElement[bool]:
    """Поэлементный фильтр Hideable-модели: зритель не в hidden_from сущности."""
    hidden = model.hidden_from_table()
    return ~exists().where(
        and_(hidden.c.entity_id == model.id, hidden.c.user_id == viewer.id)
    )


def visible_to(model: type, viewer: Viewer) -> ColumnElement[bool]:
    """Полная видимость Shareable-сущности (scope + скрытие на одной таблице)."""
    return and_(scope_visible(model, viewer), not_hidden(model, viewer))


# ── Валидация дескриптора видимости в пейлоадах доменов ──────────────────────

class VisibilityError(ValueError):
    """Нарушение инварианта видимости (I7–I9). REST мапит в 422."""


class VisibilityDTO(BaseModel):
    """Общий DTO для задания полей Shareable в create/update доменов (ТЗ §10).

    Базовая (родитель-независимая) проверка I7 — здесь. Зависящие от родителя
    I8 (owner∉hidden) / I9 (hidden⊆family) применяет сервис контекста через
    validate_against (нужен owner_id и состав семьи).
    """

    scope: Scope
    hidden_from: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _personal_has_no_exclusions(self) -> "VisibilityDTO":
        if self.scope == Scope.PERSONAL and self.hidden_from:
            raise VisibilityError("scope=personal не допускает hidden_from (I7)")
        return self

    def validate_against(self, owner_id: int, family_member_ids: list[int]) -> None:
        """Проверки, зависящие от родителя (I8, I9). Зовёт сервис контекста."""
        if owner_id in self.hidden_from:
            raise VisibilityError("владелец не может быть в hidden_from (I8)")
        if not set(self.hidden_from) <= set(family_member_ids):
            raise VisibilityError("hidden_from содержит не-члена семьи (I9)")
