"""کمک‌تابع‌های رابط: دکمه، ویرایش امن پیام."""
from __future__ import annotations

from typing import Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from redis_service import redis_service

OWN_TTL = 86400          # منوی گروه تا ۲۴ ساعت فقط برای صاحبش کار می‌کنه


def b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def url_b(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def pairs(buttons: list[InlineKeyboardButton], per_row: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def back(to: str = "menu", label: str = "◀️ برگشت") -> list[InlineKeyboardButton]:
    return [b(label, to)]


async def edit(cb: CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        await answer_owned(cb, text, markup)


def is_private(cb: CallbackQuery) -> bool:
    return cb.message is not None and cb.message.chat.type == "private"


def in_group(cb: CallbackQuery) -> bool:
    return cb.message is not None and cb.message.chat.type != "private"


async def claim(msg: Message, uid: int) -> None:
    """پیام (منو) توی گروه رو به یه بازیکن می‌سپره؛ فقط خودش می‌تونه دکمه‌هاش رو بزنه."""
    if msg.chat.type != "private":
        await redis_service.r.set(f"own:{msg.chat.id}:{msg.message_id}", uid, ex=OWN_TTL)


async def answer_owned(cb: CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> Message:
    sent = await cb.message.answer(text, reply_markup=markup)
    await claim(sent, cb.from_user.id)
    return sent


async def reply_owned(m: Message, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> Message:
    """پیوی: answer ساده. گروه: ریپلای + سپردن پیام به فرستنده."""
    if m.chat.type == "private":
        return await m.answer(text, reply_markup=markup)
    sent = await m.reply(text, reply_markup=markup)
    if markup is not None:
        await claim(sent, m.from_user.id)
    return sent


def replying_to_bot(m: Message) -> bool:
    """پیوی همیشه True؛ توی گروه فقط وقتی روی پیام ربات ریپلای شده (برای جواب سؤال‌های «اسم/یوزرنیم بفرست»)."""
    if m.chat.type == "private":
        return True
    r = m.reply_to_message
    return bool(r and r.from_user and r.from_user.is_bot)
