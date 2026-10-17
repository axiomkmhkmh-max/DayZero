"""اتحاد: ساخت، پیوستن، بانک مشترک، ارتقا + ارتش (خرید، تولید، کسب درآمد/ماموریت، دفاع) + جنگ اتحادها.

جنگ: «حمله ۲۰ تانک ابرامز و ۲۰ ارپیجی زن» (ریپلای روی طرف). مدافع تا WAR_REPLY_SEC ثانیه می‌تونه با
«حمله ۲۰ تانک t72» از ذخیره‌اش نیرو اضافه کنه؛ پادگان و پدافند و سنگر هم همیشه خودکار دفاع می‌کنن.
منطق نبرد تو war.py ـه."""
from __future__ import annotations

import asyncio
import re
import secrets
from datetime import timedelta
from typing import Optional

import structlog
from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import combat
import config as C
import eco
import rules
import war
from db import Alliance, Battle, User
from h_target import ask_target
from states import Ask
from redis_service import LockTimeout, redis_service
from ui import b, back, edit, in_group, kb, reply_owned, replying_to_bot
from util import dur, esc, fa, left, mention, norm, utcnow

router = Router()
log = structlog.get_logger("alliance")
UNITS = war.UNITS


async def members_of(aid: int) -> list[User]:
    return await User.find(User.alliance_id == aid).sort("-invested").to_list()


# ───────────────────────── ظرفیت / تولید / ماموریت ─────────────────────────
def used_supply(u: User) -> int:
    n = war.supply(u.army) + war.supply(u.garrison) + war.supply(u.fort)
    for m in (u.mission or {}).values():
        n += war.supply(m.get("u", {}))
    p = u.prod or {}
    if p.get("key") in UNITS and not p.get("al"):
        n += UNITS[p["key"]]["sup"] * int(p.get("n", 0))
    return n


def cap_of(u: User, al: Optional[Alliance]) -> int:
    return war.capacity(u.house, al.level if al else 1)


async def armory_used(al: Alliance) -> int:
    n = war.supply(al.armory)
    lead = await eco.get_user(al.leader_id)
    p = (lead.prod if lead else {}) or {}
    if p.get("al") and p.get("key") in UNITS:
        n += UNITS[p["key"]]["sup"] * int(p.get("n", 0))
    return n


async def settle_army(uid: int) -> list[str]:
    """تولید، ماموریت و سفرهای تموم‌شده رو تحویل می‌ده (تنبل: هر بار که پنل/حمله باز می‌شه)."""
    u = await eco.get_user(uid)
    notes: list[str] = []
    if u is None:
        return notes
    now = utcnow()
    p = u.prod or {}
    if p.get("key") in UNITS and p.get("end") and p["end"] <= now:
        k, n = p["key"], int(p.get("n", 0))
        if p.get("al") and u.alliance_id:
            r = await eco.col().update_one({"tg_id": uid, "prod.end": p["end"]}, {"$set": {"prod": {}}})
            if r.matched_count == 1:
                await Alliance.get_motor_collection().update_one({"aid": u.alliance_id}, {"$inc": {f"armory.{k}": n}})
                notes.append(f"🏭 تولید تموم شد: {war.label(k, n)} به انبار اتحاد رفت.")
        else:
            dest = "fort" if UNITS[k]["kind"] == "fort" else "army"
            r = await eco.col().update_one({"tg_id": uid, "prod.end": p["end"]}, {"$set": {"prod": {}}, "$inc": {f"{dest}.{k}": n}})
            if r.matched_count == 1:
                notes.append(f"🏭 تولید تموم شد: {war.label(k, n)} تحویل داده شد.")
    for cat, m in list((u.mission or {}).items()):
        if not m.get("end") or m["end"] > now:
            continue
        inc: dict = {"cash": int(m.get("reward", 0))}
        back_units = {k: n - int(m.get("lost", {}).get(k, 0)) for k, n in m.get("u", {}).items()}
        for k, n in back_units.items():
            if n > 0:
                inc[f"army.{k}"] = n
        r = await eco.col().update_one({"tg_id": uid, f"mission.{cat}.end": m["end"]}, {"$unset": {f"mission.{cat}": ""}, "$inc": inc})
        if r.matched_count == 1:
            lost = war.units_text(m.get("lost", {}))
            notes.append(f"🎖 ماموریت {C.MIL_CATS[cat][1]} تموم شد: +{fa(m.get('reward', 0))} دلار"
                         + (f" (تلفات: {lost})" if m.get("lost") else ""))
            if u.alliance_id and m.get("tax"):
                await Alliance.get_motor_collection().update_one({"aid": u.alliance_id}, {"$inc": {"bank": int(m["tax"])}})
    for kind in ("sea", "arms"):
        d = (u.inc or {}).get(kind)
        if not d or not d.get("end") or d["end"] > now:
            continue
        amt, ret = int(d.get("amt", 0)), int(d.get("ret", 0))
        r = await eco.col().update_one({"tg_id": uid, f"inc.{kind}.end": d["end"]}, {"$unset": {f"inc.{kind}": ""}, "$inc": {"cash": ret}})
        if r.matched_count != 1:
            continue
        if kind == "sea":
            notes.append((f"🏴‍☠️ دزدای دریایی به کاروان حمله کردن! از {fa(amt)} فقط {fa(ret)} برگشت." if d.get("bad")
                          else f"🚢 تجارت دریایی تموم شد: {fa(amt)} ← {fa(ret)} دلار (+{fa(ret - amt)})"))
        else:
            notes.append((f"🚔 پلیس محموله‌ی اسلحه رو گرفت! {fa(amt)} دلار سرمایه از دست رفت." if d.get("bad")
                          else f"🔫 محموله‌ی اسلحه فروخته شد: {fa(amt)} ← {fa(ret)} دلار (+{fa(ret - amt)})"))
        if ret > amt and u.alliance_id:
            await Alliance.get_motor_collection().update_one({"aid": u.alliance_id}, {"$inc": {"bank": int((ret - amt) * C.DEAL_AL_TAX)}})
    return notes


async def fresh(uid: int) -> tuple[User, list[str]]:
    notes = await settle_army(uid)
    return await eco.get_user(uid), notes


async def _show(cb: CallbackQuery, uid: int, note: str = "") -> None:
    await _page(cb, uid, render_al, note=note)


async def _page(cb: CallbackQuery, uid: int, render, *args, note: str = "") -> None:
    u, notes = await fresh(uid)
    text, markup = await render(u, *args)
    pre = "\n".join([n for n in [note, *notes] if n])
    await edit(cb, (pre + "\n\n" if pre else "") + text, markup)


# ───────────────────────── صفحه‌ی اصلی ─────────────────────────
async def render_al(u: User):
    al = await eco.alliance_of(u)
    if al is None:
        text = (f"🤝 <b>اتحاد</b>\n\nتو عضو هیچ اتحادی نیستی.\n\n"
                f"با دوستات اتحاد بساز: بانک مشترک، بونوس درآمد کاسبی، <b>ارتش</b> (تانک، جنگنده، تک‌تیرانداز...)، چاه نفت، تجارت دریایی و جنگ با اتحادهای دیگه!\n"
                f"💵 هزینه‌ی ساخت: <b>{fa(C.ALLIANCE_CREATE)}</b>\n\nیا از «لیست اتحادها» درخواست عضویت بده؛ رهبر تاییدت می‌کنه.")
        return text, kb([b("➕ ساخت اتحاد", "al:create"), b("📜 لیست اتحادها", "al:list")], back("menu", "◀️ منو"))
    cap, bonus, _ = C.ALLIANCE_LEVELS[al.level]
    n = await User.find(User.alliance_id == al.aid).count()
    leader = u.tg_id == al.leader_id
    garr = war.merge(u.garrison, u.fort)
    text = (f"🤝 <b>{esc(al.name)}</b> — سطح {fa(al.level)}\n\n👥 اعضا: {fa(n)}/{fa(cap)}   "
            f"📈 بونوس درآمد: <b>+{int(bonus * 100)}٪</b>\n🏦 بانک اتحاد: <b>{fa(al.bank)}</b>\n"
            f"🏛 انبار اتحاد: {fa(war.supply(al.armory))}/{fa(C.ARMORY_CAP_PER_LEVEL * al.level)} جا\n\n"
            f"⚔️ <b>ارتش تو</b>  (ظرفیت {fa(used_supply(u))}/{fa(cap_of(u, al))})\n"
            f"📦 ذخیره: {fa(war.supply(u.army))} جا · 🛡 پادگان: {fa(war.supply(u.garrison))} جا · 🧱 پدافند: {fa(war.supply(u.fort))} جا\n"
            f"قدرت دفاع خودکار (ضربه/استقامت):\n{war.power_line(garr)}\n\n"
            "هم‌اتحادی‌ها نمی‌تونن به هم حمله کنن. به اتحادهای دیگه می‌تونی حمله‌ی نظامی کنی!")
    rows = [[b("🎖 نیروهای من", "al:forces")],
            [b("💰 کسب درآمد", "al:inc"), b("🏭 تولید", "al:uc:p")],
            [b("🛒 خرید نیرو", "al:uc:b"), b("🛡 دفاع", "al:def")],
            [b("⚔️ حمله", "al:war"), b("🏛 انبار اتحاد", "al:arm")],
            [b("🏦 بانک اتحاد", "al:dep"), b("👥 اعضا", "al:members")]]
    if leader:
        rows.append([b(f"📨 درخواست‌ها ({fa(len(al.requests))})", "al:req"), b("🔗 لینک دعوت", "al:link")])
        if al.level < max(C.ALLIANCE_LEVELS):
            cost = C.ALLIANCE_LEVELS[al.level + 1][2]
            rows.append([b(f"⬆️ ارتقا به سطح {fa(al.level + 1)} — {fa(cost)} (از جیب رهبر)", "al:up")])
    rows.append([b("📖 راهنما", "al:help"), b("🚪 خروج از اتحاد", "al:leave")])
    rows.append(back("menu", "◀️ منو"))
    return text, kb(*rows)


@router.callback_query(F.data == "al")
async def cb_al(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await _show(cb, user.tg_id)


GUIDE = """📖 <b>راهنمای ارتش اتحاد</b>
(🎖 «نیروهای من» همه‌ی یگان‌هات و قدرت کلت رو نشون می‌ده؛ تو گروه بنویس «ارتش».)

🔺 <b>سه قدرت</b>: 🪖 انسانی · ✈️ هوایی · 🚜 زرهی. هر قدرت با قدرتِ هم‌نوعش رو‌به‌رو می‌شه.
هر یگان دو چیز داره: <b>با چی کشته می‌شه</b> و <b>با چی می‌زنه</b>. مثلاً 🚀 ارپیجی‌زن پیاده‌ست (باید با قدرت انسانی بکشیش) ولی با قدرت <b>زرهی</b> می‌زنه (تانک‌ها رو نابود می‌کنه). 🛻 نفربر زرهیه ولی پیاده‌ها رو می‌زنه، 🛰 ضدهوایی زرهیه ولی هواپیما می‌زنه.

🛒 <b>خرید</b>: فوری، قیمت کامل. 🏭 <b>تولید</b>: ۳۵٪ ارزون‌تر ولی زمان می‌بره (هر سطح اتحاد ۵٪ سریع‌تر).
💰 <b>کسب درآمد</b>: نیروهای ذخیره‌ات رو بفرست ماموریت؛ با پاداش برمی‌گردن (شاید چندتاشون تلف بشن).
📦 <b>ذخیره</b> = برای حمله و مقابله. 🛡 <b>پادگان</b> = خودکار دفاع می‌کنه. 🧱 <b>پدافند و سنگر</b> = همیشه خودکار دفاع می‌کنه.

🏦 <b>بانک اتحاد</b>: اعضا واریز می‌کنن؛ فقط رهبر خرج یا برداشت می‌کنه (خرید/تولید نیرو با دکمه‌های 🏦). نیروها می‌رن 🏛 <b>انبار اتحاد</b> و رهبر با <code>تحویل ۱۰ تانک ابرامز</code> (ریپلای روی عضو) بهشون می‌ده. ارتقای سطح از جیب رهبره.
👑 <b>رهبر</b>: درخواست‌های عضویت رو تایید/رد می‌کنه یا 🔗 لینک دعوت می‌سازه.
🛢 <b>چاه نفت</b>، 🚢 <b>تجارت دریایی</b> و 🔫 <b>قاچاق اسلحه</b> هم تو «کسب درآمد» هست.

⚔️ <b>حمله</b> (فقط به اعضای اتحادهای دیگه) — روی پیام طرف ریپلای کن:
<code>حمله ۲۰ تانک ابرامز و ۲۰ ارپیجی زن</code>
🛡 مدافع ۶۰ ثانیه وقت داره از ذخیره‌اش بنویسه: <code>حمله ۲۰ تانک t72</code> (اگه ننویسه، پادگان و پدافندش خودکار جواب می‌دن).
🏆 هر طرف ارزش بیشتری از دشمن نابود کنه برنده‌ست: غنیمت پولی + یه کارگر از کاسبی بازنده + یگان‌های نابودشده. بازنده فقط حدود نصف تلفات رو می‌ده.
نمونه نوشتن: <code>همه تانک t72</code> · <code>5 جنگنده f14 و 2 بمب افکن b52</code>"""


@router.callback_query(F.data == "al:help")
async def cb_help(cb: CallbackQuery) -> None:
    await cb.answer()
    await edit(cb, GUIDE, kb(back("al", "◀️ اتحاد")))


# ───────────────────────── کارت یگان / خرید / تولید ─────────────────────────
def _cls_names(d: dict) -> str:
    return " + ".join(f"{C.MIL_CLASSES[c][0]} {C.MIL_CLASSES[c][1]}" for c in d)


def unit_card(k: str) -> str:
    u = UNITS[k]
    return (f"{u['emoji']} <b>{u['name']}</b>\n💵 قیمت: {fa(u['price'])} · جای اشغالی: {fa(u['sup'])}\n"
            f"💥 ضربه: <b>{fa(u['atk'])}</b> تو میدان {_cls_names(u['tgt'])}\n"
            f"❤️ استقامت: <b>{fa(u['hp'])}</b> — برای کشتنش باید با قدرت {_cls_names({u['dcls']: 1})} بزنی"
            + ("\n🧱 ثابته: به حمله نمی‌ره و همیشه خودکار دفاع می‌کنه." if u["kind"] == "fort" else ""))


async def render_cats(u: User, mode: str):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    title = "🛒 <b>خرید نیرو</b> (فوری، قیمت کامل)" if mode == "b" else "🏭 <b>تولید</b> (۳۵٪ ارزون‌تر، زمان‌بر)"
    text = f"{title}\n\n💵 جیب: {fa(u.cash)} · ظرفیت: {fa(used_supply(u))}/{fa(cap_of(u, al))}"
    p = u.prod or {}
    if mode == "p":
        if p.get("key") in UNITS:
            text += f"\n\n⏳ در حال تولید: {war.label(p['key'], p['n'])} — {dur(left(p['end']))} دیگه"
        else:
            text += "\n\nخط تولید خالیه؛ یه نوع یگان انتخاب کن."
    rows = [[b(f"{C.MIL_CATS[c][0]} {C.MIL_CATS[c][1]}", f"al:uk:{mode}:{c}")] for c in war.CATS]
    rows.append(back("al", "◀️ اتحاد"))
    return text, kb(*rows)


@router.callback_query(F.data.regexp(r"^al:uc:[bp]$"))
async def cb_cats(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_cats, cb.data.split(":")[2])


async def render_list(u: User, mode: str, cat: str):
    mult = C.MIL_PROD_PRICE if mode == "p" else 1.0
    lines = [f"{C.MIL_CATS[cat][0]} <b>{C.MIL_CATS[cat][1]}</b>\n"]
    rows = []
    for k in [k for k, x in UNITS.items() if x["cat"] == cat]:
        x = UNITS[k]
        lines.append(f"{x['emoji']} {x['name']} — {fa(x['price'] * mult)} — 💥{fa(x['atk'])} ❤️{fa(x['hp'])}")
        rows.append([b(f"{x['emoji']} {x['name']} — {fa(x['price'] * mult)}", f"al:uu:{mode}:{k}")])
    rows.append(back(f"al:uc:{mode}", "◀️ دسته‌ها"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data.regexp(r"^al:uk:[bp]:\w+$"))
async def cb_list_units(cb: CallbackQuery, user: User) -> None:
    _, _, mode, cat = cb.data.split(":")
    if cat not in war.CATS:
        return await cb.answer()
    await cb.answer()
    await _page(cb, user.tg_id, render_list, mode, cat)


async def render_unit(u: User, mode: str, k: str):
    base = "p" if mode in "pq" else "b"
    x = UNITS[k]
    price = int(x["price"] * (C.MIL_PROD_PRICE if base == "p" else 1.0))
    al = await eco.alliance_of(u)
    leader = bool(al and al.leader_id == u.tg_id)
    have = u.fort.get(k, 0) if x["kind"] == "fort" else u.army.get(k, 0) + u.garrison.get(k, 0)
    text = (unit_card(k) + f"\n\n{'🏭 قیمت تولید' if base == 'p' else '🛒 قیمت خرید'}: <b>{fa(price)}</b>"
            + (f" · ⏳ {dur(war.prod_seconds(k, 1, al.level if al else 1))} برای هر عدد" if base == "p" else "")
            + f"\n🎒 داری: {fa(have)}\n💵 جیب: {fa(u.cash)} · ظرفیت: {fa(used_supply(u))}/{fa(cap_of(u, al))}")
    verb = "تولید" if base == "p" else "خرید"
    rows = [[b(f"{verb} {fa(n)}", f"al:uq:{base}:{k}:{n}") for n in (1, 5, 10)],
            [b(f"{verb} {fa(25)}", f"al:uq:{base}:{k}:25"), b(f"{verb} حداکثر", f"al:uq:{base}:{k}:0")]]
    if leader:
        bm = "q" if base == "p" else "c"
        text += (f"\n\n🏦 بانک اتحاد: {fa(al.bank)} — رهبر می‌تونه از بانک بخره؛ نیرو می‌ره <b>انبار اتحاد</b> "
                 f"({fa(await armory_used(al))}/{fa(C.ARMORY_CAP_PER_LEVEL * al.level)} جا) و بعد با «تحویل» به اعضا می‌رسه.")
        rows.append([b(f"🏦 {verb} با بانک {fa(n)}", f"al:uq:{bm}:{k}:{n}") for n in (1, 5, 10)])
        rows.append([b(f"🏦 {verb} با بانک حداکثر", f"al:uq:{bm}:{k}:0")])
    rows.append(back(f"al:uk:{base}:{x['cat']}", "◀️ لیست"))
    return text, kb(*rows)


@router.callback_query(F.data.regexp(r"^al:uu:[bpcq]:\w+$"))
async def cb_unit(cb: CallbackQuery, user: User) -> None:
    _, _, mode, k = cb.data.split(":")
    if k not in UNITS:
        return await cb.answer()
    await cb.answer()
    await _page(cb, user.tg_id, render_unit, mode, k)


@router.callback_query(F.data.regexp(r"^al:uq:[bpcq]:\w+:\d+$"))
async def cb_unit_buy(cb: CallbackQuery, user: User) -> None:
    _, _, mode, k, n_s = cb.data.split(":")
    if k not in UNITS:
        return await cb.answer()
    u, _ = await fresh(user.tg_id)
    al = await eco.alliance_of(u)
    if not al:
        return await cb.answer("اول عضو اتحاد شو!", show_alert=True)
    from_bank = mode in "cq"
    base = "p" if mode in "pq" else "b"
    if from_bank and al.leader_id != u.tg_id:
        return await cb.answer("👑 فقط رهبر از بانک اتحاد خرج می‌کنه.", show_alert=True)
    x = UNITS[k]
    price = int(x["price"] * (C.MIL_PROD_PRICE if base == "p" else 1.0))
    if from_bank:
        room = (C.ARMORY_CAP_PER_LEVEL * al.level - await armory_used(al)) // x["sup"]
        funds = al.bank
    else:
        room = (cap_of(u, al) - used_supply(u)) // x["sup"]
        funds = u.cash
    n = int(n_s)
    if n == 0:
        n = min(C.MIL_MAX_BUY, room, funds // max(1, price))
    n = min(n, C.MIL_MAX_BUY)
    where = "انبار اتحاد" if from_bank else "پادگان"
    if n <= 0:
        why = f"🏠 ظرفیت {where} پره!" if room <= 0 else ("💸 بانک اتحاد پول کافی نداره!" if from_bank else "💸 پول کافی نداری!")
        return await cb.answer(why, show_alert=True)
    if n > room:
        return await cb.answer(f"🏠 ظرفیت {where} فقط برای {fa(room)} تای دیگه جا داره!", show_alert=True)
    cost = price * n
    alc = Alliance.get_motor_collection()
    if mode == "b":
        dest = "fort" if x["kind"] == "fort" else "army"
        if not await eco.spend(u.tg_id, cost, inc={f"{dest}.{k}": n}):
            return await cb.answer("💸 پول کافی نداری!", show_alert=True)
        note = f"✅ <b>خرید انجام شد</b> — {war.label(k, n)} ({fa(cost)} دلار)"
    elif mode == "c":
        r = await alc.update_one({"aid": al.aid, "bank": {"$gte": cost}}, {"$inc": {"bank": -cost, f"armory.{k}": n}})
        if r.matched_count != 1:
            return await cb.answer("💸 بانک اتحاد پول کافی نداره!", show_alert=True)
        note = f"✅ با پول بانک خریده شد و رفت <b>انبار اتحاد</b> — {war.label(k, n)} ({fa(cost)} دلار)"
    else:
        if mode == "p":
            end = utcnow() + timedelta(seconds=war.prod_seconds(k, n, al.level))
            if not await eco.spend(u.tg_id, cost, set_={"prod": {"key": k, "n": n, "end": end}}, flt={"prod.key": {"$exists": False}}):
                if (await eco.get_user(u.tg_id)).prod.get("key"):
                    return await cb.answer("🏭 خط تولید مشغوله؛ صبر کن تموم بشه.", show_alert=True)
                return await cb.answer("💸 پول کافی نداری!", show_alert=True)
            note = f"🏭 تولید شروع شد: {war.label(k, n)} — {dur(left(end))} دیگه ({fa(cost)} دلار)"
        else:
            if (u.prod or {}).get("key"):
                return await cb.answer("🏭 خط تولید مشغوله؛ صبر کن تموم بشه.", show_alert=True)
            r = await alc.update_one({"aid": al.aid, "bank": {"$gte": cost}}, {"$inc": {"bank": -cost}})
            if r.matched_count != 1:
                return await cb.answer("💸 بانک اتحاد پول کافی نداره!", show_alert=True)
            end = utcnow() + timedelta(seconds=war.prod_seconds(k, n, al.level))
            if not await eco.update_user(u.tg_id, set_={"prod": {"key": k, "n": n, "end": end, "al": True}}, flt={"prod.key": {"$exists": False}}):
                await alc.update_one({"aid": al.aid}, {"$inc": {"bank": cost}})
                return await cb.answer("🏭 خط تولید مشغوله؛ صبر کن تموم بشه.", show_alert=True)
            note = f"🏭 تولید با پول بانک شروع شد: {war.label(k, n)} — {dur(left(end))} دیگه؛ می‌ره انبار اتحاد ({fa(cost)} دلار)"
    await cb.answer("✅")
    await _page(cb, u.tg_id, render_unit, mode, k, note=note)


# ───────────────────────── کسب درآمد (ماموریت) ─────────────────────────
def _cat_units(u: User, cat: str) -> dict[str, int]:
    return {k: n for k, n in war.clean(u.army).items() if UNITS[k]["cat"] == cat}


async def render_missions(u: User):
    lines = ["🎖 <b>ماموریت نظامی</b>\n\nنیروهای <b>ذخیره</b> رو بفرست ماموریت؛ بعد از تموم شدن با پاداش برمی‌گردن "
             f"(+{int(C.MIL_MISSION_AL_TAX * 100)}٪ هم به بانک اتحاد می‌ره). تو ماموریت نمی‌تونن حمله یا مقابله کنن.\n"]
    rows = []
    for cat in ("human", "armor", "air"):
        emo, name = C.MIL_CATS[cat]
        m = (u.mission or {}).get(cat)
        have = _cat_units(u, cat)
        if m:
            lines.append(f"{emo} {name}: ⏳ تو ماموریت — {dur(left(m['end']))} دیگه (+{fa(m['reward'])})")
        elif have:
            lines.append(f"{emo} {name}: {war.units_text(have)}")
            rows.append([b(f"{emo} فرستادن {name}", f"al:ms:{cat}")])
        else:
            lines.append(f"{emo} {name}: نیروی ذخیره نداری")
    rows.append(back("al:inc", "◀️ کسب درآمد"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "al:mis")
async def cb_missions(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_missions)


async def render_income(u: User):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    text = ("💰 <b>کسب درآمد</b>\n\n🎖 <b>ماموریت نظامی</b>: نیروهای ذخیره رو بفرست ماموریت.\n"
            "🚢 <b>تجارت دریایی</b>: سرمایه بذار، کاروان بفرست؛ ریسکش دزدای دریاییه.\n"
            "🔫 <b>قاچاق اسلحه</b>: ریسک بالا، سود بالا؛ اگه پلیس بگیره همه‌چی می‌ره.\n"
            "🛢 <b>چاه نفت</b>: چاه بخر و هر ساعت درآمد غیرفعال بگیر.\n\n"
            f"💵 جیب: {fa(u.cash)} · سقف سرمایه‌ی هر سفر (سطح اتحاد): {fa(C.INVEST_CAP[al.level])}")
    return text, kb([b("🎖 ماموریت نظامی", "al:mis"), b("🛢 چاه نفت", "al:oil")],
                    [b("🚢 تجارت دریایی", "al:dl:sea"), b("🔫 قاچاق اسلحه", "al:dl:arms")], back("al", "◀️ اتحاد"))


@router.callback_query(F.data == "al:inc")
async def cb_income(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_income)


# ───────────────────────── تجارت دریایی / قاچاق اسلحه ─────────────────────────
def _routes(kind: str):
    return C.SEA_ROUTES if kind == "sea" else C.ARMS_ROUTES


def _chance(kind: str, cfg, level: int) -> float:
    """sea: شانس حمله‌ی دزدا ؛ arms: شانس موفقیت."""
    if kind == "sea":
        return max(0.02, cfg[4] - C.DEAL_AL_BONUS * (level - 1))
    return min(0.9, cfg[4] + 2 * C.DEAL_AL_BONUS * (level - 1))


async def render_deal(u: User, kind: str):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    cap = C.INVEST_CAP[al.level]
    d = (u.inc or {}).get(kind)
    if kind == "sea":
        head = "🚢 <b>تجارت دریایی</b>\n\nسرمایه‌ات رو با کاروان می‌فرستی؛ برمی‌گرده با سود. اگه دزدای دریایی حمله کنن بخشی از سرمایه می‌ره."
    else:
        head = "🔫 <b>قاچاق اسلحه</b>\n\nمحموله‌ی قاچاق می‌فرستی؛ موفق بشه چند برابر برمی‌گرده، اگه پلیس بگیره <b>همه‌ی سرمایه می‌ره</b>."
    lines = [head, f"\n💵 جیب: {fa(u.cash)} · سقف سرمایه‌ی هر سفر: {fa(cap)} (با ارتقای اتحاد بیشتر و امن‌تر می‌شه)\n"]
    rows = []
    if d:
        lines.append(f"⏳ در جریان: سرمایه {fa(d['amt'])} — {dur(left(d['end']))} دیگه")
    else:
        for i, cfg in enumerate(_routes(kind), 1):
            ch = int(_chance(kind, cfg, al.level) * 100)
            if kind == "sea":
                lines.append(f"{fa(i)}) {cfg[1]} — برگشت ×{cfg[3]} · شانس حمله‌ی دزدا {fa(ch)}٪ (٪{int(cfg[5] * 100)} سرمایه می‌ره)")
            else:
                lines.append(f"{fa(i)}) {cfg[1]} — برگشت ×{cfg[3]} · شانس موفقیت {fa(ch)}٪")
            rows.append([b(f"{fa(i)}) {fa(cap * p // 100)}", f"al:dv:{kind}:{cfg[0]}:{p}") for p in (25, 50, 100)])
        lines.append(f"\n۵٪ سودت هم به بانک اتحاد می‌ره.")
    rows.append(back("al:inc", "◀️ کسب درآمد"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data.regexp(r"^al:dl:(sea|arms)$"))
async def cb_deal_page(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_deal, cb.data.split(":")[2])


@router.callback_query(F.data.regexp(r"^al:dv:(sea|arms):\w+:\d+$"))
async def cb_deal_go(cb: CallbackQuery, user: User) -> None:
    _, _, kind, rk, pct = cb.data.split(":")
    cfg = next((r for r in _routes(kind) if r[0] == rk), None)
    if cfg is None or int(pct) not in (25, 50, 100):
        return await cb.answer()
    u, _ = await fresh(user.tg_id)
    al = await eco.alliance_of(u)
    if not al:
        return await cb.answer("اول عضو اتحاد شو!", show_alert=True)
    amt = C.INVEST_CAP[al.level] * int(pct) // 100
    if u.cash < amt:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    ch = _chance(kind, cfg, al.level)
    hit = war.RNG.random() < ch
    if kind == "sea":
        bad, ret = hit, int(amt * (1 - cfg[5])) if hit else int(amt * cfg[3])
    else:
        bad, ret = (not hit), int(amt * cfg[3]) if hit else 0
    end = utcnow() + timedelta(seconds=cfg[2])
    if not await eco.spend(u.tg_id, amt, set_={f"inc.{kind}": {"amt": amt, "end": end, "ret": ret, "route": rk, "bad": bad}},
                           flt={f"inc.{kind}": {"$exists": False}}):
        return await cb.answer("⏳ یه سفر در جریانه یا پولت کافی نیست.", show_alert=True)
    await cb.answer("🚢" if kind == "sea" else "🔫")
    await _page(cb, u.tg_id, render_deal, kind, note=f"✅ {cfg[1]} شروع شد با {fa(amt)} دلار — {dur(cfg[2])} دیگه نتیجه مشخص می‌شه.")


# ───────────────────────── چاه نفت ─────────────────────────
def oil_info(u: User, al: Alliance) -> tuple[int, float, float, object]:
    o = (u.inc or {}).get("oil") or {}
    n = int(o.get("n", 0))
    t = o.get("t")
    rate = C.OIL_PRICE * C.OIL_YIELD_PER_HOUR * n * (1 + C.ALLIANCE_LEVELS[al.level][1])
    hrs = min(C.OIL_STORE_HOURS, max(0.0, (utcnow() - t).total_seconds() / 3600)) if (t and n) else 0.0
    return n, rate, rate * hrs, t


async def _oil_collect(u: User, al: Alliance) -> int:
    n, rate, pending, t = oil_info(u, al)
    amt = int(pending)
    if n <= 0 or t is None:
        return 0
    r = await eco.col().update_one({"tg_id": u.tg_id, "inc.oil.t": t}, {"$set": {"inc.oil.t": utcnow()}, "$inc": {"cash": amt}})
    return amt if r.matched_count == 1 else 0


async def render_oil(u: User):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    n, rate, pending, _ = oil_info(u, al)
    mx = C.OIL_MAX_PER_LEVEL * al.level
    text = (f"🛢 <b>چاه نفت</b>\n\nهر چاه {fa(C.OIL_PRICE)} دلار؛ هر ساعت {C.OIL_YIELD_PER_HOUR * 100:g}٪ قیمتش (+بونوس اتحاد) درآمد می‌ده. "
            f"مخزن حداکثر {fa(C.OIL_STORE_HOURS)} ساعت تولید نگه می‌داره.\n\n"
            f"🛢 چاه‌ها: <b>{fa(n)}/{fa(mx)}</b> (با ارتقای اتحاد بیشتر می‌شه)\n📈 درآمد: {fa(rate)} در ساعت\n"
            f"🪣 تو مخزن: <b>{fa(pending)}</b>\n💵 جیب: {fa(u.cash)}")
    rows = []
    if pending >= 1:
        rows.append([b(f"💰 برداشت {fa(pending)}", "al:oil:c")])
    if n < mx:
        rows.append([b(f"🛢 خرید چاه — {fa(C.OIL_PRICE)}", "al:oil:b")])
    rows.append(back("al:inc", "◀️ کسب درآمد"))
    return text, kb(*rows)


@router.callback_query(F.data == "al:oil")
async def cb_oil(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_oil)


@router.callback_query(F.data == "al:oil:c")
async def cb_oil_collect(cb: CallbackQuery, user: User) -> None:
    u, _ = await fresh(user.tg_id)
    al = await eco.alliance_of(u)
    if not al:
        return await cb.answer()
    got = await _oil_collect(u, al)
    await cb.answer("✅" if got else "مخزن خالیه.")
    await _page(cb, u.tg_id, render_oil, note=f"💰 {fa(got)} دلار نفت برداشت شد." if got else "")


@router.callback_query(F.data == "al:oil:b")
async def cb_oil_buy(cb: CallbackQuery, user: User) -> None:
    u, _ = await fresh(user.tg_id)
    al = await eco.alliance_of(u)
    if not al:
        return await cb.answer("اول عضو اتحاد شو!", show_alert=True)
    n, _, _, _ = oil_info(u, al)
    if n >= C.OIL_MAX_PER_LEVEL * al.level:
        return await cb.answer("به سقف چاه‌های این سطح اتحاد رسیدی! اتحاد رو ارتقا بده.", show_alert=True)
    got = await _oil_collect(u, al)
    if not await eco.spend(u.tg_id, C.OIL_PRICE, inc={"inc.oil.n": 1}, set_={"inc.oil.t": utcnow()}):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer("🛢")
    await _page(cb, u.tg_id, render_oil, note="🛢 یه چاه جدید خریدی!" + (f" (+{fa(got)} دلار برداشت قبلی)" if got else ""))


async def render_mission_pick(u: User, cat: str):
    have = _cat_units(u, cat)
    if not have or cat in (u.mission or {}):
        return await render_missions(u)
    val = war.value(have)
    lines = [f"{C.MIL_CATS[cat][0]} <b>ماموریت {C.MIL_CATS[cat][1]}</b>\n", f"نیروها: {war.units_text(have)}", f"ارزش: {fa(val)}\n"]
    rows = []
    for key, name, sec, rate, loss in C.MIL_MISSIONS:
        lines.append(f"• {name}: پاداش ≈ {fa(val * rate)} · احتمال تلفات هر یگان {int(loss * 100)}٪")
        rows.append([b(f"{name} — {fa(val * rate)}", f"al:mg:{cat}:{key}")])
    rows.append(back("al:mis", "◀️ ماموریت‌ها"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data.regexp(r"^al:ms:\w+$"))
async def cb_mission_pick(cb: CallbackQuery, user: User) -> None:
    cat = cb.data.split(":")[2]
    if cat not in ("human", "armor", "air"):
        return await cb.answer()
    await cb.answer()
    await _page(cb, user.tg_id, render_mission_pick, cat)


@router.callback_query(F.data.regexp(r"^al:mg:\w+:\w+$"))
async def cb_mission_go(cb: CallbackQuery, user: User) -> None:
    _, _, cat, mk = cb.data.split(":")
    cfg = next((m for m in C.MIL_MISSIONS if m[0] == mk), None)
    if cat not in ("human", "armor", "air") or cfg is None:
        return await cb.answer()
    u, _ = await fresh(user.tg_id)
    if not u.alliance_id:
        return await cb.answer("اول عضو اتحاد شو!", show_alert=True)
    have = _cat_units(u, cat)
    if not have or cat in (u.mission or {}):
        return await cb.answer("نیرویی برای فرستادن نیست یا ماموریت قبلی تموم نشده.", show_alert=True)
    _, _, sec, rate, loss = cfg
    reward = int(war.value(have) * rate)
    lost = {k: sum(1 for _ in range(n) if war.RNG.random() < loss) for k, n in have.items()}
    lost = {k: n for k, n in lost.items() if n}
    flt = {f"army.{k}": {"$gte": n} for k, n in have.items()}
    flt[f"mission.{cat}"] = {"$exists": False}
    ok = await eco.update_user(u.tg_id, inc={f"army.{k}": -n for k, n in have.items()}, flt=flt,
                               set_={f"mission.{cat}": {"u": have, "end": utcnow() + timedelta(seconds=sec), "reward": reward,
                                                        "lost": lost, "tax": int(reward * C.MIL_MISSION_AL_TAX)}})
    if not ok:
        return await cb.answer("⚠️ نشد؛ دوباره امتحان کن.", show_alert=True)
    await cb.answer("🎖")
    await _page(cb, u.tg_id, render_missions, note=f"🎖 ماموریت شروع شد: {C.MIL_CATS[cat][1]} — {dur(sec)} دیگه برمی‌گردن (+{fa(reward)} دلار)")


# ───────────────────────── دفاع (پادگان) ─────────────────────────
async def render_def(u: User):
    garr = war.merge(u.garrison, u.fort)
    text = ("🛡 <b>دفاع</b>\n\nپادگان و پدافند وقتی بهت حمله بشه <b>خودکار</b> مقابله می‌کنن؛ لازم نیست چیزی بنویسی.\n\n"
            f"🛡 پادگان: {war.units_text(u.garrison)}\n🧱 پدافند و سنگر: {war.units_text(u.fort)}\n📦 ذخیره: {war.units_text(u.army)}\n\n"
            f"قدرت دفاع خودکار (ضربه/استقامت):\n{war.power_line(garr)}\n\n"
            "برای جابه‌جایی یه یگان بین ذخیره و پادگان روش بزن. (پدافندها از «خرید» ← پدافند و سنگر.)")
    rows = []
    for k in war.MOBILE:
        a, g = u.army.get(k, 0), u.garrison.get(k, 0)
        if a or g:
            rows.append([b(f"{UNITS[k]['emoji']} {UNITS[k]['name']} — 📦{fa(a)} 🛡{fa(g)}", f"al:dg:{k}")])
    rows.append([b("🛡 همه ← پادگان", "al:ma:g"), b("📦 همه ← ذخیره", "al:ma:r")])
    rows.append(back("al", "◀️ اتحاد"))
    return text, kb(*rows)


@router.callback_query(F.data == "al:def")
async def cb_def(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_def)


async def render_def_unit(u: User, k: str):
    text = unit_card(k) + f"\n\n📦 ذخیره: {fa(u.army.get(k, 0))} · 🛡 پادگان: {fa(u.garrison.get(k, 0))}"
    rows = [[b(f"🛡 ← پادگان {fa(n) if n else 'همه'}", f"al:mv:{k}:g:{n}") for n in (1, 5, 0)],
            [b(f"📦 ← ذخیره {fa(n) if n else 'همه'}", f"al:mv:{k}:r:{n}") for n in (1, 5, 0)],
            back("al:def", "◀️ دفاع")]
    return text, kb(*rows)


@router.callback_query(F.data.regexp(r"^al:dg:\w+$"))
async def cb_def_unit(cb: CallbackQuery, user: User) -> None:
    k = cb.data.split(":")[2]
    if k not in UNITS:
        return await cb.answer()
    await cb.answer()
    await _page(cb, user.tg_id, render_def_unit, k)


async def _move(uid: int, k: str, direction: str, n: int) -> int:
    u = await eco.get_user(uid)
    src, dst = ("army", "garrison") if direction == "g" else ("garrison", "army")
    have = getattr(u, src).get(k, 0)
    n = have if n == 0 else min(n, have)
    if n <= 0:
        return 0
    r = await eco.col().update_one({"tg_id": uid, f"{src}.{k}": {"$gte": n}}, {"$inc": {f"{src}.{k}": -n, f"{dst}.{k}": n}})
    return n if r.matched_count == 1 else 0


@router.callback_query(F.data.regexp(r"^al:mv:\w+:[gr]:\d+$"))
async def cb_move(cb: CallbackQuery, user: User) -> None:
    _, _, k, d, n = cb.data.split(":")
    if k not in UNITS:
        return await cb.answer()
    got = await _move(user.tg_id, k, d, int(n))
    if not got:
        return await cb.answer("چیزی برای جابه‌جایی نیست.", show_alert=True)
    await cb.answer("✅")
    await _page(cb, user.tg_id, render_def_unit, k)


@router.callback_query(F.data.regexp(r"^al:ma:[gr]$"))
async def cb_move_all(cb: CallbackQuery, user: User) -> None:
    d = cb.data.split(":")[2]
    u = await eco.get_user(user.tg_id)
    src = u.army if d == "g" else u.garrison
    for k in list(war.clean(src)):
        await _move(user.tg_id, k, d, 0)
    await cb.answer("✅")
    await _page(cb, user.tg_id, render_def)


# ───────────────────────── حمله (منو) ─────────────────────────
async def render_war(u: User):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    wait = await redis_service.r.ttl(f"cd:war:{u.tg_id}")
    text = ("⚔️ <b>حمله</b>\n\nفقط به اعضای <b>اتحادهای دیگه</b> می‌تونی حمله کنی.\n\n"
            "توی گروه روی پیام طرف ریپلای کن و بنویس:\n<code>حمله ۲۰ تانک ابرامز و ۲۰ ارپیجی زن</code>\n"
            "پیوی: <code>حمله @یوزر ۲۰ تانک ابرامز</code>\n\n"
            f"📦 نیروهای آماده‌ی حمله: {war.units_text(u.army)}")
    if wait and wait > 0:
        text += f"\n\n😮‍💨 حمله‌ی بعدی: {dur(wait)} دیگه"
    return text, kb([b("🎯 حمله به @یوزر", "al:wt")], [b("🗺 اتحادهای دیگه", "al:foes")], back("al", "◀️ اتحاد"))


@router.callback_query(F.data == "al:war")
async def cb_war(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_war)


@router.callback_query(F.data == "al:wt")
async def cb_war_target(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await ask_target(cb, state, "war", "بعد از یوزرنیم، نیروها رو هم بنویس:\n<code>@یوزر ۲۰ تانک ابرامز و ۲۰ ارپیجی زن</code>")


@router.callback_query(F.data == "al:foes")
async def cb_foes(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    rows, lines = [], ["🗺 <b>اتحادهای دیگه</b>\n"]
    for al in await Alliance.find(Alliance.aid != (user.alliance_id or 0)).limit(10).to_list():
        n = await User.find(User.alliance_id == al.aid).count()
        lines.append(f"🤝 {esc(al.name)} — 👥 {fa(n)} — سطح {fa(al.level)}")
        rows.append([b(f"👀 {al.name}", f"al:foe:{al.aid}")])
    if len(lines) == 1:
        lines.append("اتحاد دیگه‌ای وجود نداره.")
    rows.append(back("al:war", "◀️ حمله"))
    await edit(cb, "\n".join(lines), kb(*rows))


@router.callback_query(F.data.regexp(r"^al:foe:\d+$"))
async def cb_foe(cb: CallbackQuery, user: User) -> None:
    aid = int(cb.data.split(":")[2])
    al = await Alliance.find_one(Alliance.aid == aid)
    if not al or aid == user.alliance_id:
        return await cb.answer()
    await cb.answer()
    lines = [f"👀 <b>{esc(al.name)}</b>\n"]
    for m in (await members_of(aid))[:15]:
        lines.append(f"{'👑 ' if m.tg_id == al.leader_id else ''}{esc(m.name)}" + (f" — @{m.username}" if m.username else f" — #{m.tg_id}")
                     + f" — ⭐{fa(rules.level_of(m.xp))}")
    await edit(cb, "\n".join(lines), kb(back("al:foes", "◀️ اتحادها")))


# ───────────────────────── ساخت ─────────────────────────
@router.callback_query(F.data == "al:create")
async def cb_create(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    if user.alliance_id:
        return await cb.answer("اول از اتحاد فعلیت خارج شو.", show_alert=True)
    if user.cash < C.ALLIANCE_CREATE:
        return await cb.answer(f"💸 {fa(C.ALLIANCE_CREATE)} دلار نقد لازمه!", show_alert=True)
    await state.set_state(Ask.alname)
    await cb.answer()
    await edit(cb, "🤝 <b>ساخت اتحاد</b>\n\nاسم اتحاد رو بفرست (۳ تا ۲۰ حرف):"
               + ("\n\n↩️ <b>روی همین پیام ریپلای کن</b> و اسم رو بفرست." if in_group(cb) else ""), kb([b("❌ لغو", "al")]))


@router.message(StateFilter(Ask.alname), F.func(replying_to_bot), F.text)
async def got_name(m: Message, state: FSMContext, user: User = None) -> None:
    if user is None:                                   # گروه
        if m.from_user is None or m.from_user.is_bot:
            return
        async with redis_service.lock(f"u:{m.from_user.id}", ttl=20, wait=6):
            actor, _ = await eco.ensure_user(m.from_user)
            return await _got_name(m, actor, state)
    return await _got_name(m, user, state)


async def _got_name(m: Message, user: User, state: FSMContext) -> None:
    async def ans(t, mk=None):
        return await reply_owned(m, t, mk)

    name = re.sub(r"\s+", " ", m.text.strip())
    if not 3 <= len(name) <= 20 or name.startswith("/"):
        return await ans("⚠️ اسم باید ۳ تا ۲۰ حرف باشه. دوباره بفرست:")
    user = await eco.get_user(user.tg_id)
    if user.cash < C.ALLIANCE_CREATE or user.alliance_id:
        await state.clear()
        return await ans("💸 پول کافی نداری یا عضو اتحادی هستی.")
    aid = await eco.next_seq("alliance")
    if not await eco.spend(user.tg_id, C.ALLIANCE_CREATE, flt={"alliance_id": None}):
        await state.clear()
        return await ans("💸 پول کافی نداری.")
    await eco.settle(user.tg_id)
    try:
        await Alliance(aid=aid, name=name, name_key=norm(name), leader_id=user.tg_id).insert()
    except DuplicateKeyError:
        await eco.gain(user.tg_id, C.ALLIANCE_CREATE)
        return await ans("😕 این اسم قبلاً گرفته شده؛ یکی دیگه بفرست:")
    await eco.update_user(user.tg_id, set_={"alliance_id": aid})
    await state.clear()
    await ans(f"🎉 اتحاد <b>{esc(name)}</b> ساخته شد! دوستات رو دعوت کن.", kb([b("🤝 اتحاد", "al")]))


# ───────────────────────── لیست و پیوستن ─────────────────────────
@router.callback_query(F.data == "al:list")
async def cb_list(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    rows, lines = [], ["📜 <b>اتحادها</b>\n"]
    pipe = [{"$group": {"_id": "$alliance_id", "n": {"$sum": 1}, "w": {"$sum": "$invested"}}},
            {"$match": {"_id": {"$ne": None}}}, {"$sort": {"n": -1, "w": -1}}, {"$limit": 8}]
    for g in await eco.col().aggregate(pipe).to_list(8):
        al = await Alliance.find_one(Alliance.aid == g["_id"])
        if not al:
            continue
        cap = C.ALLIANCE_LEVELS[al.level][0]
        lines.append(f"🤝 {esc(al.name)} — 👥 {fa(g['n'])}/{fa(cap)} — سطح {fa(al.level)}")
        if not user.alliance_id and g["n"] < cap:
            rows.append([b(f"➕ پیوستن به {al.name}", f"al:join:{al.aid}")])
    if len(lines) == 1:
        lines.append("هنوز اتحادی ساخته نشده!")
    rows.append(back("al", "◀️ اتحاد"))
    await edit(cb, "\n".join(lines), kb(*rows))


async def _join(uid: int, al: Alliance) -> Optional[str]:
    """عضو کردن (بعد از تایید رهبر یا با لینک دعوت). خروجی: متن خطا یا None."""
    u = await eco.get_user(uid)
    if u is None:
        return "کاربر پیدا نشد."
    if u.alliance_id:
        return "این بازیکن عضو یه اتحاده!"
    if await User.find(User.alliance_id == al.aid).count() >= C.ALLIANCE_LEVELS[al.level][0]:
        return "ظرفیت اتحاد پره!"
    await eco.settle(uid)
    await eco.update_user(uid, set_={"alliance_id": al.aid})
    await Alliance.get_motor_collection().update_one({"aid": al.aid}, {"$pull": {"requests": uid}})
    return None


@router.callback_query(F.data.startswith("al:join:"))
async def cb_join(cb: CallbackQuery, user: User) -> None:
    if user.alliance_id:
        return await cb.answer("تو عضو یه اتحادی!", show_alert=True)
    aid = int(cb.data.split(":")[2])
    al = await Alliance.find_one(Alliance.aid == aid)
    if not al:
        return await cb.answer("اتحاد پیدا نشد.", show_alert=True)
    r = await Alliance.get_motor_collection().update_one(
        {"aid": aid, "requests": {"$ne": user.tg_id}, "requests.29": {"$exists": False}}, {"$push": {"requests": user.tg_id}})
    if r.matched_count != 1:
        return await cb.answer("درخواستت قبلاً فرستاده شده (یا صف رهبر پره).", show_alert=True)
    await cb.answer("📨 درخواست فرستاده شد!", show_alert=True)
    await _dm(cb.bot, al.leader_id, f"📨 <b>{esc(user.name)}</b> می‌خواد به اتحاد «{esc(al.name)}» بپیونده.",
              kb([b("✅ قبول", f"al:ok:{user.tg_id}"), b("❌ رد", f"al:no:{user.tg_id}")]))
    await _show(cb, user.tg_id, f"📨 درخواستت برای «{esc(al.name)}» فرستاده شد؛ منتظر تایید رهبر باش.")


async def render_requests(u: User):
    al = await eco.alliance_of(u)
    if not al or al.leader_id != u.tg_id:
        return "👑 فقط رهبر.", kb(back("al", "◀️ اتحاد"))
    lines, rows = ["📨 <b>درخواست‌های عضویت</b>\n"], []
    for uid in al.requests:
        r = await eco.get_user(uid)
        if r is None or r.alliance_id:
            await Alliance.get_motor_collection().update_one({"aid": al.aid}, {"$pull": {"requests": uid}})
            continue
        lines.append(f"👤 {esc(r.name)} — ⭐{fa(rules.level_of(r.xp))} — {fa(eco.net_worth(r))}")
        rows.append([b(f"✅ {r.name}", f"al:ok:{uid}"), b("❌ رد", f"al:no:{uid}")])
    if len(lines) == 1:
        lines.append("درخواستی نیست.")
    rows.append(back("al", "◀️ اتحاد"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "al:req")
async def cb_requests(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_requests)


@router.callback_query(F.data.regexp(r"^al:(ok|no):\d+$"))
async def cb_decide(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("👑 فقط رهبر!", show_alert=True)
    _, act, uid_s = cb.data.split(":")
    uid = int(uid_s)
    fresh_al = await Alliance.find_one(Alliance.aid == al.aid)
    if uid not in fresh_al.requests:
        await cb.answer("این درخواست دیگه نیست.", show_alert=True)
        return await _page(cb, user.tg_id, render_requests)
    if act == "no":
        await Alliance.get_motor_collection().update_one({"aid": al.aid}, {"$pull": {"requests": uid}})
        await _dm(cb.bot, uid, f"❌ درخواستت برای اتحاد «{esc(al.name)}» رد شد.")
        await cb.answer("رد شد.")
    else:
        err = await _join(uid, al)
        if err:
            await Alliance.get_motor_collection().update_one({"aid": al.aid}, {"$pull": {"requests": uid}})
            return await cb.answer(err, show_alert=True)
        await _dm(cb.bot, uid, f"✅ رهبر اتحاد «{esc(al.name)}» درخواستت رو قبول کرد! خوش اومدی 🤝", kb([b("🤝 اتحاد", "al")]))
        await cb.answer("✅ قبول شد.")
    await _page(cb, user.tg_id, render_requests)


# ───────────────────────── لینک دعوت ─────────────────────────
async def _invite_token(al: Alliance, renew: bool = False) -> str:
    if al.invite and not renew:
        return al.invite
    tok = secrets.token_hex(4)
    await Alliance.get_motor_collection().update_one({"aid": al.aid}, {"$set": {"invite": tok}})
    return tok


async def _link_page(cb: CallbackQuery, user: User, renew: bool = False) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("👑 فقط رهبر!", show_alert=True)
    tok = await _invite_token(al, renew)
    me = await cb.bot.me()
    link = f"https://t.me/{me.username}?start=ali_{al.aid}_{tok}"
    await cb.answer()
    await edit(cb, (f"🔗 <b>لینک دعوت {esc(al.name)}</b>\n\n<code>{link}</code>\n\n"
                    "هر کی با این لینک وارد ربات بشه، <b>بدون نیاز به تایید</b> عضو اتحاد می‌شه (تا وقتی ظرفیت داری). "
                    "اگه لو رفت «لینک جدید» بزن؛ لینک قبلی باطل می‌شه."),
               kb([b("🔄 لینک جدید", "al:link:new")], back("al", "◀️ اتحاد")))


@router.callback_query(F.data == "al:link")
async def cb_link(cb: CallbackQuery, user: User) -> None:
    await _link_page(cb, user)


@router.callback_query(F.data == "al:link:new")
async def cb_link_new(cb: CallbackQuery, user: User) -> None:
    await _link_page(cb, user, renew=True)


@router.message(CommandStart(deep_link=True, magic=F.args.regexp(r"^ali_(\d+)_([0-9a-f]{8})$")))
async def deep_join(m: Message, command: CommandObject, user: User = None) -> None:
    if user is None or m.chat.type != "private":
        return
    mt = re.match(r"^ali_(\d+)_([0-9a-f]{8})$", command.args or "")
    al = await Alliance.find_one(Alliance.aid == int(mt.group(1))) if mt else None
    if al is None or not al.invite or al.invite != mt.group(2):
        return await m.answer("🔗 این لینک دعوت معتبر نیست یا باطل شده.")
    if user.alliance_id == al.aid:
        return await m.answer("تو همین الان عضو این اتحادی! 🤝", reply_markup=kb([b("🤝 اتحاد", "al")]))
    err = await _join(user.tg_id, al)
    if err:
        return await m.answer("🚫 " + err)
    await m.answer(f"✅ با لینک دعوت به اتحاد <b>{esc(al.name)}</b> پیوستی! 🤝", reply_markup=kb([b("🤝 اتحاد", "al")]))
    await _dm(m.bot, al.leader_id, f"🔗 {mention(user.tg_id, user.name)} با لینک دعوت به اتحادت پیوست.")


# ───────────────────────── نیروهای من ─────────────────────────
async def render_forces(u: User):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    away: dict[str, int] = {}
    for m in (u.mission or {}).values():
        for k, n in m.get("u", {}).items():
            away[k] = away.get(k, 0) + int(n)
    p = u.prod or {}
    total = war.merge(u.army, u.garrison, u.fort, away)
    lines = ["🎖 <b>نیروهای من</b>\n", "📦 ذخیره · 🛡 پادگان · 🧱 پدافند · 🎖 تو ماموریت\n"]
    for cat in war.CATS:
        rows = []
        for k, x in UNITS.items():
            if x["cat"] != cat:
                continue
            parts = [f"{e}{fa(n)}" for e, n in (("📦", u.army.get(k, 0)), ("🛡", u.garrison.get(k, 0)),
                                                 ("🧱", u.fort.get(k, 0)), ("🎖", away.get(k, 0))) if n > 0]
            if parts:
                rows.append(f"{x['emoji']} {x['name']}: " + " ".join(parts))
        if rows:
            lines.append(f"<b>{C.MIL_CATS[cat][0]} {C.MIL_CATS[cat][1]}</b>")
            lines.extend(rows)
            lines.append("")
    if not total:
        lines.append("هنوز نیرویی نداری. از «🛒 خرید نیرو» یا «🏭 تولید» شروع کن.\n")
    if p.get("key") in UNITS:
        lines.append(f"🏭 در حال تولید: {war.label(p['key'], p['n'])} — {dur(left(p['end']))} دیگه" + (" (برای انبار اتحاد)" if p.get("al") else "") + "\n")
    lines.append(f"💰 ارزش کل: {fa(war.value(total))}\n📏 ظرفیت: {fa(used_supply(u))}/{fa(cap_of(u, al))} جا")
    lines.append(f"\n⚔️ قدرت کل (ضربه/استقامت):\n{war.power_line(total)}")
    lines.append(f"🛡 قدرت دفاع خودکار:\n{war.power_line(war.merge(u.garrison, u.fort))}")
    return "\n".join(lines), kb([b("🛡 دفاع", "al:def"), b("🛒 خرید", "al:uc:b")], back("al", "◀️ اتحاد"))


@router.callback_query(F.data == "al:forces")
async def cb_forces(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_forces)


# ───────────────────────── انبار اتحاد / تحویل ─────────────────────────
async def render_armory(u: User):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    used = await armory_used(al)
    text = (f"🏛 <b>انبار اتحاد</b>\n\n{war.units_text(al.armory, chr(10))}\n\nظرفیت: {fa(used)}/{fa(C.ARMORY_CAP_PER_LEVEL * al.level)} جا\n"
            f"🏦 بانک اتحاد: {fa(al.bank)}\n\n")
    if al.leader_id == u.tg_id:
        text += ("👑 با پول بانک از «🛒 خرید» یا «🏭 تولید» (دکمه‌های 🏦) نیرو بخر تا بیاد اینجا. بعد روی پیام عضو ریپلای کن و بنویس:\n"
                 "<code>تحویل ۱۰ تانک ابرامز و ۵ جنگنده f14</code>\n(توی پیوی: <code>تحویل @یوزر ۱۰ تانک ابرامز</code>)")
    else:
        text += "رهبر نیروهای انبار رو بین اعضا تحویل می‌ده."
    return text, kb(back("al", "◀️ اتحاد"))


@router.callback_query(F.data == "al:arm")
async def cb_armory(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_armory)


DELIVER_USAGE = ("🎁 روی پیام عضو ریپلای کن و بنویس:\n<code>تحویل ۱۰ تانک ابرامز و ۵ جنگنده f14</code>\n"
                 "(پیوی: <code>تحویل @یوزر ۱۰ تانک ابرامز</code>)")


async def deliver_text(bot, actor: User, target: Optional[User], text: str) -> str:
    """رهبر: تحویل نیرو از انبار اتحاد به یه عضو."""
    al = await eco.alliance_of(actor)
    if not al or al.leader_id != actor.tg_id:
        return "👑 فقط رهبر اتحاد می‌تونه از انبار نیرو تحویل بده."
    if target is None:
        return DELIVER_USAGE
    t, _ = await fresh(target.tg_id)
    if t is None or t.alliance_id != al.aid or t.tg_id == actor.tg_id:
        return "🤝 طرف باید عضو اتحاد تو باشه (و خودت نباشه)."
    units, bad, _ = war.parse_units(text)
    if bad:
        return f"🤔 «{esc(' '.join(bad))}» رو نشناختم.\n" + DELIVER_USAGE
    if not units:
        return DELIVER_USAGE
    al = await Alliance.find_one(Alliance.aid == al.aid)
    send = {}
    for k, n in units.items():
        have = int(al.armory.get(k, 0))
        n = have if n is None else n
        if n <= 0 or have < n:
            return f"🚫 تو انبار از {war.label(k)} فقط {fa(have)} تا هست."
        send[k] = n
    if used_supply(t) + war.supply(send) > cap_of(t, al):
        return f"🏠 ظرفیت پادگان {esc(t.name)} پره ({fa(used_supply(t))}/{fa(cap_of(t, al))} جا)."
    r = await Alliance.get_motor_collection().update_one({"aid": al.aid, **{f"armory.{k}": {"$gte": n} for k, n in send.items()}},
                                                         {"$inc": {f"armory.{k}": -n for k, n in send.items()}})
    if r.matched_count != 1:
        return "⚠️ موجودی انبار همین الان تغییر کرد؛ دوباره امتحان کن."
    inc: dict[str, int] = {}
    for k, n in send.items():
        dest = "fort" if UNITS[k]["kind"] == "fort" else "army"
        inc[f"{dest}.{k}"] = inc.get(f"{dest}.{k}", 0) + n
    await eco.col().update_one({"tg_id": t.tg_id}, {"$inc": inc})
    await _dm(bot, t.tg_id, f"🎁 رهبر اتحاد از انبار برات نیرو فرستاد: {war.units_text(send)}")
    return f"🎁 {war.units_text(send)} از انبار اتحاد به {mention(t.tg_id, t.name)} تحویل داده شد."


# ───────────────────────── بانک / ارتقا ─────────────────────────
@router.callback_query(F.data == "al:dep")
async def cb_dep_menu(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al:
        return await cb.answer("عضو اتحادی نیستی!", show_alert=True)
    await cb.answer()
    lead = al.leader_id == user.tg_id
    text = (f"🏦 <b>بانک اتحاد</b>\n\n🏦 موجودی بانک: <b>{fa(al.bank)}</b>\n💵 جیب: {fa(user.cash)}\n\n"
            "بانک برای خرید و تولید نیروی اتحاد خرج می‌شه (فقط رهبر خرج می‌کنه؛ نیروها می‌رن «انبار اتحاد» و رهبر بین اعضا تحویل می‌ده). ارتقای سطح از جیب رهبره.")
    rows = [[b("📥 ۱۰٪", "al:dp:10"), b("📥 ۲۵٪", "al:dp:25"), b("📥 ۵۰٪", "al:dp:50"), b("📥 همه", "al:dp:100")]]
    if lead:
        text += "\n\n👑 به‌عنوان رهبر می‌تونی از بانک برداشت کنی:"
        rows.append([b("📤 ۱۰٪", "al:wd:10"), b("📤 ۲۵٪", "al:wd:25"), b("📤 ۵۰٪", "al:wd:50"), b("📤 همه", "al:wd:100")])
    rows.append(back("al", "◀️ اتحاد"))
    await edit(cb, text, kb(*rows))


@router.callback_query(F.data.regexp(r"^al:wd:\d+$"))
async def cb_withdraw(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("👑 فقط رهبر می‌تونه از بانک برداشت کنه!", show_alert=True)
    pct = int(cb.data.split(":")[2])
    if pct not in (10, 25, 50, 100):
        return await cb.answer()
    fresh_al = await Alliance.find_one(Alliance.aid == al.aid)
    amount = fresh_al.bank * pct // 100
    if amount <= 0:
        return await cb.answer("بانک خالیه.", show_alert=True)
    r = await Alliance.get_motor_collection().update_one({"aid": al.aid, "bank": {"$gte": amount}}, {"$inc": {"bank": -amount}})
    if r.matched_count != 1:
        return await cb.answer("⚠️ موجودی بانک همین الان تغییر کرد؛ دوباره امتحان کن.", show_alert=True)
    await eco.gain(user.tg_id, amount)
    await cb.answer()
    await _show(cb, user.tg_id, f"📤 {fa(amount)} دلار از بانک اتحاد برداشت شد.")


@router.callback_query(F.data.startswith("al:dp:"))
async def cb_dep(cb: CallbackQuery, user: User) -> None:
    if not user.alliance_id:
        return await cb.answer("عضو اتحادی نیستی!", show_alert=True)
    amount = user.cash * int(cb.data.split(":")[2]) // 100
    if amount <= 0 or not await eco.spend(user.tg_id, amount):
        return await cb.answer("پولی برای واریز نیست.", show_alert=True)
    await Alliance.get_motor_collection().update_one({"aid": user.alliance_id}, {"$inc": {"bank": amount}})
    await cb.answer()
    await _show(cb, user.tg_id, f"🏦 {fa(amount)} دلار به بانک اتحاد واریز شد.")


@router.callback_query(F.data == "al:up")
async def cb_up(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("فقط رهبر می‌تونه!", show_alert=True)
    if al.level >= max(C.ALLIANCE_LEVELS):
        return await cb.answer("به سقف رسیدی!", show_alert=True)
    cost = C.ALLIANCE_LEVELS[al.level + 1][2]
    if not await eco.spend(user.tg_id, cost):
        return await cb.answer(f"💸 ارتقا از جیب رهبره و باید {fa(cost)} دلار داشته باشی!", show_alert=True)
    r = await Alliance.get_motor_collection().update_one({"aid": al.aid, "level": al.level}, {"$inc": {"level": 1}})
    if r.matched_count != 1:
        await eco.gain(user.tg_id, cost)
        return await cb.answer("⚠️ نشد؛ دوباره امتحان کن.", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, f"⬆️ اتحاد شد سطح {fa(al.level + 1)}! ({fa(cost)} دلار از جیبت رفت)")


# ───────────────────────── اعضا / اخراج / خروج ─────────────────────────
@router.callback_query(F.data == "al:members")
async def cb_members(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al:
        return await cb.answer("عضو اتحادی نیستی!", show_alert=True)
    await cb.answer()
    mem = await members_of(al.aid)
    lines = [f"👥 <b>اعضای {esc(al.name)}</b>\n"]
    rows = []
    for m in mem:
        crown = "👑 " if m.tg_id == al.leader_id else ""
        lines.append(f"{crown}{esc(m.name)} — {fa(eco.net_worth(m))}")
        if user.tg_id == al.leader_id and m.tg_id != user.tg_id:
            rows.append([b(f"❌ اخراج {m.name}", f"al:kick:{m.tg_id}")])
    rows.append(back("al", "◀️ اتحاد"))
    await edit(cb, "\n".join(lines), kb(*rows))


@router.callback_query(F.data.startswith("al:kick:"))
async def cb_kick(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("فقط رهبر!", show_alert=True)
    target = int(cb.data.split(":")[2])
    t = await eco.get_user(target)
    if not t or t.alliance_id != al.aid or target == user.tg_id:
        return await cb.answer("عضو نیست.", show_alert=True)
    await eco.settle(target)
    await eco.update_user(target, set_={"alliance_id": None})
    await cb.answer("اخراج شد.")
    await cb_members(cb, user)


@router.callback_query(F.data == "al:leave")
async def cb_leave(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al:
        return await cb.answer()
    await eco.settle(user.tg_id)
    await eco.update_user(user.tg_id, set_={"alliance_id": None})
    rest = [m for m in await members_of(al.aid) if m.tg_id != user.tg_id]
    if not rest:
        await al.delete()
    elif al.leader_id == user.tg_id:
        await Alliance.get_motor_collection().update_one({"aid": al.aid}, {"$set": {"leader_id": rest[0].tg_id}})
    await cb.answer()
    await _show(cb, user.tg_id, "🚪 از اتحاد خارج شدی.")


# ═════════════════════════════ جنگ ═════════════════════════════
WAR_USAGE = ("⚔️ روی پیام طرف ریپلای کن و بنویس:\n<code>حمله ۲۰ تانک ابرامز و ۲۰ ارپیجی زن</code>\n"
             "(پیوی: <code>حمله @یوزر ۲۰ تانک ابرامز</code>)\nیگان‌ها رو تو «اتحاد ← 🛒 خرید نیرو» ببین.")
_tasks: set = set()


async def _dm(bot, uid: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(uid, text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        pass


async def open_incoming(uid: int) -> Optional[Battle]:
    return await Battle.find({"def_id": uid, "status": "open"}).sort("-created_at").first_or_none()


def _resolve_amounts(units: dict, have: dict) -> tuple[dict[str, int], Optional[str]]:
    send: dict[str, int] = {}
    for k, n in units.items():
        x = UNITS[k]
        if x["kind"] != "mobile":
            return {}, f"🧱 {x['name']} ثابته و به حمله/مقابله نمی‌ره؛ خودکار دفاع می‌کنه."
        got = int(have.get(k, 0))
        n = got if n is None else n
        if n <= 0 or got < n:
            return {}, f"🚫 از {war.label(k)} فقط {fa(got)} تا تو <b>ذخیره</b> داری (پادگان جدا حساب می‌شه؛ از «اتحاد ← دفاع» جابه‌جا کن)."
        send[k] = n
    return send, None


async def war_text(bot, actor: User, target: Optional[User], text: str, chat_id: int) -> Optional[str]:
    """ورودی مشترک متن «حمله …» نظامی (گروه، پیوی، منو). مقابله‌ی مدافع یا حمله‌ی جدید."""
    inc = await open_incoming(actor.tg_id)
    if inc and (target is None or target.tg_id == inc.att_id):
        return await counter(bot, actor, inc, text)
    if target is None:
        return WAR_USAGE
    return await attack(bot, actor, target, text, chat_id)


async def attack(bot, actor: User, target: User, text: str, chat_id: int) -> str:
    now = utcnow()
    actor, _ = await fresh(actor.tg_id)
    target = await eco.get_user(target.tg_id)
    if target is None:
        return "🤷 طرف توی بازی نیست."
    if not actor.alliance_id:
        return "🤝 برای جنگ نظامی باید عضو یه اتحاد باشی (و یگان داشته باشی). «اتحاد» رو بزن."
    if not target.alliance_id:
        return f"🏳️ {esc(target.name)} عضو هیچ اتحادی نیست؛ جنگ نظامی فقط بین اعضای اتحادهاست."
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    units, bad, _ = war.parse_units(text)
    if bad:
        return f"🤔 «{esc(' '.join(bad))}» رو نشناختم.\n" + WAR_USAGE
    send, err = _resolve_amounts(units, actor.army)
    if err:
        return err
    if not send:
        return WAR_USAGE
    wait = await redis_service.cooldown(f"war:{actor.tg_id}", C.WAR_COOLDOWN_SEC)
    if wait:
        return f"😮‍💨 نیروهات هنوز خسته‌ان؛ {dur(wait)} دیگه می‌تونی حمله کنی."
    wait = await redis_service.cooldown(f"warpair:{actor.tg_id}:{target.tg_id}", C.WAR_PAIR_COOLDOWN_SEC)
    if wait:
        await redis_service.clear_cooldown(f"war:{actor.tg_id}")
        return f"⏳ تازه به {esc(target.name)} حمله کردی؛ {dur(wait)} دیگه می‌تونی دوباره سراغش بری (بقیه رو می‌تونی بزنی)."
    ok = await eco.update_user(actor.tg_id, inc={f"army.{k}": -n for k, n in send.items()},
                               flt={f"army.{k}": {"$gte": n} for k, n in send.items()})
    if not ok:
        await redis_service.clear_cooldown(f"war:{actor.tg_id}")
        await redis_service.clear_cooldown(f"warpair:{actor.tg_id}:{target.tg_id}")
        return "⚠️ نشد (نیروها همین الان تغییر کردن)؛ دوباره امتحان کن."
    bid = await eco.next_seq("battle")
    empty = not (war.clean(target.garrison) or war.clean(target.fort) or war.clean(target.army))
    wait_s = 0 if empty else C.WAR_REPLY_SEC
    await Battle(bid=bid, att_id=actor.tg_id, def_id=target.tg_id, att_name=actor.name, def_name=target.name, chat_id=chat_id,
                 att_units=send, resolve_at=now + timedelta(seconds=wait_s)).insert()
    if empty:
        t = asyncio.create_task(_later(bot, bid, 1.5))
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)
    aa, da = await eco.alliance_of(actor), await eco.alliance_of(target)
    an, tn = mention(actor.tg_id, actor.name), mention(target.tg_id, target.name)
    head = (f"⚔️ <b>جنگ اتحادها!</b>\n{an} ({esc(aa.name) if aa else '—'}) به {tn} ({esc(da.name) if da else '—'}) حمله کرد!\n"
            f"🗡 نیروها: {war.units_text(send)}\n")
    if empty:
        return head + "💨 طرف هیچ نیرو و پدافندی نداره؛ نبرد فوری تموم می‌شه!"
    await _dm(bot, target.tg_id,
              f"🚨 <b>{esc(actor.name)}</b> ({esc(aa.name) if aa else '—'}) بهت حمله‌ی نظامی کرد!\n🗡 {war.units_text(send)}\n"
              f"⏳ تا {fa(C.WAR_REPLY_SEC)} ثانیه می‌تونی از ذخیره‌ات نیرو اضافه کنی: <code>حمله ۲۰ تانک t72</code>\n"
              f"وگرنه پادگان و پدافندت خودکار مقابله می‌کنن. 📦 ذخیره‌ی تو: {war.units_text(target.army)}")
    return head + (f"🛡 {tn} تا <b>{fa(C.WAR_REPLY_SEC)} ثانیه</b> فرصت داره بنویسه <code>حمله ۲۰ تانک t72</code> و از ذخیره‌اش مقابله کنه؛\n"
                   "پادگان و پدافند و سنگرهاش هم خودکار دفاع می‌کنن.")


async def _later(bot, bid: int, sec: float) -> None:
    await asyncio.sleep(sec)
    await resolve(bot, bid)


async def counter(bot, actor: User, battle: Battle, text: str) -> str:
    actor, _ = await fresh(actor.tg_id)
    units, bad, _ = war.parse_units(text)
    if bad:
        return f"🤔 «{esc(' '.join(bad))}» رو نشناختم. مثال: <code>حمله ۲۰ تانک t72</code>"
    send, err = _resolve_amounts(units, actor.army)
    if err:
        return err
    if not send:
        return "🤔 چی بفرستم؟ مثال: <code>حمله ۲۰ تانک t72</code>"
    ok = await eco.update_user(actor.tg_id, inc={f"army.{k}": -n for k, n in send.items()},
                               flt={f"army.{k}": {"$gte": n} for k, n in send.items()})
    if not ok:
        return "⚠️ نشد (نیروها همین الان تغییر کردن)؛ دوباره امتحان کن."
    r = await Battle.get_motor_collection().update_one(
        {"bid": battle.bid, "status": "open", "resolve_at": {"$gt": utcnow()}},
        {"$inc": {f"def_units.{k}": n for k, n in send.items()}})
    if r.matched_count != 1:
        await eco.update_user(actor.tg_id, inc={f"army.{k}": n for k, n in send.items()})
        return "⌛ دیر شد؛ نبرد همین الان شروع شد. نیروهات برگشتن به ذخیره."
    b2 = await Battle.find_one(Battle.bid == battle.bid)
    return f"🛡 {war.units_text(send)} به دفاع اضافه شد! ⏳ {dur(left(b2.resolve_at))} تا شروع نبرد."


async def _sub(uid: int, field: str, lost: dict) -> None:
    if not lost:
        return
    stage = {f"{field}.{k}": {"$max": [0, {"$subtract": [{"$ifNull": [f"${field}.{k}", 0]}, n]}]} for k, n in lost.items()}
    await eco.col().update_one({"tg_id": uid}, [{"$set": stage}])


async def hit_business(uid: int) -> Optional[str]:
    """یه کارگر (از پایین‌ترین ردهٔ یه کاسبیِ شانسی) تلف می‌شه؛ خود کاسبی هیچ‌وقت از بین نمی‌ره."""
    await eco.settle(uid)
    u = await eco.get_user(uid)
    cands = [k for k in u.businesses if k in rules.BIZ_INDEX and rules.total_workers(u, k) > 0]
    if not cands:
        return None
    key = war.RNG.choice(cands)
    cnt = rules.staff_counts(u, key)
    i = next(i for i, c in enumerate(cnt) if c > 0)
    field = f"businesses.{key}" if i == 0 else f"staff.{rules.staff_key(key, i)}"
    if not await eco.update_user(uid, inc={field: -1}, flt={field: {"$gte": 1}}):
        return None
    bz = rules.BIZ_INDEX[key]
    return f"🔥 تو {bz['emoji']} {bz['name']} یه {bz['workers'][i]['name']} تلف شد"


def _report(doc: dict, res: dict, names: tuple, parts: tuple, money: dict) -> str:
    an, tn, aa, da = names
    committed, garrison, fort = parts
    win = res["att_win"]
    dparts = [x for x in (f"مقابله‌ی دستی: {war.units_text(committed)}" if committed else "",
                          f"پادگان: {war.units_text(garrison)}" if garrison else "",
                          f"پدافند: {war.units_text(fort)}" if fort else "") if x]
    lines = [f"⚔️ <b>نتیجه‌ی نبرد</b>\n{an} ({aa}) 🆚 {tn} ({da})\n",
             f"🗡 مهاجم: {war.units_text(doc['att_units'])}",
             "🛡 مدافع: " + (" · ".join(dparts) if dparts else "بدون نیرو")]
    lines.append("\n<i>میدان‌ها: ضربه‌ی مهاجم→استقامت مدافع | ضربه‌ی مدافع→استقامت مهاجم</i>")
    for c in ("H", "A", "R"):
        a = res["arena"][c]
        if any(a.values()):
            e, nm = C.MIL_CLASSES[c]
            lines.append(f"{e} {nm}: {fa(a['a_off'])}→{fa(a['d_def'])} | {fa(a['d_off'])}→{fa(a['a_def'])}")
    lines.append(f"\n💀 تلفات مهاجم: {war.units_text(res['att_lost'])}\n💀 تلفات مدافع: {war.units_text(res['def_lost'])}")
    lines.append(f"\n🏆 <b>برنده: {an if win else tn}</b>")
    if money.get("got"):
        lines.append(f"💰 غنیمت: {fa(money['got'])} دلار" + (f" ({fa(money['tax'])} سهم بانک اتحاد)" if money.get("tax") else ""))
    lines.extend(money.get("biz", []))
    return "\n".join(lines)


async def resolve(bot, bid: int) -> None:
    col = Battle.get_motor_collection()
    doc = await col.find_one_and_update({"bid": bid, "status": "open"}, {"$set": {"status": "resolving", "resolve_at": utcnow()}},
                                        return_document=ReturnDocument.AFTER)
    if not doc:
        return
    a_id, d_id = doc["att_id"], doc["def_id"]
    try:
        async with redis_service.lock(f"u:{min(a_id, d_id)}", ttl=30, wait=15):
            async with redis_service.lock(f"u:{max(a_id, d_id)}", ttl=30, wait=15):
                await _apply(bot, doc)
    except LockTimeout:
        await col.update_one({"bid": bid}, {"$set": {"status": "open"}})
    except Exception:  # noqa: BLE001
        log.exception("war_resolve_error", bid=bid)
        await col.update_one({"bid": bid}, {"$set": {"status": "failed"}})
        await eco.update_user(a_id, inc={f"army.{k}": n for k, n in war.clean(doc["att_units"]).items()})
        await eco.update_user(d_id, inc={f"army.{k}": n for k, n in war.clean(doc["def_units"]).items()})


async def _apply(bot, doc: dict) -> None:
    a_id, d_id = doc["att_id"], doc["def_id"]
    att, dfn = await eco.get_user(a_id), await eco.get_user(d_id)
    att_units, committed = war.clean(doc["att_units"]), war.clean(doc["def_units"])
    col = Battle.get_motor_collection()
    if att is None or dfn is None:
        await eco.update_user(a_id, inc={f"army.{k}": n for k, n in att_units.items()})
        await eco.update_user(d_id, inc={f"army.{k}": n for k, n in committed.items()})
        await col.update_one({"bid": doc["bid"]}, {"$set": {"status": "failed"}})
        return
    garrison, fort = war.clean(dfn.garrison), war.clean(dfn.fort)
    res = war.simulate(att_units, war.merge(committed, garrison, fort))
    c_lost, g_lost, f_lost = war.split_losses(res["def_lost"], committed, garrison, fort)
    surv_a = {k: n - res["att_lost"].get(k, 0) for k, n in att_units.items()}
    surv_c = {k: n - c_lost.get(k, 0) for k, n in committed.items()}
    await eco.update_user(a_id, inc={f"army.{k}": n for k, n in surv_a.items() if n > 0})
    await eco.update_user(d_id, inc={f"army.{k}": n for k, n in surv_c.items() if n > 0})
    await _sub(d_id, "garrison", g_lost)
    await _sub(d_id, "fort", f_lost)

    win = res["att_win"]
    w, l = (att, dfn) if win else (dfn, att)
    loser_lost = res["v_def_lost"] if win else res["v_att_lost"]
    money: dict = {"biz": []}
    cash_l = (await eco.get_user(l.tg_id)).cash
    amt = war.loot(cash_l, res["margin"], loser_lost)
    if amt > 0 and await eco.transfer(l.tg_id, w.tg_id, amt):
        money["got"] = amt
        if w.alliance_id:
            tax = int(amt * C.WAR_AL_TAX)
            if tax > 0 and await eco.spend(w.tg_id, tax):
                await Alliance.get_motor_collection().update_one({"aid": w.alliance_id}, {"$inc": {"bank": tax}})
                money["tax"] = tax
    if war.RNG.random() < C.WAR_BIZ_CHANCE:
        for _ in range(2 if res["margin"] > 0.6 else 1):
            msg = await hit_business(l.tg_id)
            if msg:
                money["biz"].append(msg + f" ({esc(l.name)})")
    await eco.update_user(w.tg_id, inc={"stats.war_w": 1, "stats.war_loot": money.get("got", 0)})
    await eco.update_user(l.tg_id, inc={"stats.war_l": 1, "stats.war_lost": money.get("got", 0)})
    await eco.add_xp(w.tg_id, C.XP["win"])
    await eco.add_xp(l.tg_id, C.XP["lose"])
    await col.update_one({"bid": doc["bid"]}, {"$set": {"status": "done"}})

    aa, da = await eco.alliance_of(att), await eco.alliance_of(dfn)
    text = _report(doc, res, (mention(a_id, att.name), mention(d_id, dfn.name), esc(aa.name) if aa else "—", esc(da.name) if da else "—"),
                   (committed, garrison, fort), money)
    await _dm(bot, doc["chat_id"], text)
    if d_id != doc["chat_id"]:
        await _dm(bot, d_id, text)


async def sweeper(bot) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            now = utcnow()
            col = Battle.get_motor_collection()
            await col.update_many({"status": "resolving", "resolve_at": {"$lte": now - timedelta(minutes=2)}}, {"$set": {"status": "open"}})
            for d in await col.find({"status": "open", "resolve_at": {"$lte": now}}).limit(10).to_list(10):
                await resolve(bot, d["bid"])
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("war_sweeper_error")
        await asyncio.sleep(3)


try:                                        # صفحه‌ی راهنمای اتحاد/ارتش تو راهنمای اصلی
    import h_menu
    if not any(n == "🤝 اتحاد و ارتش" for n, _ in h_menu.HELP_PAGES):
        h_menu.HELP_PAGES.append(("🤝 اتحاد و ارتش", GUIDE))
except Exception:  # noqa: BLE001
    pass
