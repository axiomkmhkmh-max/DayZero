"""کمک‌تابع‌های رابط: دکمه، ویرایش امن پیام."""
from __future__ import annotations

from typing import Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from redis_service import redis_service

OWN_TTL = 86400          # منوی گروه تا ۲۴ ساعت فقط برای صاحبش کار می‌کنه


# ───────────────────────── رنگ دکمه‌ها ─────────────────────────
# تلگرام سه رنگ برای دکمه‌ی شیشه‌ای داره: primary = 🟦 آبی ، success = 🟩 سبز ، danger = 🟥 قرمز (بقیه خاکستری).
# رنگ خودکار از روی callback_data انتخاب می‌شه تا نیازی به دست‌زدن به بقیه‌ی فایل‌ها نباشه؛
# برای رنگ دستی: b("متن", "data", style="success").
PRIMARY, SUCCESS, DANGER = "primary", "success", "danger"

_DANGER = {"fight:random", "fight:target", "fight:hack", "fight:steal", "al:leave", "fam:divorce"}
_DANGER_PREFIX = ("al:kick:", "al:no:", "fire:y:", "fire:f:", "bo:x:")
_SUCCESS = {"job:work", "biz:collect", "daily", "fam:child", "fam:propose", "al:create", "house:up", "al:up",
            "fam:cook", "fam:banana", "fam:potion", "bo:div"}
_SUCCESS_PREFIX = ("bank:dep:", "bank:take:", "bank:pay", "mk:antirob:buy", "sell:g:", "sell:d:", "sell:f:", "walk:p:", "biz:buy:", "biz:hire:", "bo:b:", "mk:buy:", "mk:dbuy:", "forces:buy:", "fam:eat:", "vault:dep:",
                   "al:join:", "al:dp:", "al:uq:", "al:mg:", "al:dv:", "al:wd:", "al:ok:", "al:oil:")
_PRIMARY = {"job", "walk", "biz", "bo", "bo:me", "bo:news", "home", "mk", "games", "fight", "al", "rank", "fam", "vault", "closet", "forces", "house",
            "bank", "bank:loan", "sell", "sell:gear", "sell:dark", "sell:force", "fire:force", "mk:antirob",
            "mk:shop", "mk:dark", "biz:shop", "al:list", "al:members", "al:dep", "al:inc", "al:forces", "al:mis", "al:oil", "al:arm", "al:req", "al:link", "al:def", "al:war", "al:help", "al:foes", "al:wt", "fight:log", "fam:feed", "fam:power",
            "fam:marry", "fam:guide"}
_PRIMARY_PREFIX = ("al:uc:", "al:uk:", "al:dl:", "al:uu:", "al:ms:", "al:dg:", "al:foe:", "bank:wd:", "help:", "mk:cat:", "biz:view:", "bo:s:", "use:", "vault:wd:")


def auto_style(text: str, data: str) -> Optional[str]:
    """رنگ پیش‌فرض دکمه: آبی = ورود به پنل ، سبز = کار/خرید/تایید ، قرمز = حمله/لغو/خروج ، خاکستری = برگشت و کنترل‌ها."""
    if text.startswith("◀️") or data == "noop":
        return None
    if data.startswith("mt:"):                                    # دوئل: mt:<id>:<acc|rej|can|mN>
        op = data.rsplit(":", 1)[-1]
        return SUCCESS if op == "acc" else DANGER if op in ("rej", "can") else None
    if data.startswith(("mr:", "dv:")):                           # ازدواج/طلاق: mr:<y|n>:… و dv:<y|n|f>:…
        op = data.split(":")[1]
        return SUCCESS if op == "y" else DANGER if op in ("n", "f") else None
    if data in _DANGER or data.startswith(_DANGER_PREFIX) or "لغو" in text:
        return DANGER
    if data in _SUCCESS or data.startswith(_SUCCESS_PREFIX):
        return SUCCESS
    if data.startswith("g:"):                                     # قمارخونه
        parts = data.split(":")
        if len(parts) == 2:
            return PRIMARY                                        # ورود به یه بازی
        return SUCCESS if parts[2] == "play" else None            # شروع بازی سبز؛ تنظیم شرط خاکستری
    if data in _PRIMARY or data.startswith(_PRIMARY_PREFIX):
        return PRIMARY
    if data.startswith("forces:"):
        return PRIMARY
    return None


def b(text: str, data: str, style: Optional[str] = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, style=style or auto_style(text, data))


def url_b(text: str, url: str, style: Optional[str] = PRIMARY) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url, style=style)


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


async def receipt(cb: CallbackQuery, text: str) -> None:
    """بعد از هر خرید: یه پیام جدا تو چت می‌فرسته که «خرید انجام شد» (خطای ارسال نباید خرید رو خراب کنه)."""
    try:
        await cb.message.answer(f"✅ <b>خرید انجام شد!</b>\n{text}")
    except Exception:
        pass
