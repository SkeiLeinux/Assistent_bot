"""
handlers/keyboards.py — все инлайн-клавиатуры бота.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from db.models import ListCategory, ListItem


def categories_keyboard(categories: list[ListCategory]) -> InlineKeyboardMarkup:
    """Главное меню — корневые категории."""
    buttons = []
    for cat in categories:
        title = f"{cat.emoji} {cat.name}" if cat.emoji else cat.name
        buttons.append([
            InlineKeyboardButton(text=title, callback_data=f"cat:open:{cat.id}")
        ])
    buttons.append([
        InlineKeyboardButton(text="＋ Новый раздел", callback_data="cat:new:root"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_node_text(
    breadcrumb: list[ListCategory],
    children: list[ListCategory],
    items: list[ListItem],
) -> str:
    """
    Формирует текст сообщения для одного узла дерева.

    Заголовок — хлебные крошки: 🎬 Фильмы › Фантастика › Фэнтези
    Тело — сначала дочерние папки 📁, потом пронумерованные пункты.
    """
    # Заголовок с breadcrumb
    parts = []
    for cat in breadcrumb:
        parts.append(f"{cat.emoji} {cat.name}" if cat.emoji else cat.name)
    header = " › ".join(parts)

    lines = [f"<b>{header}</b>"]

    # Счётчики
    info_parts = []
    if children:
        info_parts.append(f"📁 {len(children)}")
    if items:
        checked = sum(1 for i in items if i.is_checked)
        info_parts.append(f"☐ {len(items)}" + (f"  ✅ {checked}" if checked else ""))
    if info_parts:
        lines.append(f"<i>{' · '.join(info_parts)}</i>")

    lines.append("")  # пустая строка-разделитель

    # Дочерние разделы
    for child in children:
        title = f"{child.emoji} {child.name}" if child.emoji else child.name
        lines.append(f"📁  {title}")

    # Пункты списка
    for i, item in enumerate(items, 1):
        icon = "✅" if item.is_checked else "☐"
        lines.append(f"{icon}  <b>{i}.</b>  {item.text}")

    if not children and not items:
        lines.append("<i>Пусто. Добавь раздел или пункт.</i>")

    return "\n".join(lines)


def node_keyboard(
    category: ListCategory,
    children: list[ListCategory],
    items: list[ListItem],
) -> InlineKeyboardMarkup:
    """
    Клавиатура узла дерева.

    Ряд 1: кнопки-папки для перехода в дочерние разделы
    Ряд 2: числа для toggle пунктов (по 5 в ряд)
    Ряд 3: + Добавить пункт | + Добавить раздел
    Ряд 4: ✏️ Редактировать | 🗑 Удалить пункт  (если есть пункты)
    Ряд 5: 🧹 Очистить отмеченные              (если есть отмеченные)
    Ряд 6: 🗑 Удалить раздел | ‹ Назад
    """
    buttons = []

    # Кнопки дочерних разделов
    for child in children:
        title = f"{child.emoji} {child.name}" if child.emoji else child.name
        buttons.append([
            InlineKeyboardButton(
                text=f"📁 {title}",
                callback_data=f"cat:open:{child.id}",
            )
        ])

    # Числа для toggle пунктов
    if items:
        row = []
        for i, item in enumerate(items, 1):
            label = f"✅{i}" if item.is_checked else str(i)
            row.append(InlineKeyboardButton(
                text=label,
                callback_data=f"item:toggle:{item.id}",
            ))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    # Добавить пункт / добавить раздел
    buttons.append([
        InlineKeyboardButton(text="＋ Пункт", callback_data=f"item:new:{category.id}"),
        InlineKeyboardButton(text="＋ Раздел", callback_data=f"cat:new:{category.id}"),
    ])

    # Редактировать / удалить пункт
    if items:
        buttons.append([
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"item:edit_ask:{category.id}"),
            InlineKeyboardButton(text="🗑 Удалить пункт", callback_data=f"item:delete_ask:{category.id}"),
        ])
        if any(i.is_checked for i in items):
            buttons.append([
                InlineKeyboardButton(
                    text="🧹 Очистить отмеченные",
                    callback_data=f"item:clear:{category.id}",
                )
            ])

    # Назад и удалить раздел
    parent = category.parent_id
    back_cb = f"cat:open:{parent}" if parent else "cat:list"
    buttons.append([
        InlineKeyboardButton(text="🗑 Удалить раздел", callback_data=f"cat:delete:{category.id}"),
        InlineKeyboardButton(text="‹ Назад", callback_data=back_cb),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_keyboard(action: str, target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=f"{action}:confirm:{target_id}"),
        InlineKeyboardButton(text="✗ Отмена", callback_data=f"{action}:cancel:{target_id}"),
    ]])


def back_keyboard(callback_data: str, text: str = "‹ Назад") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, callback_data=callback_data)
    ]])


def numbers_keyboard(
    items: list[ListItem], action: str, category_id: int
) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for i, item in enumerate(items, 1):
        row.append(InlineKeyboardButton(
            text=str(i),
            callback_data=f"item:{action}_pick:{item.id}",
        ))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="✗ Отмена", callback_data=f"cat:open:{category_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
