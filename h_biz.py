"""کاسبی: خرید کسب‌وکار، استخدام کارگر (با اسم و رده‌ی مخصوص هر کسب‌وکار)، برداشت درآمد."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

import config as C
import eco
import econ
import rules
from db import User
from ui import b, back, edit, kb, pairs, receipt
from util import fa, utcnow

router = Router()


def _sell_ratio() -> float:
    return float(getattr(C, "WORKER_SELL_RATIO", C.SELL_RATIO))


def _worker_pay(w: dict) -> int:
    """پولی که با فروش یک کارگر این رده برمی‌گرده."""
    return int(w["price"] * _sell_ratio())


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
        lines.append(f"⏱ درآمد هر ساعت: <b>{fa(await eco.income_per_hour(u))}</b>")
        lines.append(f"👶 بچه‌ها: <b>{fa(u.kids)}</b> | ضریب: <b>+{fa(bonus)}٪</b>" + (" 🍽 گرسنه‌ن" if u.kids > 0 and not fed else ""))
        lines.append(f"\n💡 هر بچه‌ی سیر <b>{fa(int(C.KID_BONUS * 100))}٪</b> روی درآمد تأثیر داره")
        ev = econ.current_event()
        if ev:
            lines.append(f"📰 اتفاق شهر: {ev['emoji']} {ev['title']} — اثر روی درآمد تو: <b>{econ.pct(econ.current_effect(u))}</b>")
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


def _staff_lines(bz: dict) -> list[str]:
    """معرفی کارگرهای اختصاصی یه کسب‌وکار: اسم، قیمت و سهم درآمد هر رده."""
    lines = [f"👷 <b>کارگرها</b> (جمعاً تا {fa(C.MAX_WORKERS)} نفر):"]
    for w in bz["workers"]:
        lines.append(f"  {w['emoji']} {w['name']}: {fa(w['price'])} ← +{fa(int(bz['income'] * w['mult']))} / ۶ ساعت")
    best = max(bz["workers"], key=lambda w: w["mult"])
    lines.append(f"\n💎 با {fa(C.MAX_WORKERS)} {best['name']}: <b>{fa(int(bz['income'] * (1 + C.MAX_WORKERS * best['mult'])))}</b> / ۶ ساعت")
    return lines


async def render_info(u: User, key: str):
    """مشخصات یه کسب‌وکار قبل از خرید (تو خود کسب‌وکار، نه تو لیست)."""
    bz = rules.BIZ_INDEX[key]
    text = (f"{bz['emoji']} <b>{bz['name']}</b>\n\n"
            f"💵 قیمت خرید: <b>{fa(bz['price'])}</b>\n"
            f"📈 درآمد پایه: <b>{fa(bz['income'])}</b> / ۶ ساعت\n\n"
            + "\n".join(_staff_lines(bz)))
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
    cnt = rules.staff_counts(u, key)
    tot = sum(cnt)
    total = rules.base_income(u.businesses, u.staff)
    mine = int(await eco.calc_pending(u) * bz["income"] * rules.biz_multiplier(u, key) / total) if total else 0   # سهم همین کسب‌وکار از درآمد جمع‌شده
    hourly = int(await eco.income_per_hour(u) * bz["income"] * rules.biz_multiplier(u, key) / total) if total else 0
    text = f"{bz['emoji']} <b>{bz['name']}</b> — 💰 درآمد جمع‌شده: <b>{fa(mine)}</b>\n⏱ درآمد هر ساعت: <b>{fa(hourly)}</b>\n\n"
    text += f"👷 کارگرها ({fa(tot)}/{fa(C.MAX_WORKERS)}):\n" + "\n".join(
        f"  {w['emoji']} {w['name']}: {fa(cnt[w['idx']])}" for w in bz["workers"])
    rows = []
    if tot < C.MAX_WORKERS:
        for w in bz["workers"]:
            rows.append([b(f"{w['emoji']} استخدام {w['name']} — {fa(w['price'])}", f"biz:hire:{key}:{w['idx']}")])
    # 💱 فروش کارگر: جای خالی باز می‌شه تا کارگر جدید (یا رده‌ی بهتر) بگیری
    sell_rows = []
    for w in bz["workers"]:
        n = cnt[w["idx"]]
        if n <= 0:
            continue
        pay = _worker_pay(w)
        btns = [b(f"💱 فروش {w['emoji']} {w['name']} (+{fa(pay)})", f"biz:fire:{key}:{w['idx']}:1")]
        if n > 1:
            btns.append(b(f"همه ({fa(n)}) +{fa(pay * n)}", f"biz:fire:{key}:{w['idx']}:all"))
        sell_rows.append(btns)
    if sell_rows:
        text += (f"\n\n💱 <b>فروش کارگر</b>: {int(_sell_ratio() * 100)}٪ قیمت خریدش برمی‌گرده و جاش خالی می‌شه؛ "
                 f"بعدش می‌تونی کارگر جدید استخدام کنی.")
        rows += sell_rows
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
    parts = cb.data.split(":")
    key = parts[2]
    bz = rules.BIZ_INDEX.get(key)
    if not bz or key not in user.businesses:
        return await cb.answer("این رو نداری!", show_alert=True)
    try:
        idx = int(parts[3]) if len(parts) > 3 else 0      # دکمه‌های قدیمی (بدون رده) = رده‌ی اول
    except ValueError:
        idx = 0
    if not 0 <= idx < len(bz["workers"]):
        return await cb.answer()
    w = bz["workers"][idx]
    if rules.total_workers(user, key) >= C.MAX_WORKERS:
        return await cb.answer("ظرفیت کارگر پره!", show_alert=True)
    if user.cash < w["price"]:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.settle(user.tg_id)       # درآمد قبلی با تعداد کارگر قبلی حساب شه
    field = f"businesses.{key}" if idx == 0 else f"staff.{rules.staff_key(key, idx)}"
    # سقف کل کارگرها (همه‌ی رده‌ها با هم) اتمیک چک می‌شه
    fields = [f"$businesses.{key}"] + [f"$staff.{rules.staff_key(key, i)}" for i in range(1, len(bz["workers"]))]
    cap = {"$expr": {"$lt": [{"$add": [{"$ifNull": [f, 0]} for f in fields]}, C.MAX_WORKERS]}}
    if not await eco.spend(user.tg_id, w["price"], inc={field: 1}, flt=cap, invest=w["price"]):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer("✅ خرید انجام شد!")
    await receipt(cb, f"{w['emoji']} {w['name']} جدید — {fa(w['price'])} دلار")
    await _show(cb, user.tg_id, render_view, key, note=f"✅ <b>خرید انجام شد</b> — {w['emoji']} {w['name']} جدید استخدام شد!")


@router.callback_query(F.data.startswith("biz:fire:"))
async def cb_fire(cb: CallbackQuery, user: User) -> None:
    """💱 فروش کارگر: biz:fire:{کسب‌وکار}:{رده}:{1|all}"""
    parts = cb.data.split(":")
    try:
        key, idx, mode = parts[2], int(parts[3]), parts[4]
    except (IndexError, ValueError):
        return await cb.answer()
    bz = rules.BIZ_INDEX.get(key)
    if not bz or not 0 <= idx < len(bz["workers"]):
        return await cb.answer()
    if key not in user.businesses:
        return await cb.answer("این رو نداری!", show_alert=True)
    w = bz["workers"][idx]
    await eco.settle(user.tg_id)                     # درآمد تا الان با تعداد کارگر قبلی حساب شه
    fresh = await eco.get_user(user.tg_id)
    have = rules.staff_counts(fresh, key)[idx]
    n = have if mode == "all" else 1
    if n <= 0 or have < n:
        return await cb.answer("این کارگر رو دیگه نداری!", show_alert=True)
    pay = _worker_pay(w) * n
    field = f"businesses.{key}" if idx == 0 else f"staff.{rules.staff_key(key, idx)}"
    # اتمیک: کارگر کم می‌شه + پول میاد + ارزش خرید از «invested» کم می‌شه (که رتبه‌بندی ثروت الکی بالا نمونه)
    r = await eco.col().update_one(
        {"tg_id": user.tg_id, field: {"$gte": n}},
        [{"$set": {field: {"$subtract": [f"${field}", n]},
                   "cash": {"$add": ["$cash", pay]},
                   "invested": {"$max": [0, {"$subtract": ["$invested", w["price"] * n]}]}}}])
    if r.matched_count != 1:
        return await cb.answer("⚠️ موجودی تغییر کرد؛ دوباره امتحان کن.", show_alert=True)
    await cb.answer("💱 فروخته شد!")
    await _show(cb, user.tg_id, render_view, key,
                note=f"💱 {fa(n)} {w['emoji']} {w['name']} فروخته شد: <b>+{fa(pay)}</b> دلار — حالا می‌تونی کارگر جدید بگیری!")
