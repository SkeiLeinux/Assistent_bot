"""
repositories/list_repository.py — CRUD для категорий и пунктов списков.

Изменения для иерархии:
  - get_root_categories() — только корневые (parent_id IS NULL)
  - get_children() — дочерние категории конкретного узла
  - get_breadcrumb() — путь от корня до текущего узла (для заголовка)
  - create_category() теперь принимает parent_id
"""

from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import ListCategory, ListItem


class ListRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Категории ──────────────────────────────────────────────────────────

    async def get_root_categories(self, family_id: int) -> list[ListCategory]:
        """Только корневые категории (parent_id IS NULL) — главное меню."""
        result = await self.session.execute(
            select(ListCategory)
            .where(
                ListCategory.family_id == family_id,
                ListCategory.parent_id.is_(None),
            )
            .order_by(ListCategory.name)
        )
        return list(result.scalars().all())

    async def get_children(self, parent_id: int) -> list[ListCategory]:
        """Дочерние категории конкретного узла, отсортированные по имени."""
        result = await self.session.execute(
            select(ListCategory)
            .where(ListCategory.parent_id == parent_id)
            .order_by(ListCategory.name)
        )
        return list(result.scalars().all())

    async def get_category(self, category_id: int) -> ListCategory | None:
        """Категория с предзагрузкой дочерних категорий и пунктов."""
        result = await self.session.execute(
            select(ListCategory)
            .where(ListCategory.id == category_id)
            .options(
                selectinload(ListCategory.children),
                selectinload(ListCategory.items),
            )
        )
        return result.scalar_one_or_none()

    async def get_breadcrumb(self, category_id: int) -> list[ListCategory]:
        """
        Строит путь от корня до текущего узла.
        Используется для заголовка: Фильмы › Фантастика › Фэнтези

        Идём вверх по цепочке parent_id пока не дойдём до корня.
        Максимальная глубина защищает от циклических ссылок (на всякий случай).
        """
        path = []
        current_id = category_id
        max_depth = 10  # защита от бесконечного цикла

        for _ in range(max_depth):
            cat = await self.session.get(ListCategory, current_id)
            if not cat:
                break
            path.insert(0, cat)  # вставляем в начало — идём снизу вверх
            if cat.parent_id is None:
                break
            current_id = cat.parent_id

        return path

    async def create_category(
        self,
        family_id: int,
        created_by: int,
        name: str,
        emoji: str | None = None,
        parent_id: int | None = None,
    ) -> ListCategory:
        category = ListCategory(
            family_id=family_id,
            created_by=created_by,
            name=name,
            emoji=emoji,
            parent_id=parent_id,
        )
        self.session.add(category)
        await self.session.flush()
        return category

    async def delete_category(self, category_id: int) -> bool:
        """
        Удалить категорию. Благодаря ondelete='CASCADE' в модели
        все дочерние категории и пункты удалятся автоматически.
        """
        category = await self.session.get(ListCategory, category_id)
        if not category:
            return False
        await self.session.delete(category)
        await self.session.flush()
        return True

    # ── Пункты списка ──────────────────────────────────────────────────────

    async def get_items(self, category_id: int) -> list[ListItem]:
        result = await self.session.execute(
            select(ListItem)
            .where(ListItem.category_id == category_id)
            .order_by(ListItem.position)
        )
        return list(result.scalars().all())

    async def get_item(self, item_id: int) -> ListItem | None:
        return await self.session.get(ListItem, item_id)

    async def create_item(
        self,
        category_id: int,
        added_by: int,
        text: str,
    ) -> ListItem:
        max_pos_result = await self.session.execute(
            select(func.max(ListItem.position))
            .where(ListItem.category_id == category_id)
        )
        max_pos = max_pos_result.scalar() or 0

        item = ListItem(
            category_id=category_id,
            added_by=added_by,
            text=text,
            position=max_pos + 1,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def update_item_text(self, item_id: int, new_text: str) -> ListItem | None:
        item = await self.get_item(item_id)
        if item:
            item.text = new_text
            await self.session.flush()
        return item

    async def toggle_item(self, item_id: int) -> ListItem | None:
        item = await self.get_item(item_id)
        if item:
            item.is_checked = not item.is_checked
            await self.session.flush()
        return item

    async def delete_item(self, item_id: int) -> bool:
        item = await self.get_item(item_id)
        if not item:
            return False
        await self.session.delete(item)
        await self.session.flush()
        return True

    async def clear_checked_items(self, category_id: int) -> int:
        result = await self.session.execute(
            delete(ListItem)
            .where(
                ListItem.category_id == category_id,
                ListItem.is_checked == True,  # noqa: E712
            )
            .returning(ListItem.id)
        )
        return len(result.fetchall())
