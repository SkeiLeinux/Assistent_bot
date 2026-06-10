"""Репозиторий доходов (IncomeSource + Income) — FAMILY-скоуп (ТЗ §4.5/§4.6).

Источники — управляемые сущности (upsert по имени при импорте). Income —
план/факт за месяц; идемпотентность по месяцу (I6) через `delete_month`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.finance.models import Income, IncomeKind, IncomeSource


class IncomeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Источники ────────────────────────────────────────────────────────────

    async def find_source(self, family_id: int, name: str) -> IncomeSource | None:
        q = select(IncomeSource).where(
            IncomeSource.family_id == family_id, IncomeSource.name == name
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def get_or_create_source(
        self, *, family_id: int, name: str, kind: IncomeKind
    ) -> IncomeSource:
        existing = await self.find_source(family_id, name)
        if existing is not None:
            return existing
        src = IncomeSource(family_id=family_id, name=name, kind=kind)
        self.session.add(src)
        await self.session.flush()
        return src

    async def list_sources(self, family_id: int) -> list[IncomeSource]:
        q = select(IncomeSource).where(IncomeSource.family_id == family_id)
        return list((await self.session.execute(q)).scalars().all())

    # ── Доходы ───────────────────────────────────────────────────────────────

    async def delete_month(self, family_id: int, period_month: date) -> None:
        src_ids = select(IncomeSource.id).where(
            IncomeSource.family_id == family_id
        )
        await self.session.execute(
            delete(Income).where(
                Income.source_id.in_(src_ids),
                Income.period_month == period_month,
            )
        )

    async def add(
        self,
        *,
        source_id: int,
        period_month: date,
        planned_amount: Decimal | None,
        actual_amount: Decimal | None,
    ) -> Income:
        income = Income(
            source_id=source_id,
            period_month=period_month,
            planned_amount=planned_amount,
            actual_amount=actual_amount,
        )
        self.session.add(income)
        return income

    async def for_months(
        self, family_id: int, months: list[date]
    ) -> list[tuple[Income, IncomeSource]]:
        """Доходы + их источники за указанные месяцы (для отчёта/аналитики)."""
        if not months:
            return []
        q = (
            select(Income, IncomeSource)
            .join(IncomeSource, Income.source_id == IncomeSource.id)
            .where(
                IncomeSource.family_id == family_id,
                Income.period_month.in_(months),
            )
        )
        rows = (await self.session.execute(q)).all()
        return [(inc, src) for inc, src in rows]

    async def income_months(self, family_id: int) -> list[date]:
        src_ids = select(IncomeSource.id).where(
            IncomeSource.family_id == family_id
        )
        q = select(Income.period_month).where(Income.source_id.in_(src_ids))
        months = set((await self.session.execute(q)).scalars().all())
        return sorted(months)
