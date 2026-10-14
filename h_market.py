"""بازار: فروشگاه (اسلحه، سپر، ابزار، امنیت) و دارک وب."""
from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

import config as C
import eco
import rules
from db import User
from ui import b, back, edit, kb, receipt
from util import fa, utcnow

router = Router()


async def _show(cb: CallbackQuery, uid: int, render, *args, note: str = "") -> None:
    u = await eco.get_user(uid)
    text, markup = await render(u, *args)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


async def render_market(u: User):
    return ("🛒 <b>بازار</b>\n\n🛍 <b>فروشگاه</b>: اسلحه، سپر، ابزار دزدی و امنیت خونه\n"
            "🕶 <b>دارک وب</b>: جن، جنگیر، طلسم‌ها و دزد گاوصندوق\n"
            "🛡 <b>ضدزورگیری</b>: مخصوص بازیکن‌های ضعیف (حفاظت در برابر قوی‌ترها)"), kb(
        [b("🛍 فروشگاه", "mk:shop"), b("🕶 دارک وب", "mk:dark")],
        [b("🛡 ضدزورگیری", "mk:antirob")], back("menu", "◀️ منو"))


@router.callback_query(F.data == "mk")
async def cb_market(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_market)


async def render_shop(u: User):
    return "🛍 <b>فروشگاه</b>\n\nیه دسته انتخاب کن:", kb(
        [b(f"{c['emoji']} {c['name']}", f"mk:cat:{cat}") for cat, c in list(C.SHOP.items())[:2]],
        [b(f"{c['emoji']} {c['name']}", f"mk:cat:{cat}") for cat, c in list(C.SHOP.items())[2:]],
        back("mk", "◀️ بازار"))


@router.callback_query(F.data == "mk:shop")
async def cb_shop(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_shop)


async def render_cat(u: User, cat: str):
    c = C.SHOP[cat]
    best = rules.best(u.gear, cat)
    hint = {"weapon": "⚔️ توی حمله می‌شه تا ۲ اسلحه رو ترکیب کرد (+۱۵٪).",
            "shield": "🛡 سپر قوی‌ترین خط دفاع توئه.",
            "tool": "🗡 ابزار دزدی به قدرت حمله اضافه می‌شه.",
            "security": "🛡 امنیت خونه به قدرت دفاع اضافه می‌شه."}[cat]
    text = f"{c['emoji']} <b>{c['name']}</b>\n{hint}\n<i>از هر دسته فقط قوی‌ترین آیتمت اثر داره.</i>"
    if best:
        text += f"\n\n⭐ فعلی: {best['emoji']} {best['name']} ({fa(best['power'])})"
    rows = []
    for it in c["items"]:
        mark = "✅ " if it["key"] in u.gear else ""
        rows.append([b(f"{mark}{it['emoji']} {it['name']} · {c['stat']}{fa(it['power'])} · {fa(it['price'])}", f"mk:buy:{it['key']}")])
    rows.append(back("mk:shop", "◀️ دسته‌ها"))
    return text, kb(*rows)


@router.callback_query(F.data.regexp(r"^mk:cat:(weapon|shield|tool|security)$"))
async def cb_cat(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_cat, cb.data.split(":")[2])


@router.callback_query(F.data.startswith("mk:buy:"))
async def cb_buy(cb: CallbackQuery, user: User) -> None:
    it = rules.SHOP_INDEX.get(cb.data.split(":")[2])
    if not it:
        return await cb.answer()
    if it["key"] in user.gear:
        return await cb.answer("این رو داری!", show_alert=True)
    if not await eco.spend(user.tg_id, it["price"], set_={f"gear.{it['key']}": 1},
                           flt={f"gear.{it['key']}": {"$exists": False}}, invest=it["price"]):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer(f"✅ خرید انجام شد!\n{it['emoji']} {it['name']} — {fa(it['price'])} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_cat, it["cat"], note=f"✅ <b>خرید انجام شد</b> — {it['emoji']} {it['name']} رفت تو کمدت!")


# ───────────────────────── دارک وب ─────────────────────────
async def render_dark(u: User):
    lines = ["🕶 <b>دارک وب</b>\n"]
    rows = []
    for d in rules.DARK_INDEX.values():
        n = u.items.get(d["key"], 0)
        have = f" — داری: {fa(n)}" if n else ""
        lines.append(f"{d['emoji']} <b>{d['name']}</b> — {fa(d['price'])}{have}\n   {d['desc']}")
        rows.append([b(f"{d['emoji']} {d['name']} — {fa(d['price'])}", f"mk:dbuy:{d['key']}")])
    lines.append("\n🎯 استفاده: از «کمد» یا ریپلای روی طرف: <code>حمله اجنه</code> · <code>دزد</code> · <code>طلسم عاشقی</code> · <code>طلسم طلاق</code>")
    rows.append(back("mk", "◀️ بازار"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "mk:dark")
async def cb_dark(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_dark)


@router.callback_query(F.data.startswith("mk:dbuy:"))
async def cb_dbuy(cb: CallbackQuery, user: User) -> None:
    d = rules.DARK_INDEX.get(cb.data.split(":")[2])
    if not d:
        return await cb.answer()
    qty = C.JINNGIR_CHARGES if d["key"] == "jinngir" else 1
    if not await eco.spend(user.tg_id, d["price"], inc={f"items.{d['key']}": qty}):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer(f"✅ خرید انجام شد!\n{d['emoji']} {d['name']} — {fa(d['price'])} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_dark, note=f"✅ <b>خرید انجام شد</b> — {d['emoji']} {d['name']} خریدی (کمدت رو ببین)")


# ───────────────────────── ضدزورگیری ─────────────────────────
async def is_weak(u: User) -> bool:
    """ضعیف = حداقل یه بازیکن فعال هست که ۵ سطح (یا بیشتر) از تو بالاتره."""
    thr = rules.xp_for_level(rules.level_of(u.xp) + C.WEAK_GAP)
    since = utcnow() - timedelta(days=C.ACTIVE_DAYS_FOR_WEAK)
    return await eco.col().count_documents({"tg_id": {"$ne": u.tg_id}, "xp": {"$gte": thr}, "last_seen": {"$gte": since}}, limit=1) == 1


async def render_antirob(u: User):
    lvl = rules.level_of(u.xp)
    text = (f"🛡 <b>ضدزورگیری</b>\n\n⭐ سطح تو: <b>{fa(lvl)}</b>\n\n"
            f"بازیکن‌های قوی‌تر (حداقل {fa(C.WEAK_GAP)} سطح بالاتر از تو) دیگه نمی‌تونن بهت حمله، هک، دزدی از خونه، "
            "دزد گاوصندوق، جن یا طلسم بفرستن. با بالا رفتن سطحت اختلاف کم می‌شه و حفاظت لازم نیست.\n"
            f"⚠️ فقط <b>ضعیف‌ها</b> (حداقل {fa(C.WEAK_GAP)} سطح پایین‌تر از یه بازیکن فعال) می‌تونن بخرن و هر نفر فقط <b>یکی</b>.\n\n"
            f"💵 قیمت: <b>{fa(C.ANTIROB_PRICE)}</b>")
    rows = []
    if u.antirob:
        text += "\n\n✅ <b>ضدزورگیری داری</b>؛ فعاله."
    elif await is_weak(u):
        rows.append([b(f"🛡 خرید ضدزورگیری — {fa(C.ANTIROB_PRICE)}", "mk:antirob:buy")])
    else:
        text += "\n\n🚫 الان ضعیف حساب نمی‌شی (بازیکن فعالی که ۵ سطح از تو بالاتر باشه نیست)؛ نمی‌تونی بخری."
    rows.append(back("mk", "◀️ بازار"))
    return text, kb(*rows)


@router.callback_query(F.data == "mk:antirob")
async def cb_antirob(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_antirob)


@router.callback_query(F.data == "mk:antirob:buy")
async def cb_antirob_buy(cb: CallbackQuery, user: User) -> None:
    u = await eco.get_user(user.tg_id)
    if u.antirob:
        return await cb.answer("ضدزورگیری داری! (هر نفر فقط یکی)", show_alert=True)
    if not await is_weak(u):
        return await cb.answer("🚫 فقط ضعیف‌ها (۵ سطح پایین‌تر از یه بازیکن فعال) می‌تونن بخرن.", show_alert=True)
    if not await eco.spend(user.tg_id, C.ANTIROB_PRICE, set_={"antirob": True}, flt={"antirob": {"$ne": True}}):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer("✅ خرید انجام شد!")
    await receipt(cb, f"🛡 ضدزورگیری — {fa(C.ANTIROB_PRICE)} دلار")
    await _show(cb, user.tg_id, render_antirob, note="✅ <b>خرید انجام شد</b> — 🛡 ضدزورگیری فعال شد!")
