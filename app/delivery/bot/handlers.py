"""Telegram-бот kernel: вход и пейринг (ТЗ §6).

Mini App — primary-канал (REST /api/v1); бот сохраняет вход /start и команды
пейринга. Срез 1 — текстовые команды (минимальный UI; богатый UI — в Mini App).
"""

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from app.config import settings
from app.kernel.services import IdentityService, PairingService
from app.kernel.services.errors import KernelError

from app.analytics import seed_default_schedules

logger = logging.getLogger(__name__)
router = Router()


def _app_keyboard() -> InlineKeyboardMarkup | None:
    """Кнопка запуска Mini App ассистента. None, если webapp_url не задан (волна C)."""
    if not settings.webapp_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🏠 Открыть ассистент",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        ]]
    )


@router.message(CommandStart())
async def cmd_start(message: Message, user, session) -> None:
    fam = await IdentityService(session).family_with_members(user.id)
    if fam is not None:
        family, members = fam
        text = (
            f"Привет, {user.first_name}! Ты в семье «{family.name}» "
            f"({len(members)}/2).\n/family — детали."
        )
    else:
        text = (
            f"Привет, {user.first_name}! Семьи пока нет.\n\n"
            "• /newfamily — создать семью и получить код-приглашение\n"
            "• /join КОД — вступить по коду партнёра"
        )
    await message.answer(text, reply_markup=_app_keyboard())


@router.message(Command("app"))
async def cmd_app(message: Message) -> None:
    """Открыть Mini App ассистента (списки, финансы, питание, напоминания)."""
    kb = _app_keyboard()
    if kb is None:
        await message.answer(
            "Mini App ещё не настроена: не задан HTTPS-адрес (webapp_url в .env)."
        )
        return
    await message.answer(
        "🏠 <b>Семейный ассистент</b>\n\n"
        "Списки, финансы, питание и напоминания — в одном приложении.\n"
        "Нажми кнопку ниже, чтобы открыть.",
        reply_markup=kb,
    )


@router.message(Command("newfamily"))
async def cmd_newfamily(message: Message, user, session) -> None:
    ps = PairingService(session)
    try:
        family, invite = await ps.create_family(user)
        # Дефолтные расписания дайджеста — вместе с семьёй (ТЗ §3.1), до commit.
        seed_default_schedules(session, family.id)
        await session.commit()
    except KernelError as exc:
        await message.answer(f"⚠️ {exc.message}")
        return
    await message.answer(
        f"Семья «{family.name}» создана.\n\n"
        f"Код для партнёра: <code>{invite.code}</code>\n"
        f"Он вводит: <code>/join {invite.code}</code>"
    )


@router.message(Command("join"))
async def cmd_join(message: Message, command: CommandObject, user, session) -> None:
    code = (command.args or "").strip()
    if not code:
        await message.answer("Укажи код: <code>/join КОД</code>")
        return
    try:
        await PairingService(session).join(user, code)
        await session.commit()
    except KernelError as exc:
        await message.answer(f"⚠️ {exc.message}")
        return
    await message.answer("Готово — вы спарены! /family — детали.")


@router.message(Command("family"))
async def cmd_family(message: Message, user, session) -> None:
    fam = await IdentityService(session).family_with_members(user.id)
    if fam is None:
        await message.answer("Ты не в семье. /newfamily или /join КОД.")
        return
    family, members = fam
    lines = [f"👪 <b>{family.name}</b> ({len(members)}/2)"]
    for _membership, member in members:
        mark = " — ты" if member.id == user.id else ""
        lines.append(f"• {member.first_name}{mark}")
    await message.answer("\n".join(lines))
