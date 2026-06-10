"""Маппер MonthParse → домен: идемпотентный upsert по месяцу (ТЗ §7 шаг 6, I6/I7).

Резолв категорий:
  - канон по `normalize_name` + таблица алиасов (явных);
  - существующая (parent, name) → переиспользуется (идентичность I2);
  - невиданная → создаётся со статусом NEEDS_REVIEW (I7), НИКОГДА не сливается по
    нечёткому совпадению.
Upsert данных месяца: сначала снос (BudgetPlan/ActualAmount/Income за месяц),
затем запись — повторный импорт перезаписывает без дублей (I6). Всё в одной
транзакции вызывающего (сервис делает commit/rollback) — частичной записи нет (I3).

ВАЖНО: маппер вызывается ТОЛЬКО после валидации всех месяцев (validate_all) —
fail-loud случается до любой мутации.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.contexts.finance.models import Category, CategoryStatus, IncomeKind
from app.contexts.finance.repositories import (
    ActualRepository,
    BudgetRepository,
    CategoryRepository,
    IncomeRepository,
)

from .model import MonthParse, ParsedCategory, ParsedParent
from .normalize import display_name, normalize_name


@dataclass
class MonthResult:
    month: date
    sheet_name: str
    categories: int = 0
    income: int = 0


@dataclass
class ImportOutcome:
    months: list[MonthResult] = field(default_factory=list)
    created_for_review: list[Category] = field(default_factory=list)


class DomainMapper:
    """Применяет распознанные месяцы к домену под `family_id`."""

    def __init__(self, session, family_id: int) -> None:
        self.session = session
        self.family_id = family_id
        self.categories = CategoryRepository(session)
        self.budgets = BudgetRepository(session)
        self.actuals = ActualRepository(session)
        self.incomes = IncomeRepository(session)
        # Кэш резолвнутых категорий в пределах одного импорта.
        self._root_cache: dict[str, Category] = {}
        self._child_cache: dict[tuple[int, str], Category] = {}
        self._review: list[Category] = []

    async def apply(self, parses: list[MonthParse]) -> ImportOutcome:
        outcome = ImportOutcome()
        for parse in parses:
            res = await self._apply_month(parse)
            outcome.months.append(res)
        outcome.created_for_review = self._review
        return outcome

    async def _apply_month(self, parse: MonthParse) -> MonthResult:
        month = parse.month
        # Идемпотентность (I6): снести данные месяца ПЕРЕД записью.
        await self.budgets.delete_month(self.family_id, month)
        await self.actuals.delete_month(self.family_id, month)
        await self.incomes.delete_month(self.family_id, month)

        cat_count = 0
        for parent in parse.parents:
            parent_cat = await self._resolve_root(parent)
            await self._write_category_data(parent_cat, parent, month, is_leaf=False)
            cat_count += 1
            for child in parent.children:
                child_cat = await self._resolve_child(parent_cat, child)
                await self._write_category_data(child_cat, child, month, is_leaf=True)
                cat_count += 1

        income_count = await self._apply_income(parse, month)
        await self.session.flush()
        return MonthResult(
            month=month,
            sheet_name=parse.sheet_name,
            categories=cat_count,
            income=income_count,
        )

    # ── Резолв таксономии (I2/I7) ─────────────────────────────────────────────

    async def _resolve_root(self, parent: ParsedParent) -> Category:
        disp = display_name(parent.name)
        canon = normalize_name(parent.name)
        if canon in self._root_cache:
            return self._root_cache[canon]
        # Явный алиас (только канон → существующая категория).
        aliased = await self._resolve_alias(canon)
        if aliased is not None and aliased.parent_id is None:
            self._root_cache[canon] = aliased
            return aliased
        existing = await self.categories.find_root(self.family_id, disp)
        if existing is not None:
            self._root_cache[canon] = existing
            return existing
        created = await self.categories.create(
            family_id=self.family_id,
            name=disp,
            parent_id=None,
            status=CategoryStatus.NEEDS_REVIEW,  # невиданная (I7)
        )
        self._review.append(created)
        self._root_cache[canon] = created
        return created

    async def _resolve_child(
        self, parent_cat: Category, child: ParsedCategory
    ) -> Category:
        disp = display_name(child.name)
        canon = normalize_name(child.name)
        key = (parent_cat.id, canon)
        if key in self._child_cache:
            return self._child_cache[key]
        aliased = await self._resolve_alias(canon)
        if aliased is not None and aliased.parent_id == parent_cat.id:
            self._child_cache[key] = aliased
            return aliased
        existing = await self.categories.find_child(
            self.family_id, parent_cat.id, disp
        )
        if existing is not None:
            self._child_cache[key] = existing
            return existing
        created = await self.categories.create(
            family_id=self.family_id,
            name=disp,
            parent_id=parent_cat.id,
            status=CategoryStatus.NEEDS_REVIEW,  # невиданная (I7)
        )
        self._review.append(created)
        self._child_cache[key] = created
        return created

    async def _resolve_alias(self, canon: str) -> Category | None:
        alias = await self.categories.find_alias(self.family_id, canon)
        if alias is None:
            return None
        return await self.categories.get(self.family_id, alias.category_id)

    # ── Запись данных категории ───────────────────────────────────────────────

    async def _write_category_data(
        self,
        cat: Category,
        parsed: ParsedParent | ParsedCategory,
        month: date,
        *,
        is_leaf: bool,
    ) -> None:
        # План — per-category (на родителе И/ИЛИ ребёнке, I4).
        if parsed.plan is not None:
            await self.budgets.add(
                category_id=cat.id, period_month=month, planned_amount=parsed.plan
            )
        # Факт хранится ТОЛЬКО на листьях (§4.4); родитель — агрегат (не пишем дни).
        if is_leaf:
            for day_num, amount in parsed.days.items():
                if amount == 0:
                    continue
                await self.actuals.add(
                    category_id=cat.id,
                    day=date(month.year, month.month, day_num),
                    amount=amount,
                )

    # ── Доходы ────────────────────────────────────────────────────────────────

    async def _apply_income(self, parse: MonthParse, month: date) -> int:
        count = 0
        for parsed in parse.income:
            if parsed.plan is None and parsed.fact is None:
                # Пустой источник (ни плана, ни факта) — заводим источник, но
                # строку дохода за месяц не создаём (нечего хранить).
                await self.incomes.get_or_create_source(
                    family_id=self.family_id,
                    name=display_name(parsed.name),
                    kind=IncomeKind(parsed.kind),
                )
                continue
            src = await self.incomes.get_or_create_source(
                family_id=self.family_id,
                name=display_name(parsed.name),
                kind=IncomeKind(parsed.kind),
            )
            await self.incomes.add(
                source_id=src.id,
                period_month=month,
                planned_amount=parsed.plan,
                actual_amount=parsed.fact,
            )
            count += 1
        return count
