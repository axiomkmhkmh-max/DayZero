"""جواب دکمه‌های ازدواج (mr:) و طلاق (dv:) — فقط طرف مقابل می‌تونه قبول/رد کنه."""
from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.types import CallbackQuery

import actions
import eco
from db import User
from redis_service import redis_service
from util import esc, left, mention

router = Router()


async def _done(cb: CallbackQuery, text: str) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=None)
    except Exception:  # noqa: BLE001
        await cb.message.answer(text)


@router.callback_query(F.data.startswith("mr:"))
async def cb_marriage(cb: CallbackQuery, user: User) -> None:
    _, ans, a, t = cb.data.split(":")
    a, t = int(a), int(t)
    if cb.from_user.id != t:
        return await cb.answer("این پیشنهاد برای تو نیست!", show_alert=True)
    A, T = await eco.get_user(a), await eco.get_user(t)
    if A is None or T is None:
        return await cb.answer("بازیکن پیدا نشد.", show_alert=True)
    if ans == "n":
        await cb.answer()
        await _done(cb, f"❌ {mention(t, T.name)} پیشنهاد ازدواج {mention(a, A.name)} رو رد کرد.")
        await actions.send(cb.bot, a, f"💔 {esc(T.name)} پیشنهادت رو رد کرد.")
        return
    if A.spouse_id or T.spouse_id:
        await cb.answer("یکی‌تون متاهله!", show_alert=True)
        return await _done(cb, "💔 پیشنهاد منقضی شد (یکی از طرفین ازدواج کرده).")
    if await redis_service.r.exists(f"nomarry:{min(a, t)}:{max(a, t)}"):
        return await cb.answer("🚫 طلسم طلاق: فعلاً نمی‌تونید ازدواج کنید.", show_alert=True)
    if not await actions.do_marry(a, t):
        return await cb.answer("نشد! دوباره امتحان کنید.", show_alert=True)
    await cb.answer("💍")
    await _done(cb, f"💍🎉 {mention(a, A.name)} و {mention(t, T.name)} ازدواج کردن! مبارک باشه 💞\n"
                    f"+۱۰٪ درآمد کاسبی برای هر دو.")
    await actions.send(cb.bot, a, f"💍 {esc(T.name)} پیشنهادت رو قبول کرد! ازدواج کردید 🎉")


@router.callback_query(F.data.startswith("dv:"))
async def cb_divorce(cb: CallbackQuery, user: User) -> None:
    _, ans, a, t = cb.data.split(":")
    a, t = int(a), int(t)
    A, T = await eco.get_user(a), await eco.get_user(t)
    if A is None or T is None or A.spouse_id != t or T.spouse_id != a:
        await cb.answer("دیگه همسر نیستن!", show_alert=True)
        return await _done(cb, "ℹ️ این درخواست طلاق دیگه معتبر نیست.")
    who = cb.from_user.id
    if ans == "f":                                   # یک‌طرفه: فقط درخواست‌دهنده، بعد ۲۴ ساعت
        if who != a:
            return await cb.answer("فقط درخواست‌دهنده می‌تونه!", show_alert=True)
        ts = await redis_service.r.get(f"divreq:{a}")
        if not ts or time.time() - int(ts) < 24 * 3600:
            rem = 24 * 3600 - (time.time() - int(ts)) if ts else 24 * 3600
            return await cb.answer(f"⏳ هنوز {int(rem // 3600)} ساعت مونده.", show_alert=True)
        if left(A.love_lock_until) > 0:
            return await cb.answer("💘 طلسم عاشقی فعاله!", show_alert=True)
    elif who != t:
        return await cb.answer("این درخواست برای تو نیست!", show_alert=True)
    if ans == "n":
        await cb.answer()
        await _done(cb, f"❌ {mention(t, T.name)} درخواست طلاق رو رد کرد.")
        await actions.send(cb.bot, a, f"❌ {esc(T.name)} درخواست طلاقت رو رد کرد.")
        return
    if left(A.love_lock_until) > 0:
        return await cb.answer("💘 طلسم عاشقی فعاله!", show_alert=True)
    await actions.do_divorce(a, t)
    await redis_service.r.delete(f"divreq:{a}")
    await cb.answer()
    await _done(cb, f"💔 {mention(a, A.name)} و {mention(t, T.name)} از هم جدا شدن.")
    await actions.send(cb.bot, a if who != a else t, "💔 طلاق ثبت شد.")
