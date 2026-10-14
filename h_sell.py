"""فروش نیروها و وسایل کمد (فروشگاه + دارک وب): هر کدوم SELL_RATIO قیمت خرید."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

import config as C
import eco
import rules
from db import User
from ui import b, back, edit, kb
from util import fa

router = Router()


def _ratio_txt() -> str:
    return f"{int(C.SELL_RATIO * 100)}٪"


def dark_unit_price(key: str) -> int:
    """قیمت یک واحد آیتم دارک وب (جنگیر یک بسته‌ی چندبارمصرفه)."""
    d = rules.DARK_INDEX[key]
    return d["price"] // (C.JINNGIR_CHARGES if key == "jinngir" else 1)


async def _show(cb: CallbackQuery, uid: int, render, *args, note: str = "") -> None:
    u = await eco.get_user(uid)
    text, markup = await render(u, *args)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


async def render_sell(u: User):
    n_gear = sum(1 for k in u.gear if k in rules.SHOP_INDEX)
    n_dark = sum(n for k, n in u.items.items() if n > 0 and k in rules.DARK_INDEX)
    n_force = rules.force_count(u.forces)
    text = (f"💰 <b>فروش</b>\n\nهر چیزی بفروشی {_ratio_txt()} قیمت خریدش رو پس می‌گیری.\n\n"
            f"🗄 وسایل فروشگاه: {fa(n_gear)}\n🕶 آیتم‌های دارک وب: {fa(n_dark)}\n💂 نیروها: {fa(n_force)}")
    return text, kb([b("🗄 فروش وسایل کمد", "sell:gear")], [b("🕶 فروش آیتم دارک وب", "sell:dark")],
                    [b("💂 فروش نیرو", "sell:force")], back("home", "◀️ خونه"))


async def render_gear(u: User):
    rows = []
    for cat, c in C.SHOP.items():
        for it in rules.owned(u.gear, cat):
            rows.append([b(f"{it['emoji']} {it['name']} ({c['stat']}{fa(it['power'])}) → +{fa(int(it['price'] * C.SELL_RATIO))}",
                           f"sell:g:{it['key']}")])
    text = "🗄 <b>فروش وسایل کمد</b>\nروی هر وسیله بزنی همون لحظه فروخته می‌شه." if rows else "🗄 کمدت خالیه؛ چیزی برای فروش نداری."
    rows.append(back("sell", "◀️ فروش"))
    return text, kb(*rows)


async def render_dark(u: User):
    rows = []
    for k, n in u.items.items():
        if n > 0 and k in rules.DARK_INDEX:
            d = rules.DARK_INDEX[k]
            unit = " (هر بار دفاع)" if k == "jinngir" else ""
            rows.append([b(f"{d['emoji']} {d['name']} × {fa(n)}{unit} → +{fa(int(dark_unit_price(k) * C.SELL_RATIO))}", f"sell:d:{k}")])
    text = "🕶 <b>فروش آیتم دارک وب</b>\nهر بار یکی فروخته می‌شه." if rows else "🕶 آیتم دارک وبی نداری."
    rows.append(back("sell", "◀️ فروش"))
    return text, kb(*rows)


async def render_force(u: User):
    rows = []
    for code, f in rules.FORCE_INDEX.items():
        n = u.forces.get(code, 0)
        if n > 0:
            rows.append([b(f"{f['emoji']} {f['name']} × {fa(n)} → +{fa(int(f['price'] * C.SELL_RATIO))}", f"sell:f:{code}")])
    text = ("💂 <b>فروش نیرو</b>\nهر بار یکی فروخته می‌شه (قدرت دفاع/حمله‌ت کم می‌شه)." if rows else "💂 نیرویی نداری.")
    rows.append(back("sell", "◀️ فروش"))
    return text, kb(*rows)


@router.callback_query(F.data == "sell")
async def cb_sell(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_sell)


@router.callback_query(F.data == "sell:gear")
async def cb_gear(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_gear)


@router.callback_query(F.data == "sell:dark")
async def cb_dark(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_dark)


@router.callback_query(F.data == "sell:force")
async def cb_force(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_force)


@router.callback_query(F.data.startswith("sell:g:"))
async def cb_sell_gear(cb: CallbackQuery, user: User) -> None:
    it = rules.SHOP_INDEX.get(cb.data.split(":")[2])
    if not it:
        return await cb.answer()
    pay = int(it["price"] * C.SELL_RATIO)
    r = await eco.col().update_one({"tg_id": user.tg_id, f"gear.{it['key']}": {"$exists": True}},
                                   {"$unset": {f"gear.{it['key']}": ""}, "$inc": {"cash": pay, "invested": -it["price"]}})
    if r.matched_count != 1:
        return await cb.answer("این رو دیگه نداری!", show_alert=True)
    await cb.answer("💰 فروخته شد!")
    await _show(cb, user.tg_id, render_gear, note=f"💰 {it['emoji']} {it['name']} فروخته شد: <b>+{fa(pay)}</b> دلار")


@router.callback_query(F.data.startswith("sell:d:"))
async def cb_sell_dark(cb: CallbackQuery, user: User) -> None:
    key = cb.data.split(":")[2]
    d = rules.DARK_INDEX.get(key)
    if not d:
        return await cb.answer()
    pay = int(dark_unit_price(key) * C.SELL_RATIO)
    r = await eco.col().update_one({"tg_id": user.tg_id, f"items.{key}": {"$gte": 1}},
                                   {"$inc": {f"items.{key}": -1, "cash": pay}})
    if r.matched_count != 1:
        return await cb.answer("این رو دیگه نداری!", show_alert=True)
    await cb.answer("💰 فروخته شد!")
    await _show(cb, user.tg_id, render_dark, note=f"💰 {d['emoji']} {d['name']} فروخته شد: <b>+{fa(pay)}</b> دلار")


@router.callback_query(F.data.startswith("sell:f:"))
async def cb_sell_force(cb: CallbackQuery, user: User) -> None:
    code = cb.data.split(":")[2]
    f = rules.FORCE_INDEX.get(code)
    if not f:
        return await cb.answer()
    pay = int(f["price"] * C.SELL_RATIO)
    r = await eco.col().update_one({"tg_id": user.tg_id, f"forces.{code}": {"$gte": 1}},
                                   {"$inc": {f"forces.{code}": -1, "cash": pay, "invested": -f["price"]}})
    if r.matched_count != 1:
        return await cb.answer("این نیرو رو دیگه نداری!", show_alert=True)
    await cb.answer("💰 فروخته شد!")
    await _show(cb, user.tg_id, render_force, note=f"💰 {f['emoji']} {f['name']} فروخته شد: <b>+{fa(pay)}</b> دلار")
