"""کاسبی: خرید کسب‌وکار، استخدام کارگر، برداشت درآمد."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

import config as C
import eco
import rules
from db import User
from ui import b, back, edit, kb, pairs, receipt
from util import fa, utcnow

router = Router()


async def _show(cb: CallbackQuery, uid: int, render, *args, note: str = "") -> None:
    u = await eco.get_user(uid)
    text, markup = await render(u, *args)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


async def render_biz(u: User):
    pending = await eco.calc_pending(u)
    lines = ["🏪 <b>کاسبی</b>\n"]
    if u.businesses:
        fed = u.kids > 0 and (u.cook or (u.fed_until is not None and u.fed_until > utcnow()))
        bonus = int(C.KID_BONUS * u.kids * 100) if fed else 0
        lines.append(f"💰 درآمد جمع‌شده: <b>{fa(pending)}</b>")
        lines.append(f"👶 بچه‌ها: <b>{fa(u.kids)}</b> | ضریب: <b>+{fa(bonus)}٪</b>" + (" 🍽 گرسنه‌ن" if u.kids > 0 and not fed else ""))
        lines.append(f"\n💡 هر بچه‌ی سیر <b>{fa(int(C.KID_BONUS * 100))}٪</b> روی درآمد تأثیر داره")
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
    await eco.add_xp(user.tg_id, C.XP["collect"])
    await cb.answer()
    await _show(cb, user.tg_id, render_biz, note=f"💰 <b>+{fa(amount)}</b> دلار اومد تو جیبت!")


async def render_shop(u: User):
    lines = ["🛒 <b>خرید کسب‌وکار</b>\n", "👇 روی هر کسب‌وکار بزن تا مشخصاتش رو ببینی و بخری."]
    rows = []
    for bz in rules.BIZ_INDEX.values():
        if bz["key"] in u.businesses:
            continue
        rows.append([b(f"{bz['emoji']} {bz['name']} — {fa(bz['price'])}", f"biz:info:{bz['key']}")])
    rows.append(back("biz", "◀️ کاسبی"))
    return "\n".join(lines), kb(*rows)


async def render_info(u: User, key: str):
    """مشخصات یه کسب‌وکار قبل از خرید (تو خود کسب‌وکار، نه تو لیست)."""
    bz = rules.BIZ_INDEX[key]
    text = (f"{bz['emoji']} <b>{bz['name']}</b>\n\n"
            f"💵 قیمت خرید: <b>{fa(bz['price'])}</b>\n"
            f"📈 درآمد پایه: <b>{fa(bz['income'])}</b> / ۶ ساعت\n"
            f"👷 هر کارگر: {fa(bz['worker'])} (درآمد پایه رو یه بار دیگه اضافه می‌کنه)\n"
            f"👷 تا {fa(C.MAX_WORKERS)} کارگر؛ با همه‌ی کارگرها: <b>{fa(bz['income'] * (1 + C.MAX_WORKERS))}</b> / ۶ ساعت")
    return text, kb([b(f"🛒 خرید — {fa(bz['price'])}", f"biz:buy:{key}")], back("biz:shop", "◀️ لیست کسب‌وکارها"))


@router.callback_query(F.data == "biz:shop")
async def cb_shop(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_shop)


@router.callback_query(F.data.startswith("biz:info:"))
async def cb_info(cb: CallbackQuery, user: User) -> None:
    key = cb.data.split(":")[2]
    if key not in rules.BIZ_INDEX:
        return await cb.answer()
    await cb.answer()
    if key in user.businesses:                      # قبلاً خریده → صفحه‌ی خود کسب‌وکار
        return await _show(cb, user.tg_id, render_view, key)
    await _show(cb, user.tg_id, render_info, key)


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
    await eco.add_xp(user.tg_id, C.XP["biz"])
    await cb.answer(f"✅ خرید انجام شد!\n{bz['emoji']} {bz['name']} — {fa(bz['price'])} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_biz, note=f"✅ <b>خرید انجام شد</b> — 🎉 {bz['emoji']} {bz['name']} راه افتاد!")


async def render_view(u: User, key: str):
    bz = rules.BIZ_INDEX[key]
    w = u.businesses.get(key, 0)
    total = rules.base_income(u.businesses)
    mine = int(await eco.calc_pending(u) * bz["income"] * (1 + w) / total) if total else 0   # سهم همین کسب‌وکار از درآمد جمع‌شده
    text = f"{bz['emoji']} <b>{bz['name']}</b> — 💰 درآمد جمع‌شده: <b>{fa(mine)}</b>"
    rows = []
    if w < C.MAX_WORKERS:
        rows.append([b(f"👷 استخدام کارگر ({fa(w)}/{fa(C.MAX_WORKERS)}) — {fa(bz['worker'])}", f"biz:hire:{key}")])
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
    await cb.answer(f"✅ خرید انجام شد!\n👷 کارگر جدید — {fa(bz['worker'])} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_view, key, note="✅ <b>خرید انجام شد</b> — 👷 کارگر جدید استخدام شد!")
