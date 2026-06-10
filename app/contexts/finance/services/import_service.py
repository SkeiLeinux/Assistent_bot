"""ImportService — тонкая обёртка над Import ACL + список месяцев (ТЗ §9 S1/S2, §10).

Зовёт заменяемый `importing.import_xlsx` (parse→validate→map, fail-loud). Доменный
слой НЕ знает про openpyxl/форму таблицы — только про `bytes + year` (ADR-0004,
standalone-шов). Транзакцией владеет вызывающий (REST-middleware commit/rollback);
при ImportStructureError ничего не записано (I3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.kernel import Viewer

from app.contexts.finance.importing import ImportOutcome, import_xlsx
from app.contexts.finance.repositories import (
    ActualRepository,
    BudgetRepository,
    IncomeRepository,
)
from app.contexts.finance.services.errors import FamilyRequired


def _family(viewer: Viewer) -> int:
    if viewer.family_id is None:
        raise FamilyRequired("Сначала создай семью или вступи в неё (/family).")
    return viewer.family_id


@dataclass
class MonthInfo:
    month: date
    categories: int
    has_facts: bool


class ImportService:
    def __init__(self, session) -> None:
        self.session = session
        self.actuals = ActualRepository(session)
        self.budgets = BudgetRepository(session)
        self.incomes = IncomeRepository(session)

    async def import_file(
        self, viewer: Viewer, data: bytes, year: int
    ) -> tuple[ImportOutcome, list]:
        """Импорт xlsx. Бросает ImportStructureError (422) при несоответствии (I3)."""
        family_id = _family(viewer)
        return await import_xlsx(self.session, family_id, data, year)

    async def list_months(self, viewer: Viewer) -> list[MonthInfo]:
        """Месяцы с импортированными данными (факт ∪ план), для GET /months."""
        from sqlalchemy import func, select

        from app.contexts.finance.models import BudgetPlan, Category

        family_id = _family(viewer)
        plan_months = sorted(
            set(
                (
                    await self.session.execute(
                        select(BudgetPlan.period_month)
                        .join(Category, BudgetPlan.category_id == Category.id)
                        .where(Category.family_id == family_id)
                    )
                ).scalars().all()
            )
        )
        months = await self.actuals.months_with_any_data(family_id, plan_months)
        out: list[MonthInfo] = []
        for m in months:
            has_facts = await self.actuals.has_facts_for_month(family_id, m)
            # Кол-во категорий с любыми данными за месяц — приблизим числом
            # категорий семьи (дерево общее); точная привязка к месяцу — North Star.
            ncat = await self.session.scalar(
                select(func.count()).select_from(Category).where(
                    Category.family_id == family_id
                )
            )
            out.append(MonthInfo(month=m, categories=int(ncat or 0), has_facts=has_facts))
        return out
