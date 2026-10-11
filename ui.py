"""کمک‌تابع‌های رابط: دکمه، ویرایش امن پیام."""
from __future__ import annotations

from typing import Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup


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
        await cb.message.answer(text, reply_markup=markup)


def is_private(cb: CallbackQuery) -> bool:
    return cb.message is not None and cb.message.chat.type == "private"
