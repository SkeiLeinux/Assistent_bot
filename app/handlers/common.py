"""
handlers/common.py — общие команды: /start, /help, /family.

FSM (Finite State Machine) — машина состояний.
Используется когда боту нужен диалог из нескольких шагов.
Например: бот спрашивает название группы → ждёт ответа → создаёт группу.
Без FSM бот не знал бы "в каком месте диалога" находится пользователь.

States — это просто класс с именами состояний.
"""

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User
from app.services import UserService

router = Router()


def _food_webapp_button() -> InlineKeyboardButton | None:
    """Кнопка запуска мини-апы «Еда». None, если webapp_url не задан."""
    if not settings.webapp_url:
        return None
    return InlineKeyboardButton(
        text="🍎 Дневник питания",
        web_app=WebAppInfo(url=settings.webapp_url),
    )


# ── FSM States ────────────────────────────────────────────────────────────────

class FamilyStates(StatesGroup):
    """Состояния для создания/вступления в семью."""
    waiting_for_family_name = State()    # ждём название новой группы
    waiting_for_invite_code = State()    # ждём инвайт-код


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, user: User):
    """
    Приветственное сообщение.
    user уже есть в аргументах — middleware положил его туда.
    """
    family_status = "не состоишь в группе"
    if user.family_id:
        family_status = f"группа #{user.family_id}"

    food_line = "/food — дневник питания (мини-апа)\n" if settings.webapp_url else ""
    food_btn = _food_webapp_button()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[food_btn]]) if food_btn else None

    await message.answer(
        f"Привет, {user.full_name}! 👋\n\n"
        f"Я помогу вам с партнёром вести общие списки и задачи.\n\n"
        f"Статус: {family_status}\n\n"
        f"Команды:\n"
        f"/lists — открыть списки\n"
        f"{food_line}"
        f"/family — управление группой\n"
        f"/help — помощь",
        reply_markup=keyboard,
    )


# ── /food — запуск мини-апы «Еда» ──────────────────────────────────────────────

@router.message(Command("food"))
async def cmd_food(message: Message):
    """Открыть мини-апу дневника питания."""
    btn = _food_webapp_button()
    if btn is None:
        await message.answer(
            "Мини-апа «Еда» ещё не настроена: не задан HTTPS-адрес (webapp_url в .env)."
        )
        return
    await message.answer(
        "🍎 <b>Дневник питания</b>\n\n"
        "Считай КБЖУ по тексту или фото, отслеживай цели и историю.\n"
        "Нажми кнопку ниже, чтобы открыть.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]),
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    food_block = (
        "<b>Еда:</b>\n/food — дневник питания (мини-апа): КБЖУ по тексту/фото, цели, история\n\n"
        if settings.webapp_url else ""
    )
    await message.answer(
        "📋 <b>Life Manager</b>\n\n"
        "<b>Списки:</b>\n"
        "/lists — все категории списков\n\n"
        f"{food_block}"
        "<b>Группа:</b>\n"
        "/family — создать группу или вступить по коду\n\n"
        "Управление через кнопки под сообщениями.",
        parse_mode="HTML",
    )


# ── /family ───────────────────────────────────────────────────────────────────

@router.message(Command("family"))
async def cmd_family(message: Message, user: User, session: AsyncSession):
    """Показывает статус группы и варианты действий."""
    if user.family_id:
        from app.repositories import FamilyRepository
        family_repo = FamilyRepository(session)
        family = await family_repo.get_by_id(user.family_id)

        await message.answer(
            f"👥 Твоя группа: <b>{family.name}</b>\n"
            f"Инвайт-код: <code>{family.invite_code}</code>\n\n"
            f"Поделись кодом с партнёром — он введёт его через /family.",
            parse_mode="HTML",
        )
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать группу", callback_data="family:create")],
            [InlineKeyboardButton(text="🔑 Вступить по коду", callback_data="family:join")],
        ])
        await message.answer(
            "Ты пока не состоишь в группе.\n\n"
            "Создай новую группу или вступи в существующую по инвайт-коду.",
            reply_markup=keyboard,
        )


# ── Создание группы ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "family:create")
async def family_create_start(callback: CallbackQuery, state: FSMContext):
    """Начало диалога создания группы — переходим в состояние ожидания имени."""
    await callback.message.edit_text("Придумай название для вашей группы:")
    # Устанавливаем состояние — следующее сообщение пользователя
    # попадёт в family_create_name, а не в общий обработчик
    await state.set_state(FamilyStates.waiting_for_family_name)
    await callback.answer()


@router.message(FamilyStates.waiting_for_family_name)
async def family_create_name(
    message: Message, state: FSMContext, user: User, session: AsyncSession
):
    """Получили название — создаём группу."""
    name = message.text.strip()
    if not name or len(name) > 50:
        await message.answer("Название должно быть от 1 до 50 символов. Попробуй ещё раз:")
        return

    user_service = UserService(session)
    result = await user_service.create_family(user, name)

    # Сбрасываем состояние — диалог завершён
    await state.clear()

    await message.answer(
        result.message,
        parse_mode="Markdown" if result.success else None,
    )


# ── Вступление в группу ───────────────────────────────────────────────────────

@router.callback_query(F.data == "family:join")
async def family_join_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введи инвайт-код группы:\n(например: <code>ABC123</code>)",
        parse_mode="HTML",
    )
    await state.set_state(FamilyStates.waiting_for_invite_code)
    await callback.answer()


@router.message(FamilyStates.waiting_for_invite_code)
async def family_join_code(
    message: Message, state: FSMContext, user: User, session: AsyncSession
):
    """Получили код — пытаемся вступить."""
    code = message.text.strip()
    user_service = UserService(session)
    result = await user_service.join_family(user, code)

    await state.clear()
    await message.answer(result.message)