"""بورس: صفحه‌ی بازار، سهم، پرتفوی، اخبار اقتصادی، خرید/فروش، سود سهام."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

import bourse
import config as C
import eco
import econ
import rules
from db import User
from ui import b, back, edit, kb, pairs, receipt
from util import dur, fa, utcnow

router = Router()


async def _show(cb: CallbackQuery, uid: int, render, *args, note: str = "") -> None:
    u = await eco.get_user(uid)
    text, markup = await render(u, *args)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


def _p(v: float) -> str:
    return fa(int(round(v)))


def _pct(v: float) -> str:
    r = round(v)
    return f"+{fa(r)}٪" if r >= 0 else f"−{fa(-r)}٪"


def _held(u: User, key: str) -> int:
    return max(0, int((u.stocks or {}).get(key, 0)))


# ───────────────────────── صفحه‌ی اصلی بازار ─────────────────────────
async def render_bourse(u: User):
    await bourse.settle_div(u.tg_id)
    u = await eco.get_user(u.tg_id)
    stocks = await bourse.get_stocks()
    ev = econ.current_event()
    lines = ["📈 <b>بورس</b>\n"]
    if ev:
        lines.append(f"📰 <b>اتفاق شهر:</b> {ev['emoji']} {ev['title']}")
        lines.append("   " + "  ".join(f"{C.SECTORS[s][0]}{econ.pct(m)}" for s, m in sorted(ev["income"].items(), key=lambda kv: -kv[1])))
    else:
        lines.append("📰 الان اتفاق خاصی تو شهر نیست؛ بازار آرومه.")
    val = econ.holdings_value(u)
    lines.append(f"\n💼 ارزش سهام تو: <b>{fa(val)}</b>" + (f" | 💰 سود دریافت‌نشده: <b>{fa(u.stock_div)}</b>" if u.stock_div > 0 else ""))
    lines.append("")
    for st in stocks:
        key = st["key"]
        n = _held(u, key)
        ch = bourse.change_pct(st)
        lines.append(f"{bourse.stock_name(key)}  <b>{_p(st['p'])}</b>  {bourse.arrow(ch)} {_pct(ch)}" + (f"  · 💼{fa(n)}" if n else ""))
    lines.append("\n💡 قیمت‌ها با خرید و فروش همه‌ی بازیکن‌ها و اتفاق‌های شهر بالا و پایین می‌شن.")
    btns = [b(f"{bourse.stock_name(st['key'])} {bourse.arrow(bourse.change_pct(st))[-1]}", f"bo:s:{st['key']}") for st in stocks]
    rows = pairs(btns, 2)
    rows.append([b("💼 سهام من", "bo:me"), b("📰 اخبار", "bo:news")])
    if u.stock_div > 0:
        rows.append([b(f"💰 دریافت سود سهام ({fa(u.stock_div)})", "bo:div")])
    rows.append(back("menu", "◀️ منو"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "bo")
async def cb_bo(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_bourse)


# ───────────────────────── صفحه‌ی یه سهم ─────────────────────────
async def render_stock(u: User, key: str):
    st = await bourse.get_stock(key)
    bz = rules.BIZ_INDEX[key]
    sec = C.SECTORS[C.SECTOR_OF[key]]
    p, s = float(st["p"]), float(st["s"])
    ch = bourse.change_pct(st)
    n = _held(u, key)
    lines = [f"{bourse.stock_name(key)} <b>— سهام</b>",
             f"بخش: {sec[0]} {sec[1]}\n",
             f"💵 قیمت هر سهم: <b>{_p(p)}</b>  {bourse.arrow(ch)} {_pct(ch)}",
             f"📊 {bourse.spark(list(st.get('hist') or []) + [p])}  (حدود ۶ ساعت اخیر)"]
    ev = econ.current_event()
    if ev and C.SECTOR_OF[key] in ev["income"]:
        lines.append(f"\n📰 {ev['emoji']} {ev['title']}: درآمد این بخش {econ.pct(ev['income'][C.SECTOR_OF[key]])}")
    if n:
        cost = int((u.stock_cost or {}).get(key, 0))
        value = int(p * n)
        pl = value - cost
        lines.append(f"\n💼 سهم تو: <b>{fa(n)}</b> | میانگین خرید: <b>{_p(cost / n)}</b>")
        lines.append(f"💎 ارزش الان: <b>{fa(value)}</b> | {'🟢 سود' if pl >= 0 else '🔴 زیان'}: <b>{fa(abs(pl))}</b>")
    room = C.STOCK_MAX_HOLD - n
    lines.append(f"\n🏦 سقف مالکیت: {fa(C.STOCK_MAX_HOLD)} سهم | هر معامله حداکثر {fa(bourse.max_trade(s))} | کارمزد {fa(int(C.STOCK_FEE * 100))}٪")
    lines.append(f"💰 سود سهام: حدود {fa(round(C.STOCK_DIV_PER_HOUR * 24 * 100, 1))}٪ در روز (با رونق بخش بیشتر می‌شه)")
    rows = []
    buys = [q for q in (1, 10, 50) if q <= room and q <= bourse.max_trade(s)]
    bb = [b(f"خرید {fa(q)} — {fa(bourse.buy_cost(p, s, q))}", f"bo:b:{key}:{q}") for q in buys]
    if room > 0:
        bb.append(b("خرید حداکثر", f"bo:b:{key}:max"))
    rows += pairs(bb, 2)
    if n:
        sells = [q for q in (1, 10, 50) if q < n and q <= bourse.max_trade(s)]
        sb = [b(f"فروش {fa(q)} — {fa(bourse.sell_gain(p, s, q))}", f"bo:x:{key}:{q}") for q in sells]
        sb.append(b("فروش همه", f"bo:x:{key}:all"))
        rows += pairs(sb, 2)
    rows.append([b("🔄 به‌روزرسانی", f"bo:s:{key}")])
    rows.append(back("bo", "◀️ بورس"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data.startswith("bo:s:"))
async def cb_stock(cb: CallbackQuery, user: User) -> None:
    key = cb.data.split(":")[2]
    if key not in rules.BIZ_INDEX or not await bourse.get_stock(key):
        return await cb.answer("این سهم وجود نداره!", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_stock, key)


# ───────────────────────── خرید و فروش ─────────────────────────
_ERR = {
    "bad": "این سهم وجود نداره!",
    "hold": f"به سقف مالکیت ({fa(C.STOCK_MAX_HOLD)} سهم) می‌رسی!",
    "cash": "💸 پول کافی نداری!",
    "held": "این‌قدر سهم نداری!",
    "race": "همین الان یکی دیگه قیمت رو عوض کرد؛ دوباره بزن.",
}


def _err(code: str, info: dict) -> str:
    if code == "big":
        return f"هر معامله حداکثر {fa(info.get('max', 1))} سهم می‌تونه باشه."
    return _ERR.get(code, "خطا!")


@router.callback_query(F.data.startswith("bo:b:"))
async def cb_buy(cb: CallbackQuery, user: User) -> None:
    parts = cb.data.split(":")
    key, qs = parts[2], parts[3]
    st = await bourse.get_stock(key) if key in rules.BIZ_INDEX else None
    if not st:
        return await cb.answer(_ERR["bad"], show_alert=True)
    if qs == "max":
        q = bourse.max_affordable(float(st["p"]), float(st["s"]), user.cash, C.STOCK_MAX_HOLD - _held(user, key))
        if q <= 0:
            return await cb.answer(_ERR["cash"] if C.STOCK_MAX_HOLD - _held(user, key) > 0 else _ERR["hold"], show_alert=True)
    else:
        try:
            q = int(qs)
        except ValueError:
            return await cb.answer()
    ok, code, info = await bourse.buy(user.tg_id, key, q)
    if not ok:
        return await cb.answer(_err(code, info), show_alert=True)
    await cb.answer(f"✅ {fa(q)} سهم خریدی!")
    await receipt(cb, f"📈 {fa(q)} سهم {bourse.stock_name(key)} — {fa(info['cost'])} دلار")
    await _show(cb, user.tg_id, render_stock, key, note=f"✅ <b>خرید انجام شد</b> — {fa(q)} سهم به قیمت کل {fa(info['cost'])}")


@router.callback_query(F.data.startswith("bo:x:"))
async def cb_sell(cb: CallbackQuery, user: User) -> None:
    parts = cb.data.split(":")
    key, qs = parts[2], parts[3]
    st = await bourse.get_stock(key) if key in rules.BIZ_INDEX else None
    if not st:
        return await cb.answer(_ERR["bad"], show_alert=True)
    held = _held(user, key)
    if held <= 0:
        return await cb.answer(_ERR["held"], show_alert=True)
    if qs == "all":
        q = min(held, bourse.max_trade(float(st["s"])))
    else:
        try:
            q = int(qs)
        except ValueError:
            return await cb.answer()
    ok, code, info = await bourse.sell(user.tg_id, key, q)
    if not ok:
        return await cb.answer(_err(code, info), show_alert=True)
    pl = info["gain"] - info["basis"]
    await cb.answer(f"✅ {fa(q)} سهم فروختی!")
    note = (f"✅ <b>فروش انجام شد</b> — {fa(q)} سهم، دریافتی {fa(info['gain'])} | "
            f"{'🟢 سود' if pl >= 0 else '🔴 زیان'}: {fa(abs(pl))}")
    if qs == "all" and q < held:
        note += f"\n(هر معامله حداکثر {fa(q)} سهمه؛ دوباره بزن تا بقیه هم فروخته بشه)"
    await _show(cb, user.tg_id, render_stock, key, note=note)


# ───────────────────────── سهام من ─────────────────────────
async def render_me(u: User):
    held = [(k, n) for k, n in (u.stocks or {}).items() if n > 0 and k in rules.BIZ_INDEX]
    if not held:
        return ("💼 <b>سهام من</b>\n\nهنوز سهمی نداری. از بورس یه سهم بخر! 👇",
                kb([b("📈 بورس", "bo")], back("menu", "◀️ منو")))
    lines = ["💼 <b>سهام من</b>\n"]
    total_v = total_c = 0
    rows = []
    for k, n in held:
        p = econ.PRICES.get(k, 0.0)
        cost = int((u.stock_cost or {}).get(k, 0))
        value = int(p * n)
        total_v += value
        total_c += cost
        pct_ = (value / cost - 1) * 100 if cost else 0.0
        lines.append(f"{bourse.stock_name(k)}: {fa(n)} سهم × {_p(p)} = <b>{fa(value)}</b> ({bourse.arrow(pct_)} {_pct(pct_)})")
        rows.append(b(bourse.stock_name(k), f"bo:s:{k}"))
    pl = total_v - total_c
    lines.append(f"\n💎 جمع ارزش: <b>{fa(total_v)}</b> | {'🟢 سود' if pl >= 0 else '🔴 زیان'}: <b>{fa(abs(pl))}</b>")
    if u.stock_div > 0:
        lines.append(f"💰 سود دریافت‌نشده: <b>{fa(u.stock_div)}</b>")
    out = pairs(rows, 2)
    if u.stock_div > 0:
        out.append([b(f"💰 دریافت سود سهام ({fa(u.stock_div)})", "bo:div")])
    out.append(back("bo", "◀️ بورس"))
    return "\n".join(lines), kb(*out)


@router.callback_query(F.data == "bo:me")
async def cb_me(cb: CallbackQuery, user: User) -> None:
    await bourse.settle_div(user.tg_id)
    await cb.answer()
    await _show(cb, user.tg_id, render_me)


# ───────────────────────── سود سهام ─────────────────────────
@router.callback_query(F.data == "bo:div")
async def cb_div(cb: CallbackQuery, user: User) -> None:
    amount = await bourse.claim_div(user.tg_id)
    if amount <= 0:
        return await cb.answer("هنوز سودی جمع نشده.", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_bourse, note=f"💰 <b>+{fa(amount)}</b> دلار سود سهام اومد تو جیبت!")


# ───────────────────────── اخبار ─────────────────────────
async def render_news(u: User):
    now = utcnow()
    ev = econ.current_event(now)
    lines = ["📰 <b>اخبار اقتصادی شهر</b>\n"]
    if ev:
        lines.append("<b>الان:</b>\n" + econ.describe_event(ev, now))
        eff = econ.current_effect(u, now)
        if u.businesses:
            lines.append(f"\n🏪 اثر روی درآمد کاسبی خودت: <b>{econ.pct(eff)}</b>")
    else:
        lines.append("الان اتفاق خاصی نیست. اتفاق بعدی هر لحظه ممکنه برسه!")
    past = [e for e in reversed(econ.events()) if e["end"] <= now][:5]
    if past:
        lines.append("\n<b>اتفاق‌های اخیر:</b>")
        for e in past:
            lines.append(f"{e['emoji']} {e['title']} — {dur((now - e['start']).total_seconds())} پیش")
    lines.append("\n💡 هر اتفاق تا پایانش درآمد کاسبی بخش‌های درگیر رو بالا یا پایین می‌بره و قیمت سهامشون رو هم تکون می‌ده.")
    return "\n".join(lines), kb([b("📈 بورس", "bo")], back("menu", "◀️ منو"))


@router.callback_query(F.data == "bo:news")
async def cb_news(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_news)
