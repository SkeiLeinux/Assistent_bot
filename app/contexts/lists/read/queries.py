"""Read-проекции Lists для аналитики (ТЗ §12, A5/A6).

Единственная read-граница контекста: Analytics (S3) берёт данные ТОЛЬКО отсюда
(import-linter запрещает прямой импорт models/repositories/services из analytics).
Все проекции visibility-фильтрованы (kernel-клаузы). Группировка по периоду — в
Python (объём крошечный, req §9), чтобы не зависеть от DB-специфики дат (ADR-0005).
"""

from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel.sharing import Viewer, not_hidden, scope_visible

from app.contexts.lists.models import ItemStatus, List, ListItem, ListType


async def analytics_by_type(
    session: AsyncSession,
    viewer: Viewer,
    list_type: ListType,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Сводка по типу: всего видимых элементов, DONE за период, разбивка по дням."""
    vis = and_(
        scope_visible(List, viewer),
        not_hidden(ListItem, viewer),
        List.type == list_type,
    )

    total = await session.scalar(
        select(func.count())
        .select_from(ListItem)
        .join(List, ListItem.list_id == List.id)
        .where(vis)
    )

    done_stmt = (
        select(ListItem.completed_at)
        .join(List, ListItem.list_id == List.id)
        .where(vis, ListItem.status == ItemStatus.DONE, ListItem.completed_at.is_not(None))
    )
    timestamps = (await session.execute(done_stmt)).scalars().all()

    buckets: dict[str, int] = {}
    done = 0
    for ts in timestamps:
        day = ts.date()
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        done += 1
        key = day.isoformat()
        buckets[key] = buckets.get(key, 0) + 1

    return {
        "type": list_type.value,
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "total": total or 0,
        "done": done,
        "by_period": [{"period": k, "done": buckets[k]} for k in sorted(buckets)],
    }


async def recent_changes(
    session: AsyncSession,
    viewer: Viewer,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """Recent-changes по типам за [date_from, date_to] (Q-an-1 / CH-5).

    Источник изменений — поля `ListItem` (`created_at`/`completed_at`), не history.
    На каждый `ListType` с ненулевой активностью в периоде:
      added = видимые элементы с created_at.date() ∈ [date_from, date_to];
      done  = видимые DONE-элементы с completed_at.date() ∈ [date_from, date_to].
    Visibility — те же kernel-клаузы, что в analytics_by_type (скрытые/невидимые
    исключены). `type` — строка (.value): потребитель (Analytics) не импортирует
    enum ListType (прямой импорт lists.models из analytics запрещён линтером).
    """
    vis = and_(
        scope_visible(List, viewer),
        not_hidden(ListItem, viewer),
    )

    stmt = (
        select(List.type, ListItem.created_at, ListItem.status, ListItem.completed_at)
        .join(List, ListItem.list_id == List.id)
        .where(vis)
    )
    rows = (await session.execute(stmt)).all()

    added: dict[ListType, int] = {}
    done: dict[ListType, int] = {}
    for list_type, created_at, status, completed_at in rows:
        if created_at is not None and date_from <= created_at.date() <= date_to:
            added[list_type] = added.get(list_type, 0) + 1
        if (
            status == ItemStatus.DONE
            and completed_at is not None
            and date_from <= completed_at.date() <= date_to
        ):
            done[list_type] = done.get(list_type, 0) + 1

    active = added.keys() | done.keys()
    return [
        {"type": t.value, "added": added.get(t, 0), "done": done.get(t, 0)}
        for t in sorted(active, key=lambda lt: lt.value)
    ]
