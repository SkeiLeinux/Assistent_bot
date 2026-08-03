"""resolve/ports.py — DTO и контракты резолвера КБЖУ (spec-nutrition-retrieval §5).

DTO (pydantic):
  - Macros            — КБЖУ на 100 г (единица обмена всех ступеней).
  - ExtractedItem     — одна позиция, как её вернул извлекатель (ТЗ контракта §3.1):
                        идентификация + per100g + `readout` (откуда числа) + note.
  - ExtractResult     — обёртка-объект (JSON-режим требует объект сверху).
  - ResolveQuery      — вход ступени-источника (что искать + fallback per100g).
  - ResolvedNutrition — выход ступени: per100g + провенанс (source/source_ref/...).

Контракты (Protocol):
  - NutritionSource   — одна ступень лестницы: `lookup(q) -> ResolvedNutrition|None`.
  - ProductResolver   — прогон лестницы на ОДНУ позицию: `refine(item) -> ResolvedNutrition|None`.

Дельта волны 1 (ТЗ контракта §3): булев `label_complete` заменён трёхзначным
`readout`. Причина — булево поле не различало два разных состояния: «этикетки нет,
дал типовые числа» и «этикетка есть, но нечитаема». Первое нормально, второе —
сигнал пользователю переснять. Заодно `per100g` стал nullable: модель получила
право отказаться от выдумывания чисел (ADR-0017 §17), а не обязана заполнять поле
любой ценой. В БД `readout` не персистится — это транспорт analyze→confirm.
"""

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator, model_validator

ProvenanceSource = Literal["label", "off", "web", "nutritionix", "estimate"]

# Откуда числа позиции. Отвечает МОДЕЛЬ, поэтому формулировка в промпте (§4.1)
# и это перечисление обязаны совпадать по смыслу один в один.
Readout = Literal["label", "estimate", "unreadable"]

# Физическая константа перевода: 1 ккал = 4.184 кДж. Здесь, а не в checks.py,
# потому что нужна обеим сторонам (ports заполняет ккал из кДж, checks сверяет),
# а checks импортирует ports — обратное направление дало бы цикл.
KJ_PER_KCAL = 4.184

# Текст пометки о пересчёте. Константа, а не литерал: по нему тест отличает
# «ккал посчитаны кодом» от «ккал прочитаны с этикетки».
KJ_NOTE = "пересчёт из кДж"


class Macros(BaseModel):
    """КБЖУ на 100 г. Все числа неотрицательны."""

    kcal: float
    protein: float
    fat: float
    carbs: float

    @field_validator("kcal", "protein", "fat", "carbs")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        return max(0.0, float(v))


class ExtractedItem(BaseModel):
    """Одна позиция из ввода (1 LLM-вызов сегментирует ввод на список таких, §3.1)."""

    name: str                       # родовое название без бренда («молоко питьевое»)
    brand: str | None = None        # «Домик в деревне»
    fat_pct: float | None = None    # 3.2 — дискриминатор молочки
    portion_g: float | None = None  # масса именно этой порции; None если не указана
    barcode: str | None = None      # EAN; проверяется контрольной суммой (C4.0)
    # None ТОЛЬКО при readout="unreadable" (инвариант ниже). Во всех остальных
    # случаях — числа с этикетки или типовая оценка модели.
    per100g: Macros | None = None
    # Килоджоули «как напечатано» — транспортное поле, в БД не персистится и в
    # `Macros` не входит (ТЗ v0.5 §3.1). Заведено после полевого дефекта F-01
    # (`05_implementation/field-defect-2026-08-02-kj-leak.md`): пока энергия была
    # ОДНИМ полем, модель сама решала, что в него положить, — при двух
    # напечатанных величинах выбирала, при одной делила в уме. И то и другое —
    # арифметика в модели, запрещённая L2 для всего остального. В проде это
    # выстрелило в первый же день: 450 кДж уехали в ккал, жиры были подогнаны
    # под них, пользователь получил завышение в 4,1 раза с провенансом `label`.
    # Теперь модель транскрибирует обе величины, а сверяет их код (C3a, §6.3a).
    energy_kj_100g: float | None = None
    readout: Readout = "estimate"
    note: str | None = None         # причина estimate/unreadable, факт пересчёта из кДж
    confidence: float = 0.0
    # Флаги проверок §6. Заполняет КОД (extractor после run_checks), не модель:
    # в промпте поля нет, поэтому нормальный ответ его не содержит. Транспорт
    # до экрана подтверждения, в БД не персистится.
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_label_complete(cls, data):
        """Совместимость с промптом v1 (`label_complete: bool`).

        Нужна ровно одному потребителю — раннеру eval, который обязан прогнать обе
        версии промпта, иначе «не хуже baseline» недоказуемо (ТЗ §12.3). Прод-промпт
        v2 поля не просит, поэтому в проде ветка не срабатывает. Удаляется вместе
        с `SYSTEM_PROMPT_V1`, когда сравнение версий перестанет быть нужным.
        """
        if isinstance(data, dict) and "label_complete" in data:
            data = dict(data)
            legacy = data.pop("label_complete")
            data.setdefault("readout", "label" if legacy else "estimate")
        return data

    @model_validator(mode="before")
    @classmethod
    def _kcal_from_kj(cls, data):
        """C3a, ветка «напечатаны только кДж»: ккал считает КОД (ТЗ §6.3a).

        Промпт v3 велит модели ставить `null` там, где величина не напечатана, и
        прямо запрещает делить кДж на 4.184 самой — именно это деление «в уме»
        и утекло в F-01. Пересчёт делается здесь, ДО валидации схемы, по одной
        причине: `Macros.kcal` обязателен, и оставить его nullable значило бы
        протащить `None` через агрегацию, цели, дайджест и фронт ради случая,
        который закрывается одним делением.

        Это не автокоррекция, запрещённая §2.4: код не «чинит» подозрительное
        число, а переводит единицы у ОТСУТСТВУЮЩЕГО. Подозрительные числа по-
        прежнему только помечаются (C3a в `checks.py`).
        """
        if not isinstance(data, dict):
            return data
        kj = data.get("energy_kj_100g")
        if kj is None:
            return data
        per100g = data.get("per100g")
        if not isinstance(per100g, dict) or per100g.get("kcal") is not None:
            return data
        try:
            kcal = round(float(kj) / KJ_PER_KCAL, 1)
        except (TypeError, ValueError):
            return data
        data = dict(data)
        data["per100g"] = {**per100g, "kcal": kcal}
        note = (data.get("note") or "").strip()
        data["note"] = f"{note}; {KJ_NOTE}" if note else KJ_NOTE
        return data

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name пустой")
        return v

    @model_validator(mode="after")
    def _readout_consistency(self):
        """ТЗ §3.1: отказ обязан быть объяснён, а не-отказ — обязан нести числа.

        Без первой половины `unreadable` становится бесплатным способом ничего не
        сказать; без второй — `label`/`estimate` без чисел молча ломает лестницу
        (EstimateSource нечего вернуть).

        **Текст ошибки уходит в лог целиком** (`extractor._schema_errors`, ТЗ §9):
        у инвариантов уровня модели `loc` вырождается в «items.0», и без текста
        непонятно, какое правило нарушено. Поэтому интерполировать сюда свободные
        поля позиции (`name`/`note`/`brand`) нельзя — это дневник питания живых
        людей. Закрытые словари вроде `readout` можно. Пин —
        `tests/test_llm_observability.py::test_ports_validator_messages_carry_no_model_data`.
        """
        if self.readout == "unreadable":
            if not (self.note or "").strip():
                raise ValueError("unreadable требует note")
        elif self.per100g is None:
            raise ValueError(f"readout={self.readout} требует per100g")
        return self

    def to_query(self) -> "ResolveQuery":
        """Запрос для ступени-источника. fallback_per100g = текущая оценка (estimate)."""
        return ResolveQuery(
            name=self.name,
            brand=self.brand,
            fat_pct=self.fat_pct,
            barcode=self.barcode,
            fallback_per100g=self.per100g,
        )


class ExtractResult(BaseModel):
    """Обёртка-объект для строгого JSON-режима модели (массив на верхнем уровне нельзя)."""

    items: list[ExtractedItem]


class ResolveQuery(BaseModel):
    """Вход ступени-источника."""

    name: str
    brand: str | None = None
    fat_pct: float | None = None
    barcode: str | None = None
    # None при readout="unreadable": чисел нет вовсе, и EstimateSource в этом
    # случае ответить не может (§6.6) — позиция уходит в `unresolved`.
    fallback_per100g: Macros | None = None


class ResolvedNutrition(BaseModel):
    """Выход ступени: КБЖУ на 100 г + провенанс (показываем пользователю)."""

    per100g: Macros
    source: ProvenanceSource
    source_ref: str | None = None   # URL/код товара/цитата (off/web)
    matched_name: str | None = None # как назван матч в источнике
    confidence: float = 0.0
    # Уверенность МАТЧА (не полноты карточки) — гейт сверки label-preferred
    # (ТЗ контракта §7.1a). Единственное возможное значение — "exact": товар
    # найден точным lookup'ом по штрихкоду, прошедшему контрольную сумму EAN.
    # Значение "confirmed" (карточка выбрана LLM-переранжированием) упразднено
    # 2026-08-03 вместе с поисковой веткой — порождать его нечем (ADR-0019,
    # ревизия 2026-08-03). `None` = матча нет, сверке верить нельзя.
    match_confidence: str | None = None
    # Флаги проверок §6 + маршрута (§7.2). Транспорт до confirm-экрана, не персистится.
    warnings: list[str] = Field(default_factory=list)
    # Вторая версия чисел, когда этикетка разошлась с источником >15% (§7.2).
    # Пользователь выбирает сам — код не подменяет числа (принцип §2.4).
    alternative: "ResolvedNutrition | None" = None


@runtime_checkable
class NutritionSource(Protocol):
    """Ступень лестницы — заменяемый адаптер (ADR-0002). Добавить источник = новый
    адаптер за этим контрактом, лестницу не трогаем (так R2-web встанет без правок)."""

    name: str
    timeout_s: float

    async def lookup(self, q: ResolveQuery) -> ResolvedNutrition | None: ...


@runtime_checkable
class ProductResolver(Protocol):
    """Прогон лестницы на ОДНУ позицию. `allow_network=False` — бюджет ввода вышел:
    сразу estimate, без сети (§3).

    `None` = позиция не разрешена (§6.6): чисел нет ни у модели, ни у источников.
    Раньше метод всегда возвращал числа, потому что `per100g` был обязателен —
    то есть отказ был невыразим и модель была вынуждена выдумывать.
    """

    async def refine(
        self, item: ExtractedItem, *, allow_network: bool = True
    ) -> ResolvedNutrition | None: ...
