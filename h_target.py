"""گرفتن هدف (یوزرنیم/آیدی) در پیوی برای کارهایی مثل ازدواج، جن، طلسم و حمله."""
from __future__ import annotations

import re
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import actions
import eco
from db import User
from states import Ask
from ui import b, edit, kb

router = Router()

TITLES = {
    "marry": "💍 ازدواج", "attack": "⚔️ حمله", "jinn": "👹 جن", "vthief": "🥷 دزد گاوصندوق",
    "love": "💘 طلسم عاشقی", "split": "💔 طلسم طلاق",
}


async def ask_target(cb: CallbackQuery, state: FSMContext, action: str, extra: str = "") -> None:
    await state.set_state(Ask.target)
    await state.update_data(action=action)
    await cb.answer()
    await edit(cb, f"<b>{TITLES.get(action, action)}</b>\n\n🎯 <b>@یوزرنیم</b> یا <b>آیدی عددی</b> هدف رو بفرست.\n"
                   f"(طرف باید یه بار توی بازی باشه.)" + (f"\n\n{extra}" if extra else ""),
               kb([b("❌ لغو", "menu")]))


async def resolve(text: str) -> Optional[User]:
    t = text.strip()
    if t.startswith("#"):
        t = t[1:]
    if t.isdigit():
        return await eco.get_user(int(t))
    m = re.match(r"^@?([A-Za-z0-9_]{4,32})$", t)
    return await eco.find_by_username(m.group(1)) if m else None


@router.message(StateFilter(Ask.target), F.chat.type == "private", F.text)
async def got_target(m: Message, user: User, state: FSMContext) -> None:
    txt = (m.text or "").strip()
    if txt in ("لغو", "/cancel", "/menu", "/start"):
        await state.clear()
        await m.answer("❌ لغو شد. /menu")
        return
    target = await resolve(txt.split()[0] if txt.split() else "")
    if target is None:
        await m.answer("🤷 همچین بازیکنی پیدا نشد. دوباره بفرست یا «لغو» بنویس.")
        return
    action = (await state.get_data()).get("action", "")
    await state.clear()
    extra = " ".join(txt.split()[1:])
    text = await actions.run(m.bot, action, user, target, chat_id=m.chat.id, extra=extra)
    await m.answer(text, reply_markup=kb([b("◀️ منو", "menu")]))


@router.callback_query(F.data.startswith("use:"))
async def cb_use(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    item = cb.data.split(":")[1]
    action = {"jinn": "jinn", "vthief": "vthief", "love": "love", "divorce": "split"}.get(item)
    if not action or user.items.get(item, 0) < 1:
        await cb.answer("این آیتم رو نداری.", show_alert=True)
        return
    await ask_target(cb, state, action)
