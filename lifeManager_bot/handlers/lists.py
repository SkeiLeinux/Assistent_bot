"""
handlers/lists.py — хендлеры для работы со списками.

Изменения: добавлены уведомления через NotificationService.
Хендлер получает bot: Bot как параметр — aiogram 3 инжектирует его
автоматически, так же как session и user.
"""

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User
from services import ListService, NotificationService
from handlers.keyboards import (
    categories_keyboard,
    node_keyboard,
    confirm_keyboard,
    back_keyboard,
    numbers_keyboard,
    format_node_text,
)

router = Router()


class ListStates(StatesGroup):
    waiting_for_category_name = State()
    waiting_for_item_text = State()
    waiting_for_item_edit = State()


# ── Вспомогательные функции ───────────────────────────────────────────────────

async def _show_node(target, user: User, session: AsyncSession, category_id: int, prefix: str = ""):
    list_service = ListService(session)
    result = await list_service.get_node(user, category_id)

    if not result.success:
        if isinstance(target, CallbackQuery):
            await target.answer(result.message, show_alert=True)
        else:
            await target.answer(result.message)
        return

    text = (prefix + "\n\n" if prefix else "") + format_node_text(
        result.breadcrumb, result.children, result.items
    )
    keyboard = node_keyboard(result.category, result.children, result.items)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _show_root(target, user: User, session: AsyncSession, prefix: str = ""):
    list_service = ListService(session)
    result = await list_service.get_root_categories(user)

    if not result.success:
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(result.message)
        else:
            await target.answer(result.message)
        return

    text = (prefix + "\n\n" if prefix else "") + "📋 <b>Твои списки:</b>"
    keyboard = categories_keyboard(result.categories)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ── /lists ────────────────────────────────────────────────────────────────────

@router.message(Command("lists"))
async def cmd_lists(message: Message, user: User, session: AsyncSession):
    await _show_root(message, user, session)


@router.callback_query(F.data == "cat:list")
async def cb_root(callback: CallbackQuery, user: User, session: AsyncSession):
    await callback.answer()
    await _show_root(callback, user, session)


@router.callback_query(F.data.startswith("cat:open:"))
async def cb_node_open(callback: CallbackQuery, user: User, session: AsyncSession):
    await callback.answer()
    category_id = int(callback.data.split(":")[2])
    await _show_node(callback, user, session, category_id)


# ── Создание категории ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cat:new:"))
async def cb_category_new(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    raw = callback.data.split(":")[2]
    parent_id = None if raw == "root" else int(raw)
    await state.update_data(parent_id=parent_id)

    if parent_id:
        hint = "Введи название нового раздела внутри текущего.\n\nМожно с эмодзи: <code>🎬 Фэнтези</code>"
        back_cb = f"cat:open:{parent_id}"
    else:
        hint = "Введи название нового раздела.\n\nМожно с эмодзи: <code>🛒 Продукты</code>"
        back_cb = "cat:list"

    await callback.message.edit_text(hint, parse_mode="HTML", reply_markup=back_keyboard(back_cb))
    await state.set_state(ListStates.waiting_for_category_name)


@router.message(ListStates.waiting_for_category_name)
async def handle_category_name(
    message: Message, state: FSMContext, user: User, session: AsyncSession, bot: Bot
):
    text = message.text.strip()
    if not text or len(text) > 60:
        await message.answer("Название должно быть от 1 до 60 символов. Попробуй ещё раз:")
        return

    emoji, name = None, text
    if text and not text[0].isalnum() and not text[0].isspace():
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            emoji, name = parts[0], parts[1]

    data = await state.get_data()
    parent_id = data.get("parent_id")
    await state.clear()

    list_service = ListService(session)
    if parent_id:
        result = await list_service.create_subcategory(user, parent_id, name, emoji)
    else:
        result = await list_service.create_root_category(user, name, emoji)

    if not result.success:
        await message.answer(result.message)
        return

    # Уведомляем партнёра
    notif = NotificationService(bot, session)
    await notif.category_created(user, result.category)

    if parent_id:
        await _show_node(message, user, session, parent_id, prefix="✅ Раздел создан!")
    else:
        await _show_root(message, user, session, prefix="✅ Раздел создан!")


# ── Удаление категории ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cat:delete:"))
async def cb_category_delete(callback: CallbackQuery):
    await callback.answer()
    category_id = int(callback.data.split(":")[2])
    await callback.message.edit_text(
        "⚠️ Удалить этот раздел?\n\n"
        "<i>Все вложенные разделы и пункты тоже будут удалены.</i>",
        parse_mode="HTML",
        reply_markup=confirm_keyboard("cat_del", category_id),
    )


@router.callback_query(F.data.startswith("cat_del:confirm:"))
async def cb_category_delete_confirm(
    callback: CallbackQuery, user: User, session: AsyncSession, bot: Bot
):
    await callback.answer()
    category_id = int(callback.data.split(":")[2])

    from repositories import ListRepository
    cat = await ListRepository(session).get_category(category_id)
    parent_id = cat.parent_id if cat else None
    # Запоминаем название ДО удаления — после удаления объект недоступен
    cat_title = f"{cat.emoji} {cat.name}" if cat and cat.emoji else (cat.name if cat else "")

    list_service = ListService(session)
    result = await list_service.delete_category(user, category_id)

    if not result.success:
        await callback.message.edit_text(result.message)
        return

    # Уведомляем партнёра
    notif = NotificationService(bot, session)
    await notif.category_deleted(user, cat_title)

    if parent_id:
        await _show_node(callback, user, session, parent_id, prefix="✅ Удалено.")
    else:
        await _show_root(callback, user, session, prefix="✅ Удалено.")


@router.callback_query(F.data.startswith("cat_del:cancel:"))
async def cb_category_delete_cancel(
    callback: CallbackQuery, user: User, session: AsyncSession
):
    await callback.answer()
    category_id = int(callback.data.split(":")[2])
    await _show_node(callback, user, session, category_id)


# ── Добавление пункта ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("item:new:"))
async def cb_item_new(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    category_id = int(callback.data.split(":")[2])
    await state.update_data(category_id=category_id)
    await callback.message.edit_text(
        "Введи текст нового пункта:",
        reply_markup=back_keyboard(f"cat:open:{category_id}"),
    )
    await state.set_state(ListStates.waiting_for_item_text)


@router.message(ListStates.waiting_for_item_text)
async def handle_item_text(
    message: Message, state: FSMContext, user: User, session: AsyncSession, bot: Bot
):
    data = await state.get_data()
    category_id = data["category_id"]
    text = message.text.strip()

    if not text or len(text) > 500:
        await message.answer("Текст должен быть от 1 до 500 символов. Попробуй ещё раз:")
        return

    list_service = ListService(session)
    result = await list_service.add_item(user, category_id, text)
    await state.clear()

    if not result.success:
        await message.answer(result.message)
        return

    # Получаем категорию для уведомления
    from repositories import ListRepository
    category = await ListRepository(session).get_category(category_id)
    notif = NotificationService(bot, session)
    await notif.item_added(user, result.item, category)

    await _show_node(message, user, session, category_id, prefix="✅ Добавлено!")


# ── Toggle ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("item:toggle:"))
async def cb_item_toggle(
    callback: CallbackQuery, user: User, session: AsyncSession, bot: Bot
):
    await callback.answer()
    item_id = int(callback.data.split(":")[2])
    list_service = ListService(session)
    result = await list_service.toggle_item(user, item_id)

    if not result.success:
        return

    # Уведомляем — но только если это не сам пользователь в одиночку
    from repositories import ListRepository
    category = await ListRepository(session).get_category(result.item.category_id)
    notif = NotificationService(bot, session)
    await notif.item_checked(user, result.item, category)

    await _show_node(callback, user, session, result.item.category_id)


# ── Редактирование ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("item:edit_ask:"))
async def cb_item_edit_ask(callback: CallbackQuery, user: User, session: AsyncSession):
    await callback.answer()
    category_id = int(callback.data.split(":")[2])
    list_service = ListService(session)
    result = await list_service.get_node(user, category_id)

    if not result.success or not result.items:
        return

    text = format_node_text(result.breadcrumb, result.children, result.items)
    await callback.message.edit_text(
        text + "\n\n<i>Выбери номер пункта для редактирования:</i>",
        reply_markup=numbers_keyboard(result.items, "edit", category_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("item:edit_pick:"))
async def cb_item_edit_pick(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    item_id = int(callback.data.split(":")[2])
    # Сохраняем старый текст в FSM — понадобится для уведомления
    from repositories import ListRepository
    item = await ListRepository(session).get_item(item_id)
    await state.update_data(item_id=item_id, old_text=item.text if item else "")
    await callback.message.edit_text("Введи новый текст для пункта:")
    await state.set_state(ListStates.waiting_for_item_edit)


@router.message(ListStates.waiting_for_item_edit)
async def handle_item_edit(
    message: Message, state: FSMContext, user: User, session: AsyncSession, bot: Bot
):
    data = await state.get_data()
    item_id = data["item_id"]
    old_text = data.get("old_text", "")
    new_text = message.text.strip()

    if not new_text or len(new_text) > 500:
        await message.answer("Текст должен быть от 1 до 500 символов. Попробуй ещё раз:")
        return

    list_service = ListService(session)
    result = await list_service.edit_item(user, item_id, new_text)
    await state.clear()

    if not result.success:
        await message.answer(result.message)
        return

    from repositories import ListRepository
    category = await ListRepository(session).get_category(result.item.category_id)
    notif = NotificationService(bot, session)
    await notif.item_edited(user, result.item, category, old_text)

    await _show_node(message, user, session, result.item.category_id, prefix="✅ Изменено!")


# ── Удаление пункта ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("item:delete_ask:"))
async def cb_item_delete_ask(callback: CallbackQuery, user: User, session: AsyncSession):
    await callback.answer()
    category_id = int(callback.data.split(":")[2])
    list_service = ListService(session)
    result = await list_service.get_node(user, category_id)

    if not result.success or not result.items:
        return

    text = format_node_text(result.breadcrumb, result.children, result.items)
    await callback.message.edit_text(
        text + "\n\n<i>Выбери номер пункта для удаления:</i>",
        reply_markup=numbers_keyboard(result.items, "delete", category_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("item:delete_pick:"))
async def cb_item_delete_pick(
    callback: CallbackQuery, user: User, session: AsyncSession, bot: Bot
):
    await callback.answer("Удалено")
    item_id = int(callback.data.split(":")[2])

    from repositories import ListRepository
    list_repo = ListRepository(session)
    item = await list_repo.get_item(item_id)
    if not item:
        return
    category_id = item.category_id
    item_text = item.text  # запоминаем до удаления
    category = await list_repo.get_category(category_id)

    list_service = ListService(session)
    await list_service.delete_item(user, item_id)

    notif = NotificationService(bot, session)
    await notif.item_deleted(user, item_text, category)

    await _show_node(callback, user, session, category_id)


# ── Очистка отмеченных ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("item:clear:"))
async def cb_items_clear(callback: CallbackQuery, user: User, session: AsyncSession):
    await callback.answer()
    category_id = int(callback.data.split(":")[2])
    list_service = ListService(session)
    result = await list_service.clear_checked(user, category_id)

    if not result.success:
        await callback.answer(result.message, show_alert=True)
        return

    await _show_node(callback, user, session, category_id, prefix=f"🧹 {result.message}")