"""
services/list_service.py — бизнес-логика для списков с поддержкой иерархии.

Ключевые изменения:
  - get_categories() → get_root_categories() для главного меню
  - get_node() — открыть конкретный узел (подкатегории + пункты + breadcrumb)
  - create_subcategory() — создать вложенную категорию
  - Проверка доступа теперь идёт через family_id узла
"""

from dataclasses import dataclass, field
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import ListCategory, ListItem, User
from repositories import ListRepository


@dataclass
class ListServiceResult:
    success: bool
    message: str = ""
    category: ListCategory | None = None
    item: ListItem | None = None
    items: list[ListItem] = field(default_factory=list)
    categories: list[ListCategory] = field(default_factory=list)
    children: list[ListCategory] = field(default_factory=list)
    breadcrumb: list[ListCategory] = field(default_factory=list)


class ListService:
    def __init__(self, session: AsyncSession):
        self.list_repo = ListRepository(session)
        self.session = session

    async def _check_access(
        self, category_id: int, family_id: int
    ) -> ListCategory | None:
        """Проверяет что категория принадлежит семье пользователя."""
        cat = await self.list_repo.get_category(category_id)
        if not cat or cat.family_id != family_id:
            return None
        return cat

    # ── Корневые категории (главное меню) ──────────────────────────────────

    async def get_root_categories(self, user: User) -> ListServiceResult:
        if not user.family_id:
            return ListServiceResult(
                success=False,
                message="Сначала создай группу или вступи в неё (/family).",
            )
        categories = await self.list_repo.get_root_categories(user.family_id)
        return ListServiceResult(success=True, categories=categories)

    # ── Открыть узел (подкатегории + пункты + хлебные крошки) ─────────────

    async def get_node(self, user: User, category_id: int) -> ListServiceResult:
        """
        Загружает всё необходимое для отображения одного узла дерева:
          - breadcrumb: путь от корня (для заголовка)
          - children: дочерние категории (показываем папками 📁)
          - items: пункты этого узла (показываем списком)
        """
        if not user.family_id:
            return ListServiceResult(success=False, message="Нет доступа.")

        category = await self._check_access(category_id, user.family_id)
        if not category:
            return ListServiceResult(success=False, message="Категория не найдена.")

        children = await self.list_repo.get_children(category_id)
        items = await self.list_repo.get_items(category_id)
        breadcrumb = await self.list_repo.get_breadcrumb(category_id)

        return ListServiceResult(
            success=True,
            category=category,
            children=children,
            items=items,
            breadcrumb=breadcrumb,
        )

    # ── Создание категорий ─────────────────────────────────────────────────

    async def create_root_category(
        self, user: User, name: str, emoji: str | None = None
    ) -> ListServiceResult:
        """Создать корневую категорию (parent_id = NULL)."""
        if not user.family_id:
            return ListServiceResult(
                success=False,
                message="Сначала создай группу или вступи в неё (/family).",
            )
        if emoji:
            emoji = emoji.strip()[:2]

        category = await self.list_repo.create_category(
            family_id=user.family_id,
            created_by=user.id,
            name=name.strip(),
            emoji=emoji,
            parent_id=None,
        )
        await self.session.commit()
        return ListServiceResult(success=True, category=category)

    async def create_subcategory(
        self, user: User, parent_id: int, name: str, emoji: str | None = None
    ) -> ListServiceResult:
        """Создать вложенную категорию внутри существующей."""
        if not user.family_id:
            return ListServiceResult(success=False, message="Нет доступа.")

        parent = await self._check_access(parent_id, user.family_id)
        if not parent:
            return ListServiceResult(success=False, message="Родительская категория не найдена.")

        if emoji:
            emoji = emoji.strip()[:2]

        category = await self.list_repo.create_category(
            family_id=user.family_id,
            created_by=user.id,
            name=name.strip(),
            emoji=emoji,
            parent_id=parent_id,
        )
        await self.session.commit()
        return ListServiceResult(success=True, category=category)

    async def delete_category(
        self, user: User, category_id: int
    ) -> ListServiceResult:
        """Удалить категорию и все её содержимое (дочерние + пункты)."""
        if not user.family_id:
            return ListServiceResult(success=False, message="Нет доступа.")

        category = await self._check_access(category_id, user.family_id)
        if not category:
            return ListServiceResult(success=False, message="Категория не найдена.")

        parent_id = category.parent_id  # запомним до удаления — вернёмся туда
        await self.list_repo.delete_category(category_id)
        await self.session.commit()

        return ListServiceResult(
            success=True,
            message="Удалено.",
            # передаём parent_id чтобы хендлер знал куда вернуться
            category=ListCategory(id=parent_id) if parent_id else None,
        )

    # ── Пункты ────────────────────────────────────────────────────────────

    async def add_item(
        self, user: User, category_id: int, text: str
    ) -> ListServiceResult:
        if not user.family_id:
            return ListServiceResult(success=False, message="Нет доступа.")

        if not await self._check_access(category_id, user.family_id):
            return ListServiceResult(success=False, message="Категория не найдена.")

        item = await self.list_repo.create_item(
            category_id=category_id,
            added_by=user.id,
            text=text.strip(),
        )
        await self.session.commit()
        return ListServiceResult(success=True, item=item)

    async def edit_item(
        self, user: User, item_id: int, new_text: str
    ) -> ListServiceResult:
        if not user.family_id:
            return ListServiceResult(success=False, message="Нет доступа.")

        item = await self.list_repo.get_item(item_id)
        if not item:
            return ListServiceResult(success=False, message="Пункт не найден.")

        if not await self._check_access(item.category_id, user.family_id):
            return ListServiceResult(success=False, message="Нет доступа.")

        item = await self.list_repo.update_item_text(item_id, new_text.strip())
        await self.session.commit()
        return ListServiceResult(success=True, item=item)

    async def toggle_item(
        self, user: User, item_id: int
    ) -> ListServiceResult:
        if not user.family_id:
            return ListServiceResult(success=False, message="Нет доступа.")

        item = await self.list_repo.get_item(item_id)
        if not item:
            return ListServiceResult(success=False, message="Пункт не найден.")

        if not await self._check_access(item.category_id, user.family_id):
            return ListServiceResult(success=False, message="Нет доступа.")

        item = await self.list_repo.toggle_item(item_id)
        await self.session.commit()
        return ListServiceResult(success=True, item=item)

    async def delete_item(
        self, user: User, item_id: int
    ) -> ListServiceResult:
        if not user.family_id:
            return ListServiceResult(success=False, message="Нет доступа.")

        item = await self.list_repo.get_item(item_id)
        if not item:
            return ListServiceResult(success=False, message="Пункт не найден.")

        category_id = item.category_id
        if not await self._check_access(category_id, user.family_id):
            return ListServiceResult(success=False, message="Нет доступа.")

        await self.list_repo.delete_item(item_id)
        await self.session.commit()
        return ListServiceResult(success=True)

    async def clear_checked(
        self, user: User, category_id: int
    ) -> ListServiceResult:
        if not user.family_id:
            return ListServiceResult(success=False, message="Нет доступа.")

        if not await self._check_access(category_id, user.family_id):
            return ListServiceResult(success=False, message="Категория не найдена.")

        count = await self.list_repo.clear_checked_items(category_id)
        await self.session.commit()
        return ListServiceResult(success=True, message=f"Удалено отмеченных: {count}")
