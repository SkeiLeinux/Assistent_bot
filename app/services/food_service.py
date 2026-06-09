"""
services/food_service.py — бизнес-логика дневника питания.

Разделение ответственности:
  - LLMService считает КБЖУ (text/photo) — но НЕ сохраняет.
  - FoodService решает что и как сохранить, агрегирует по дням, делает выгрузку.
  - Шаг подтверждения ("так? поправь порцию") живёт в UI: сначала analyze_*,
    показали пользователю позиции, он подтвердил — потом save_entries.

Время:
  created_at в БД — наивный UTC. "День" считаем в таймзоне пользователя
  (user.timezone), переводя локальные границы суток в UTC перед запросом.
"""

from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FoodEntry, SharedFood, User
from app.repositories import FoodRepository, SharedFoodRepository, UserRepository
from app.services.llm_service import LLMService, NutritionItem, NutritionResult

_DEFAULT_TZ = "Europe/Amsterdam"

# Порядок приёмов пищи для группировки дня. meal_type=None → "other" в конце.
MEAL_ORDER = ["breakfast", "lunch", "dinner", "snack"]
_OTHER_MEAL = "other"


@dataclass
class Totals:
    kcal: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carbs: float = 0.0

    def add(self, e: FoodEntry) -> None:
        self.kcal += e.kcal
        self.protein += e.protein
        self.fat += e.fat
        self.carbs += e.carbs


@dataclass
class Goals:
    """Дневные цели КБЖУ. None в поле = цель не задана."""
    kcal: float | None = None
    protein: float | None = None
    fat: float | None = None
    carbs: float | None = None

    @classmethod
    def from_user(cls, user: User) -> "Goals":
        return cls(
            kcal=user.target_kcal,
            protein=user.target_protein,
            fat=user.target_fat,
            carbs=user.target_carbs,
        )


@dataclass
class MacroProgress:
    """Прогресс по одному макросу для прогресс-бара: съедено / цель."""
    consumed: float
    target: float | None = None

    @property
    def percent(self) -> float | None:
        """Процент от цели (может быть > 100). None если цель не задана."""
        if not self.target:
            return None
        return round(self.consumed / self.target * 100, 1)

    @property
    def remaining(self) -> float | None:
        """Сколько осталось до цели (может быть отрицательным). None без цели."""
        if self.target is None:
            return None
        return round(self.target - self.consumed, 1)


@dataclass
class MealGroup:
    """Записи одного приёма пищи + итоги по нему."""
    meal_type: str  # breakfast/lunch/dinner/snack/other
    entries: list[FoodEntry] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)


@dataclass
class DayView:
    day: date
    entries: list[FoodEntry] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)
    goals: Goals = field(default_factory=Goals)

    def progress(self) -> dict[str, MacroProgress]:
        """4 прогресс-бара: ккал, белки, жиры, углеводы (съедено vs цель)."""
        return {
            "kcal": MacroProgress(self.totals.kcal, self.goals.kcal),
            "protein": MacroProgress(self.totals.protein, self.goals.protein),
            "fat": MacroProgress(self.totals.fat, self.goals.fat),
            "carbs": MacroProgress(self.totals.carbs, self.goals.carbs),
        }

    def by_meal(self) -> list[MealGroup]:
        """Записи дня, сгруппированные по приёму пищи, с итогами по группе.

        Порядок: завтрак → обед → ужин → перекус → other. Пустые группы
        пропускаются. Внутри группы сохраняется порядок по времени.
        """
        groups: dict[str, MealGroup] = {}
        for e in self.entries:
            key = e.meal_type if e.meal_type in MEAL_ORDER else _OTHER_MEAL
            group = groups.get(key)
            if group is None:
                group = groups[key] = MealGroup(meal_type=key)
            group.entries.append(e)
            group.totals.add(e)
        order = MEAL_ORDER + [_OTHER_MEAL]
        return [groups[k] for k in order if k in groups]


@dataclass
class PeriodView:
    start: date
    end: date
    days: list[DayView] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)


def _meal_type_by_hour(hour: int) -> str:
    if 5 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 16:
        return "lunch"
    if 16 <= hour < 22:
        return "dinner"
    return "snack"


class FoodService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.food_repo = FoodRepository(session)
        self.user_repo = UserRepository(session)
        self.shared_repo = SharedFoodRepository(session)
        self.llm = LLMService()

    # ── Таймзона / границы суток ────────────────────────────────────────────

    def _tz(self, user: User) -> ZoneInfo:
        try:
            return ZoneInfo(user.timezone or _DEFAULT_TZ)
        except Exception:
            return ZoneInfo(_DEFAULT_TZ)

    def _day_bounds_utc(self, user: User, day: date) -> tuple[datetime, datetime]:
        """Локальные [00:00, 24:00) указанного дня → наивные UTC-границы."""
        tz = self._tz(user)
        start_local = datetime.combine(day, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        to_utc = lambda dt: dt.astimezone(timezone.utc).replace(tzinfo=None)
        return to_utc(start_local), to_utc(end_local)

    def today(self, user: User) -> date:
        return datetime.now(self._tz(user)).date()

    # ── Анализ (LLM, без сохранения) ────────────────────────────────────────

    async def analyze_text(self, text: str) -> NutritionResult:
        return await self.llm.parse_text(text)

    async def analyze_photo(
        self, image_bytes: bytes, caption: str = "", mime_type: str = "image/jpeg"
    ) -> NutritionResult:
        return await self.llm.analyze_photo(image_bytes, caption, mime_type)

    async def analyze_dish_text(
        self, description: str, name: str, weight_g: float
    ) -> NutritionResult:
        """Текстовый состав блюда → суммарное КБЖУ (для создания SharedFood)."""
        return await self.llm.analyze_dish_text(description, name, weight_g)

    async def analyze_dish_photos(
        self, images_b64: list[str], name: str, weight_g: float
    ) -> NutritionResult:
        """Несколько фото блюда → суммарное КБЖУ (для создания SharedFood)."""
        return await self.llm.analyze_dish_photos(images_b64, name, weight_g)

    async def analyze_dish_combined(
        self, text: str, images_b64: list[str], name: str, weight_g: float
    ) -> NutritionResult:
        """Текст с граммовками + фото этикеток → суммарное КБЖУ."""
        return await self.llm.analyze_dish_combined(text, images_b64, name, weight_g)

    # ── Сохранение ──────────────────────────────────────────────────────────

    async def save_entries(
        self,
        user: User,
        items: list[NutritionItem],
        source: str,
        photo_file_id: str | None = None,
        meal_type: str | None = None,
        created_at: datetime | None = None,
        save_to_db: list[bool] | None = None,
    ) -> list[FoodEntry]:
        """Сохранить подтверждённые позиции. save_to_db[i]=True → также в SharedFood."""
        if meal_type is None:
            local_now = datetime.now(self._tz(user))
            meal_type = _meal_type_by_hour(local_now.hour)

        saved: list[FoodEntry] = []
        for i, item in enumerate(items):
            shared_food_id = None
            do_save = save_to_db and i < len(save_to_db) and save_to_db[i]
            if do_save and item.weight_g and item.weight_g > 0:
                sf = await self.save_shared_food(
                    name=item.description,
                    weight_g=item.weight_g,
                    kcal=item.kcal,
                    protein=item.protein,
                    fat=item.fat,
                    carbs=item.carbs,
                    user_id=user.id,
                )
                shared_food_id = sf.id
            entry = await self.food_repo.create(
                user_id=user.id,
                description=item.description,
                kcal=item.kcal,
                protein=item.protein,
                fat=item.fat,
                carbs=item.carbs,
                weight_g=item.weight_g,
                meal_type=meal_type,
                source=source,
                photo_file_id=photo_file_id,
                created_at=created_at,
                shared_food_id=shared_food_id,
            )
            saved.append(entry)
        await self.session.commit()
        return saved

    # ── Общая база блюд ─────────────────────────────────────────────────────────

    async def save_shared_food(
        self,
        *,
        name: str,
        weight_g: float,
        kcal: float,
        protein: float,
        fat: float,
        carbs: float,
        user_id: int,
    ) -> SharedFood:
        """Вычисляет значения на 100 г и сохраняет блюдо в общую базу."""
        factor = 100 / weight_g
        return await self.shared_repo.create(
            name=name,
            weight_g=weight_g,
            kcal_per_100g=round(kcal * factor, 2),
            protein_per_100g=round(protein * factor, 2),
            fat_per_100g=round(fat * factor, 2),
            carbs_per_100g=round(carbs * factor, 2),
            created_by=user_id,
        )

    async def search_shared_foods(self, query: str) -> list[SharedFood]:
        query = query.strip()
        if not query:
            return []
        return await self.shared_repo.search(query)

    async def get_quick_pick(self, user: User) -> list[SharedFood]:
        return await self.shared_repo.get_quick_pick(user.id)

    async def create_entry_from_shared(
        self,
        user: User,
        food_id: int,
        weight_g: float,
        meal_type: str | None = None,
        created_at: datetime | None = None,
    ) -> FoodEntry | None:
        """Создаёт FoodEntry из SharedFood с масштабированием КБЖУ под заданный вес."""
        food = await self.shared_repo.get_by_id(food_id)
        if not food:
            return None
        factor = weight_g / 100
        if meal_type is None:
            meal_type = _meal_type_by_hour(datetime.now(self._tz(user)).hour)
        entry = await self.food_repo.create(
            user_id=user.id,
            description=food.name,
            kcal=round(food.kcal_per_100g * factor, 1),
            protein=round(food.protein_per_100g * factor, 1),
            fat=round(food.fat_per_100g * factor, 1),
            carbs=round(food.carbs_per_100g * factor, 1),
            weight_g=weight_g,
            meal_type=meal_type,
            source="db",
            shared_food_id=food_id,
            created_at=created_at,
        )
        await self.shared_repo.increment_use_count(food_id)
        await self.session.commit()
        return entry

    async def add_explicit(
        self,
        user: User,
        description: str,
        kcal: float,
        protein: float,
        fat: float,
        carbs: float,
        weight_g: float | None = None,
        meal_type: str | None = None,
    ) -> FoodEntry:
        """Прямой ввод чисел пользователем, без LLM (fallback / точные данные с этикетки)."""
        item = NutritionItem(
            description=description, weight_g=weight_g,
            kcal=kcal, protein=protein, fat=fat, carbs=carbs,
        )
        saved = await self.save_entries(user, [item], source="manual", meal_type=meal_type)
        return saved[0]

    # ── Цели КБЖУ ─────────────────────────────────────────────────────────────

    def get_goals(self, user: User) -> Goals:
        return Goals.from_user(user)

    async def set_goals(
        self,
        user: User,
        kcal: float | None = None,
        protein: float | None = None,
        fat: float | None = None,
        carbs: float | None = None,
    ) -> Goals:
        """Задать/обновить дневные цели. None в поле = снять цель."""
        await self.user_repo.update_food_goals(user.id, kcal, protein, fat, carbs)
        await self.session.commit()
        return Goals.from_user(user)

    # ── Чтение / агрегация ──────────────────────────────────────────────────

    async def get_day(self, user: User, day: date | None = None) -> DayView:
        day = day or self.today(user)
        start, end = self._day_bounds_utc(user, day)
        entries = await self.food_repo.get_in_range(user.id, start, end)
        view = DayView(day=day, entries=entries, goals=Goals.from_user(user))
        for e in entries:
            view.totals.add(e)
        return view

    async def get_period(
        self, user: User, start_date: date, end_date: date
    ) -> PeriodView:
        """Записи за [start_date, end_date] включительно, сгруппированные по дням."""
        start_utc, _ = self._day_bounds_utc(user, start_date)
        _, end_utc = self._day_bounds_utc(user, end_date)
        entries = await self.food_repo.get_in_range(user.id, start_utc, end_utc)

        tz = self._tz(user)
        goals = Goals.from_user(user)
        by_day: dict[date, DayView] = {}
        period = PeriodView(start=start_date, end=end_date)
        for e in entries:
            # created_at наивный UTC → локальная дата пользователя
            local_dt = e.created_at.replace(tzinfo=timezone.utc).astimezone(tz)
            d = local_dt.date()
            day_view = by_day.get(d)
            if day_view is None:
                day_view = by_day[d] = DayView(day=d, goals=goals)
            day_view.entries.append(e)
            day_view.totals.add(e)
            period.totals.add(e)
        period.days = [by_day[d] for d in sorted(by_day)]
        return period

    # ── Редактирование / удаление (с проверкой владельца) ────────────────────

    async def _owned(self, user: User, entry_id: int) -> FoodEntry | None:
        entry = await self.food_repo.get_by_id(entry_id)
        if not entry or entry.user_id != user.id:
            return None
        return entry

    async def update_entry(self, user: User, entry_id: int, **fields) -> FoodEntry | None:
        if not await self._owned(user, entry_id):
            return None
        entry = await self.food_repo.update(entry_id, **fields)
        await self.session.commit()
        return entry

    async def delete_entry(self, user: User, entry_id: int) -> bool:
        if not await self._owned(user, entry_id):
            return False
        ok = await self.food_repo.delete(entry_id)
        await self.session.commit()
        return ok

    # ── Выгрузка за период (дамп для вставки в ChatGPT/Claude) ────────────────

    async def export_period(self, user: User, start_date: date, end_date: date) -> str:
        """Текстовый дамп: группировка по дням, итоги дня, общий итог, шаблон промпта."""
        period = await self.get_period(user, start_date, end_date)

        def fmt_totals(t: Totals) -> str:
            return (f"{round(t.kcal)} ккал · "
                    f"Б {round(t.protein)} / Ж {round(t.fat)} / У {round(t.carbs)}")

        lines = [f"# Дневник питания {start_date:%d.%m.%Y} — {end_date:%d.%m.%Y}", ""]
        if not period.days:
            lines.append("_Записей за период нет._")
            return "\n".join(lines)

        tz = self._tz(user)
        for day_view in period.days:
            lines.append(f"## {day_view.day:%d.%m.%Y}")
            for e in day_view.entries:
                t = e.created_at.replace(tzinfo=timezone.utc).astimezone(tz)
                weight = f", {round(e.weight_g)} г" if e.weight_g else ""
                lines.append(
                    f"- {t:%H:%M} {e.description}{weight} — "
                    f"{round(e.kcal)} ккал "
                    f"(Б {round(e.protein)} / Ж {round(e.fat)} / У {round(e.carbs)})"
                )
            lines.append(f"**Итог за день:** {fmt_totals(day_view.totals)}")
            lines.append("")

        lines.append(f"**Итог за период:** {fmt_totals(period.totals)}")
        lines.append("")
        lines.append("---")
        lines.append(
            "Промпт для анализа: «Проанализируй мой дневник питания выше. "
            "Оцени баланс БЖУ и калорийность, отметь перекосы и дай рекомендации.»"
        )
        return "\n".join(lines)
