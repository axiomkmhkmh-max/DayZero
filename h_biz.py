"""کاسبی: خرید کسب‌وکار، استخدام کارگر، برداشت درآمد."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

import config as C
import eco
import rules
from db import User
from ui import b, back, edit, kb, pairs
from util import fa, utcnow

router = Router()


async def _show(cb: CallbackQuery, uid: int, render, *args, note: str = "") -> None:
    u = await eco.get_user(uid)
    text, markup = await render(u, *args)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


async def render_biz(u: User):
    pending = await eco.calc_pending(u)
    total = rules.base_income(u.businesses)
    lines = ["🏪 <b>کاسبی</b>\n"]
    if u.businesses:
        lines.append(f"🏬 کسب‌وکارها: <b>{fa(len(u.businesses))}</b> از {fa(len(C.BUSINESSES))}")
        lines.append(f"📊 درآمد پایه: <b>{fa(total)}</b> / ۶ ساعت (قبل از بونوس بچه‌ها، ازدواج، اتحاد و …)")
        lines.append(f"💰 آماده‌ی برداشت: <b>{fa(pending)}</b> (ذخیره تا {fa(C.INCOME_CAP_HOURS)} ساعت)")
        lines.append("\n👇 روی هر کسب‌وکار بزن تا مشخصاتش رو ببینی.")
    else:
        lines.append("هنوز کسب‌وکاری نداری! با یه سوپری شروع کن. 👇")
    rows = []
    if u.businesses:
        rows.append([b(f"💰 برداشت درآمد ({fa(pending)})", "biz:collect")])
        rows += pairs([b(f"{rules.BIZ_INDEX[k]['emoji']} {rules.BIZ_INDEX[k]['name']}", f"biz:view:{k}")
                       for k in u.businesses if k in rules.BIZ_INDEX], 2)
    if len(u.businesses) < len(C.BUSINESSES):
        rows.append([b("🛒 خرید کسب‌وکار", "biz:shop")])
    rows.append(back("menu", "◀️ منو"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "biz")
async def cb_biz(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_biz)


@router.callback_query(F.data == "biz:collect")
async def cb_collect(cb: CallbackQuery, user: User) -> None:
    amount = await eco.settle(user.tg_id)
    if amount <= 0:
        return await cb.answer("هنوز چیزی جمع نشده.", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_biz, note=f"💰 <b>+{fa(amount)}</b> دلار اومد تو جیبت!")


async def render_shop(u: User):
    lines = ["🛒 <b>خرید کسب‌وکار</b>\n"]
    rows = []
    for bz in rules.BIZ_INDEX.values():
        if bz["key"] in u.businesses:
            continue
        lines.append(f"{bz['emoji']} {bz['name']} — درآمد {fa(bz['income'])}/۶ساعت — کارگر {fa(bz['worker'])}")
        rows.append([b(f"{bz['emoji']} {bz['name']} — {fa(bz['price'])}", f"biz:buy:{bz['key']}")])
    lines.append(f"\n👷 هر کسب‌وکار تا {fa(C.MAX_WORKERS)} کارگر می‌گیره و هر کارگر درآمد پایه رو یه بار دیگه اضافه می‌کنه.")
    rows.append(back("biz", "◀️ کاسبی"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "biz:shop")
async def cb_shop(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_shop)


@router.callback_query(F.data.startswith("biz:buy:"))
async def cb_buy(cb: CallbackQuery, user: User) -> None:
    bz = rules.BIZ_INDEX.get(cb.data.split(":")[2])
    if not bz:
        return await cb.answer()
    if bz["key"] in user.businesses:
        return await cb.answer("این رو داری!", show_alert=True)
    if user.cash < bz["price"]:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.settle(user.tg_id)       # ساعت درآمد ریست شه تا کسب‌وکار جدید از «الان» حساب بشه
    if not await eco.spend(user.tg_id, bz["price"], set_={f"businesses.{bz['key']}": 0},
                           flt={f"businesses.{bz['key']}": {"$exists": False}}, invest=bz["price"]):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_biz, note=f"🎉 {bz['emoji']} {bz['name']} راه افتاد!")


async def render_view(u: User, key: str):
    bz = rules.BIZ_INDEX[key]
    w = u.businesses.get(key, 0)
    text = (f"{bz['emoji']} <b>{bz['name']}</b>\n\n👷 کارگرها: {fa(w)}/{fa(C.MAX_WORKERS)}\n"
            f"💵 درآمد فعلی: <b>{fa(bz['income'] * (1 + w))}</b> / ۶ ساعت\n"
            f"(پایه {fa(bz['income'])} + هر کارگر {fa(bz['income'])})")
    rows = []
    if w < C.MAX_WORKERS:
        rows.append([b(f"👷 استخدام کارگر — {fa(bz['worker'])}", f"biz:hire:{key}")])
    else:
        text += "\n\n✅ ظرفیت کارگر پره."
    rows.append(back("biz", "◀️ کاسبی"))
    return text, kb(*rows)


@router.callback_query(F.data.startswith("biz:view:"))
async def cb_view(cb: CallbackQuery, user: User) -> None:
    key = cb.data.split(":")[2]
    if key not in user.businesses:
        return await cb.answer("این رو نداری!", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_view, key)


@router.callback_query(F.data.startswith("biz:hire:"))
async def cb_hire(cb: CallbackQuery, user: User) -> None:
    key = cb.data.split(":")[2]
    bz = rules.BIZ_INDEX.get(key)
    if not bz or key not in user.businesses:
        return await cb.answer("این رو نداری!", show_alert=True)
    if user.businesses[key] >= C.MAX_WORKERS:
        return await cb.answer("ظرفیت کارگر پره!", show_alert=True)
    if user.cash < bz["worker"]:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.settle(user.tg_id)       # درآمد قبلی با تعداد کارگر قبلی حساب شه
    if not await eco.spend(user.tg_id, bz["worker"], inc={f"businesses.{key}": 1},
                           flt={f"businesses.{key}": {"$lt": C.MAX_WORKERS}}, invest=bz["worker"]):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_view, key, note="👷 کارگر جدید استخدام شد!")
