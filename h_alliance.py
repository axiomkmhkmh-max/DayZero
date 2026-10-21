"""اتحاد (گروه): ساخت، پیوستن، بانک مشترک، ارتقا + ارتش مشترک (خرید، تولید، ماموریت، چاه نفت، تجارت) + جنگ اتحادها.

همه‌چی مال کل اتحاده: ارتش، پدافند، چاه نفت، خط تولید، ماموریت‌ها و بانک روی خود Alliance نگه داشته می‌شن، نه روی تک‌تک بازیکن‌ها.
هرچی خریده/تولید بشه همون لحظه تو ارتش مشترکه؛ انبار، پادگان جدا و تحویل نداریم.
جنگ: «حمله ۲۰ تانک ابرامز و ۲۰ ارپیجی زن» (ریپلای روی یکی از اعضای اتحاد دشمن). کل ارتش و پدافند مدافع خودکار دفاع می‌کنن؛ نبرد همون لحظه حل می‌شه.
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
def alc():
    return Alliance.get_motor_collection()


def can_manage(u: User, al: Optional[Alliance]) -> bool:
    """خرج بانک، ماموریت، تجارت، چاه نفت و فرستادن ارتش به جنگ: رهبر، یا اعضا وقتی رهبر اجازه داده (همون سوئیچ «اعضا هم از بانک بخرن»)."""
    return bool(al and (u.tg_id == al.leader_id or al.member_bank))


NO_PERM = "🔒 رهبر مدیریت دارایی اتحاد رو فقط برای خودش گذاشته."


def away_units(al: Alliance) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in (al.mission or {}).values():
        for k, n in (m.get("u") or {}).items():
            out[k] = out.get(k, 0) + int(n)
    return war.clean(out)


def count_units(al: Alliance) -> int:
    """تعداد کل نیروهای اتحاد: آماده + پدافند + ماموریت + در حال تولید."""
    n = sum(war.clean(al.army).values()) + sum(war.clean(al.fort).values()) + sum(away_units(al).values())
    p = al.prod or {}
    if p.get("key") in UNITS:
        n += int(p.get("n", 0))
    return n


def slots_of(al: Alliance) -> int:
    """ظرفیت عضو اتحاد: پایه ۵ نفر؛ هر جای اضافه ۱ میلیارد (از بانک اتحاد) تا سقف ۱۰ نفر."""
    return int(min(C.ALLIANCE_MAX_SLOTS, max(C.ALLIANCE_BASE_SLOTS, int(getattr(al, "slots", C.ALLIANCE_BASE_SLOTS)))))


def oil_max(al: Alliance) -> int:
    return C.OIL_MAX_PER_LEVEL * al.level * slots_of(al)


def oil_info(al: Alliance) -> tuple[int, float, float, object]:
    """(تعداد چاه، درآمد ساعتی، درآمد جمع‌شده‌ی هنوز واریزنشده، زمان آخرین واریز)."""
    o = (al.inc or {}).get("oil") or {}
    n = int(o.get("n", 0))
    t = o.get("t")
    rate = C.OIL_PRICE * C.OIL_YIELD_PER_HOUR * n * (1 + C.ALLIANCE_LEVELS[al.level][1])
    hrs = min(C.OIL_STORE_HOURS, max(0.0, (utcnow() - t).total_seconds() / 3600)) if (t and n) else 0.0
    return n, rate, rate * hrs, t


async def settle_alliance(aid: int, force_oil: bool = False) -> list[str]:
    """تولید، ماموریت، سفرهای تجاری و درآمد چاه‌های اتحاد رو تحویل می‌ده (تنبل: هر بار که پنل/حمله باز می‌شه).
    همه‌چی مستقیم می‌ره تو ارتش/بانک مشترک؛ انبار و تحویل نداریم."""
    notes: list[str] = []
    al = await Alliance.find_one(Alliance.aid == aid)
    if al is None:
        return notes
    now = utcnow()
    p = al.prod or {}
    if p.get("key") in UNITS and p.get("end") and p["end"] <= now:
        k, n = p["key"], int(p.get("n", 0))
        dest = "fort" if UNITS[k]["kind"] == "fort" else "army"
        r = await alc().update_one({"aid": aid, "prod.end": p["end"]}, {"$set": {"prod": {}}, "$inc": {f"{dest}.{k}": n}})
        if r.matched_count == 1:
            notes.append(f"🏭 تولید تموم شد: {war.label(k, n)} رفت تو ارتش اتحاد.")
    for cat, m in list((al.mission or {}).items()):
        if not m.get("end") or m["end"] > now:
            continue
        reward = int(m.get("reward", 0))
        inc: dict = {"bank": reward}
        back_units = {k: n - int(m.get("lost", {}).get(k, 0)) for k, n in m.get("u", {}).items()}
        for k, n in back_units.items():
            if n > 0:
                inc[f"army.{k}"] = n
        r = await alc().update_one({"aid": aid, f"mission.{cat}.end": m["end"]}, {"$unset": {f"mission.{cat}": ""}, "$inc": inc})
        if r.matched_count == 1:
            lost = war.units_text(m.get("lost", {}))
            notes.append(f"🎖 ماموریت {C.MIL_CATS[cat][1]} تموم شد: +{fa(reward)} دلار به بانک اتحاد"
                         + (f" (تلفات: {lost})" if m.get("lost") else ""))
    for kind in ("sea", "arms"):
        d = (al.inc or {}).get(kind)
        if not d or not d.get("end") or d["end"] > now:
            continue
        amt, ret = int(d.get("amt", 0)), int(d.get("ret", 0))
        r = await alc().update_one({"aid": aid, f"inc.{kind}.end": d["end"]}, {"$unset": {f"inc.{kind}": ""}, "$inc": {"bank": ret}})
        if r.matched_count != 1:
            continue
        if kind == "sea":
            notes.append((f"🏴‍☠️ دزدای دریایی به کاروان حمله کردن! از {fa(amt)} فقط {fa(ret)} برگشت."
                          if d.get("bad") else f"🚢 تجارت دریایی تموم شد: {fa(amt)} ← {fa(ret)} دلار (+{fa(ret - amt)}) رفت تو بانک اتحاد"))
        else:
            notes.append((f"🚔 پلیس محموله‌ی اسلحه رو گرفت! {fa(amt)} دلار سرمایه‌ی اتحاد از دست رفت."
                          if d.get("bad") else f"🔫 محموله‌ی اسلحه فروخته شد: {fa(amt)} ← {fa(ret)} دلار (+{fa(ret - amt)}) رفت تو بانک اتحاد"))
    # درآمد چاه‌ها خودکار می‌ره تو بانک اتحاد (برای کم‌شدن نوشتن، فقط وقتی حداقل ۱۰۰۰ شده یا صفحه‌ی چاه باز باشه)
    n, _, pending, t = oil_info(al)
    amt = int(pending)
    if n > 0 and t is not None and (amt >= 1000 or (force_oil and amt >= 1)):
        await alc().update_one({"aid": aid, "inc.oil.t": t}, {"$set": {"inc.oil.t": now}, "$inc": {"bank": amt}})
    return notes


async def fresh(uid: int) -> tuple[User, list[str]]:
    u = await eco.get_user(uid)
    notes = await settle_alliance(u.alliance_id) if (u and u.alliance_id) else []
    return u, notes

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
                f"با دوستات اتحاد بساز: بانک مشترک، بونوس درآمد کاسبی، <b>ارتش مشترک</b> (تانک، جنگنده، تک‌تیرانداز...)، چاه نفت، تجارت دریایی و جنگ با اتحادهای دیگه!\n"
                f"💵 هزینه‌ی ساخت: <b>{fa(C.ALLIANCE_CREATE)}</b>\n\nیا از «لیست اتحادها» درخواست عضویت بده؛ رهبر تاییدت می‌کنه.")
        return text, kb([b("➕ ساخت اتحاد", "al:create"), b("📜 لیست اتحادها", "al:list")], back("menu", "◀️ منو"))
    _, bonus, _ = C.ALLIANCE_LEVELS[al.level]
    cap = slots_of(al)
    n = await User.find(User.alliance_id == al.aid).count()
    leader = u.tg_id == al.leader_id
    on, _, _, _ = oil_info(al)
    defense = war.merge(al.army, al.fort)
    text = (f"🤝 <b>{esc(al.name)}</b> — سطح {fa(al.level)}\n\n👥 اعضا: {fa(n)}/{fa(cap)}   "
            f"📈 بونوس درآمد: <b>+{int(bonus * 100)}٪</b>\n🏦 بانک اتحاد: <b>{fa(al.bank)}</b>\n"
            "\n"
            f"⚔️ <b>ارتش مشترک اتحاد</b>\n"
            f"🪖 نیرو: {fa(sum(war.clean(al.army).values()))} · 🧱 پدافند: {fa(sum(war.clean(al.fort).values()))}"
            f" · 🛢 چاه: {fa(on)}\n"
            f"قدرت دفاع خودکار (ضربه/استقامت):\n{war.power_line(defense)}\n\n"
            "همه‌چی مال کل اتحاده: هر کی بخره، همه استفاده می‌کنن. هم‌اتحادی‌ها نمی‌تونن به هم حمله کنن؛ به اتحادهای دیگه می‌تونی حمله‌ی نظامی کنی!")
    rows = [[b("🎖 نیروها", "al:forces"), b("💱 فروش", "al:uc:s")],
            [b("💰 کسب درآمد", "al:inc"), b("🏭 تولید", "al:uc:p")],
            [b("🛒 خرید نیرو", "al:uc:b"), b("⚔️ حمله", "al:war")],
            [b("🏦 بانک اتحاد", "al:dep"), b("👥 اعضا", "al:members")]]
    if leader:
        rows.append([b(f"📨 درخواست‌ها ({fa(len(al.requests))})", "al:req"), b("🔗 لینک دعوت", "al:link")])
        if slots_of(al) < C.ALLIANCE_MAX_SLOTS:
            rows.append([b(f"➕ جای عضو جدید — {fa(C.ALLIANCE_SLOT_PRICE)} از بانک ({fa(slots_of(al))}/{fa(C.ALLIANCE_MAX_SLOTS)})", "al:slot")])
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
(🎖 «نیروها» همه‌ی یگان‌های اتحاد و قدرت کلشون رو نشون می‌ده؛ تو گروه بنویس «ارتش».)

🤝 <b>همه‌چی مشترکه</b>: ارتش، پدافند، چاه‌های نفت، خط تولید، ماموریت‌ها و بانک مال <b>کل اتحادن</b>، نه یه نفر. هر کی نیرو بخره، همه ازش استفاده می‌کنن؛ هر چاهی که خریده بشه درآمدش مستقیم می‌ره بانک اتحاد. انبار و پادگان جدا و تحویل نداریم.

🔺 <b>سه قدرت</b>: 🪖 انسانی · ✈️ هوایی · 🚜 زرهی. هر قدرت با قدرتِ هم‌نوعش رو‌به‌رو می‌شه.
هر یگان دو چیز داره: <b>با چی کشته می‌شه</b> و <b>با چی می‌زنه</b>. مثلاً 🚀 ارپیجی‌زن پیاده‌ست (باید با قدرت انسانی بکشیش) ولی با قدرت <b>زرهی</b> می‌زنه (تانک‌ها رو نابود می‌کنه). 🛻 نفربر زرهیه ولی پیاده‌ها رو می‌زنه، 🛰 ضدهوایی زرهیه ولی هواپیما می‌زنه.

🛒 <b>خرید</b>: فوری، قیمت کامل، همون لحظه تو ارتش اتحاد. 🏭 <b>تولید</b>: ۳۵٪ ارزون‌تر ولی زمان می‌بره (هر سطح اتحاد ۵٪ سریع‌تر؛ یه خط تولید برای کل اتحاد).
پول خرید رو می‌شه از جیب خودت داد (کمک به اتحاد) یا با <code>بانک 20</code> از بانک اتحاد (رهبر، یا اعضا اگه رهبر اجازه داده باشه). فروش نیرو فقط با رهبره و پولش می‌ره بانک.

💰 <b>کسب درآمد</b> (از بانک و ارتش اتحاد): 🎖 ماموریت نظامی · 🚢 تجارت دریایی · 🔫 قاچاق اسلحه · 🛢 چاه نفت. همه‌ی پاداش‌ها می‌ره بانک اتحاد.
🏦 <b>بانک اتحاد</b>: اعضا واریز می‌کنن؛ برداشت نداره؛ پول بانک فقط خرج اتحاد می‌شه. ظرفیت اتحاد ۵ نفره؛ رهبر می‌تونه تا ۱۰ نفر جای اضافه بخره (هر جا ۱ میلیارد از بانک اتحاد). ارتقای سطح از جیب رهبره.
👑 <b>رهبر</b>: درخواست‌های عضویت رو تایید/رد می‌کنه، 🔗 لینک دعوت می‌سازه و تعیین می‌کنه اعضا هم بتونن خرج بانک کنن و ارتش رو بفرستن یا نه.

⚔️ <b>حمله</b> (فقط به اتحادهای دیگه) — روی پیام یکی از اعضای اتحاد دشمن ریپلای کن:
<code>حمله ۲۰ تانک ابرامز و ۲۰ ارپیجی زن</code>
🛡 همه‌ی نیروهای آماده و پدافندهای اتحاد مدافع خودکار دفاع می‌کنن؛ نبرد همون لحظه حل می‌شه.
🏆 هر طرف ارزش بیشتری از دشمن نابود کنه برنده‌ست: غنیمت پولی از بانک اتحاد بازنده (به کاسبی و دارایی شخصی اعضا کاری نداره). بازنده فقط حدود نصف تلفات رو می‌ده.
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
    return (f"{u['emoji']} <b>{u['name']}</b>\n💵 قیمت: {fa(u['price'])}\n"
            f"💥 ضربه: <b>{fa(u['atk'])}</b> تو میدان {_cls_names(u['tgt'])}\n"
            f"❤️ استقامت: <b>{fa(u['hp'])}</b> — برای کشتنش باید با قدرت {_cls_names({u['dcls']: 1})} بزنی"
            + ("\n🧱 ثابته: به حمله نمی‌ره و همیشه خودکار دفاع می‌کنه." if u["kind"] == "fort" else ""))


async def render_cats(u: User, mode: str):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    title = {"b": "🛒 <b>خرید نیرو</b> (فوری، قیمت کامل)", "s": f"💱 <b>فروش نیرو و تجهیزات</b> ({int(C.MIL_SELL_RATE * 100)}٪ قیمت خرید برمی‌گرده)"}.get(mode, "🏭 <b>تولید</b> (۳۵٪ ارزون‌تر، زمان‌بر)")
    text = f"{title}\n\n💵 جیب: {fa(u.cash)} · 🏦 بانک اتحاد: {fa(al.bank)}"
    p = al.prod or {}
    if mode == "p":
        if p.get("key") in UNITS:
            text += f"\n\n⏳ در حال تولید: {war.label(p['key'], p['n'])} — {dur(left(p['end']))} دیگه"
        else:
            text += "\n\nخط تولید خالیه؛ یه نوع یگان انتخاب کن."
    rows = [[b(f"{C.MIL_CATS[c][0]} {C.MIL_CATS[c][1]}", f"al:uk:{mode}:{c}")] for c in war.CATS]
    rows.append(back("al", "◀️ اتحاد"))
    return text, kb(*rows)


@router.callback_query(F.data.regexp(r"^al:uc:[bps]$"))
async def cb_cats(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_cats, cb.data.split(":")[2])


async def render_list(u: User, mode: str, cat: str):
    al = await eco.alliance_of(u)
    mult = C.MIL_PROD_PRICE if mode == "p" else (C.MIL_SELL_RATE if mode == "s" else 1.0)
    lines = [f"{C.MIL_CATS[cat][0]} <b>{C.MIL_CATS[cat][1]}</b>\n"]
    rows = []
    for k in [k for k, x in UNITS.items() if x["cat"] == cat]:
        x = UNITS[k]
        if mode == "s":
            n = _owned(al, k) if al else 0
            if n <= 0:
                continue
            lines.append(f"{x['emoji']} {x['name']} ({fa(n)}) — فروش هر عدد {fa(int(x['price'] * mult))}")
            rows.append([b(f"{x['emoji']} {x['name']} ({fa(n)}) — {fa(int(x['price'] * mult))}", f"al:uu:s:{k}")])
            continue
        lines.append(f"{x['emoji']} {x['name']} — {fa(x['price'] * mult)} — 💥{fa(x['atk'])} ❤️{fa(x['hp'])}")
        rows.append([b(f"{x['emoji']} {x['name']} — {fa(x['price'] * mult)}", f"al:uu:{mode}:{k}")])
    if mode == "s" and not rows:
        lines.append("تو این دسته چیزی برای فروش نداری.")
    rows.append(back(f"al:uc:{mode}", "◀️ دسته‌ها"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data.regexp(r"^al:uk:[bps]:\w+$"))
async def cb_list_units(cb: CallbackQuery, user: User) -> None:
    _, _, mode, cat = cb.data.split(":")
    if cat not in war.CATS:
        return await cb.answer()
    await cb.answer()
    await _page(cb, user.tg_id, render_list, mode, cat)


def _owned(al: Alliance, k: str) -> int:
    """چندتا از این یگان تو اتحاد قابل فروشه."""
    return int((al.fort if UNITS[k]["kind"] == "fort" else al.army).get(k, 0))


async def sell_unit(uid: int, k: str, n_s: str) -> tuple[bool, str]:
    """فروش یگان اتحاد (فقط رهبر). پولش می‌ره بانک اتحاد. خروجی: (موفق؟، متن)."""
    if k not in UNITS:
        return False, "یگان نامعتبره."
    u, _ = await fresh(uid)
    al = await eco.alliance_of(u)
    if not al:
        return False, "اول عضو اتحاد شو!"
    if al.leader_id != u.tg_id:
        return False, "👑 فروش نیروهای اتحاد فقط با رهبره."
    x = UNITS[k]
    unit_price = int(x["price"] * C.MIL_SELL_RATE)
    field = "fort" if x["kind"] == "fort" else "army"
    total = _owned(al, k)
    n = int(n_s)
    n = total if n == 0 else n
    if n <= 0 or n > total:
        return False, f"🚫 از {war.label(k)} فقط {fa(total)} تا تو اتحاد هست."
    r = await alc().update_one({"aid": al.aid, f"{field}.{k}": {"$gte": n}}, {"$inc": {f"{field}.{k}": -n, "bank": unit_price * n}})
    if r.matched_count != 1:
        return False, "⚠️ موجودی نیروها همین الان تغییر کرد؛ دوباره امتحان کن."
    return True, f"💱 {war.label(k, n)} فروخته شد و {fa(unit_price * n)} دلار رفت تو بانک اتحاد."


async def render_unit(u: User, mode: str, k: str):
    al = await eco.alliance_of(u)
    if mode == "s":
        x = UNITS[k]
        text = (unit_card(k) + f"\n\n💱 قیمت فروش هر عدد: <b>{fa(int(x['price'] * C.MIL_SELL_RATE))}</b> ({int(C.MIL_SELL_RATE * 100)}٪ قیمت خرید) — پولش می‌ره بانک اتحاد"
                + f"\n🎒 داری: {fa(_owned(al, k) if al else 0)}"
                + "\n\n✍️ (فقط رهبر) برای فروش روی <b>همین پیام ریپلای</b> کن و بنویس:\n<code>فروش 20</code>\n<code>فروش همه</code>")
        text += "\n⚠️ نیروهای تو ماموریت یا در حال تولید قابل فروش نیستن."
        return text, kb(back(f"al:uk:s:{x['cat']}", "◀️ لیست"))
    base = "p" if mode in "pn" else "b"
    x = UNITS[k]
    price = int(x["price"] * (C.MIL_PROD_PRICE if base == "p" else 1.0))
    have = _owned(al, k) if al else 0
    verb = "تولید" if base == "p" else "خرید"
    text = (unit_card(k) + f"\n\n{'🏭 قیمت تولید' if base == 'p' else '🛒 قیمت خرید'}: <b>{fa(price)}</b>"
            + (f" · ⏳ {dur(war.prod_seconds(k, 1, al.level if al else 1))} برای هر عدد" if base == "p" else "")
            + f"\n🎒 داری: {fa(have)} (تو ارتش مشترک اتحاد)\n💵 جیب: {fa(u.cash)}"
            + (("\n\n" + _busy_text(al)) if base == "p" and al and (al.prod or {}).get("key") else ""))
    text += f"\n\n✍️ برای {verb}، روی <b>همین پیام ریپلای</b> کن و بنویس:\n<code>20</code> ← {verb} از جیب خودت (برای کل اتحاد)\n<code>حداکثر</code> ← تا جایی که پول داری"
    if al and can_manage(u, al):
        text += f"\n<code>بانک 20</code> ← با پول بانک اتحاد ({fa(al.bank)})"
    elif al:
        text += "\n🔒 رهبر خرج از بانک رو برای اعضا بسته کرده."
    return text, kb(back(f"al:uk:{base}:{x['cat']}", "◀️ لیست"))


@router.callback_query(F.data.regexp(r"^al:uu:[bpmns]:\w+$"))
async def cb_unit(cb: CallbackQuery, user: User) -> None:
    _, _, mode, k = cb.data.split(":")
    if k not in UNITS:
        return await cb.answer()
    await cb.answer()
    await _page(cb, user.tg_id, render_unit, mode, k)


def _busy_text(al: Optional[Alliance]) -> str:
    p = (al.prod or {}) if al else {}
    if p.get("key") in UNITS and p.get("end"):
        return f"🏭 خط تولید اتحاد مشغوله (هر کی شروعش کرده فرقی نداره): {war.label(p['key'], p['n'])} — {dur(left(p['end']))} دیگه تموم می‌شه و می‌ره تو ارتش."
    return "🏭 خط تولید اتحاد مشغوله؛ چند لحظه بعد دوباره امتحان کن."


async def buy_unit(uid: int, mode: str, k: str, n_s: str) -> tuple[bool, str]:
    """خرید/تولید یه یگان برای کل اتحاد. mode: b خرید از جیب · m خرید از بانک · p تولید از جیب · n تولید از بانک.
    خروجی: (موفق؟، متن نتیجه یا خطا). هرچی خریده بشه همون لحظه تو ارتش مشترکه."""
    if k not in UNITS:
        return False, "یگان نامعتبره."
    u, _ = await fresh(uid)
    al = await eco.alliance_of(u)
    if not al:
        return False, "اول عضو اتحاد شو!"
    from_bank = mode in "cqmn"
    base = "p" if mode in "pn" else "b"
    if from_bank and not can_manage(u, al):
        return False, NO_PERM
    x = UNITS[k]
    dest = "fort" if x["kind"] == "fort" else "army"
    price = int(x["price"] * (C.MIL_PROD_PRICE if base == "p" else 1.0))
    funds = al.bank if from_bank else u.cash
    n = int(n_s)
    if n == 0:
        n = funds // max(1, price)
    if n <= 0 or funds < price * n:
        return False, ("💸 بانک اتحاد پول کافی نداره!" if from_bank else "💸 پول کافی نداری!")
    cost = price * n
    if mode == "b":
        if not await eco.spend(u.tg_id, cost):
            return False, "💸 پول کافی نداری!"
        try:
            await alc().update_one({"aid": al.aid}, {"$inc": {f"{dest}.{k}": n}})
        except Exception:  # noqa: BLE001
            await eco.gain(u.tg_id, cost)
            raise
        return True, f"✅ <b>خرید انجام شد</b> — {war.label(k, n)} رفت تو ارتش اتحاد ({fa(cost)} دلار از جیبت)"
    if mode == "m":
        r = await alc().update_one({"aid": al.aid, "bank": {"$gte": cost}}, {"$inc": {"bank": -cost, f"{dest}.{k}": n}})
        if r.matched_count != 1:
            return False, "💸 بانک اتحاد پول کافی نداره!"
        return True, f"✅ با پول بانک خریده شد و رفت تو ارتش اتحاد — {war.label(k, n)} ({fa(cost)} دلار از بانک)"
    end = utcnow() + timedelta(seconds=war.prod_seconds(k, n, al.level))
    prod = {"key": k, "n": n, "end": end}
    if mode == "p":
        r = await alc().update_one({"aid": al.aid, "prod.key": {"$exists": False}}, {"$set": {"prod": prod}})
        if r.matched_count != 1:
            return False, _busy_text(await eco.alliance_of(u))
        if not await eco.spend(u.tg_id, cost):
            await alc().update_one({"aid": al.aid, "prod.end": end}, {"$set": {"prod": {}}})
            return False, "💸 پول کافی نداری!"
        return True, f"🏭 تولید شروع شد: {war.label(k, n)} — {dur(left(end))} دیگه، بعدش می‌ره تو ارتش اتحاد ({fa(cost)} دلار از جیبت)"
    r = await alc().update_one({"aid": al.aid, "bank": {"$gte": cost}, "prod.key": {"$exists": False}},
                               {"$inc": {"bank": -cost}, "$set": {"prod": prod}})
    if r.matched_count != 1:
        fresh_al = await eco.alliance_of(u)
        return False, (_busy_text(fresh_al) if fresh_al and (fresh_al.prod or {}).get("key") else "💸 بانک اتحاد پول کافی نداره!")
    return True, f"🏭 تولید با پول بانک شروع شد: {war.label(k, n)} — {dur(left(end))} دیگه؛ می‌ره تو ارتش اتحاد ({fa(cost)} دلار)"


@router.callback_query(F.data.regexp(r"^al:uq:[bpmn]:\w+:\d+$"))
async def cb_unit_buy(cb: CallbackQuery, user: User) -> None:
    _, _, mode, k, n_s = cb.data.split(":")
    ok, note = await buy_unit(user.tg_id, mode, k, n_s)
    if not ok:
        return await cb.answer(note, show_alert=True)
    await cb.answer("✅")
    await _page(cb, user.tg_id, render_unit, mode, k, note=note)


# ── خرید با ریپلای: روی صفحه‌ی یگان ریپلای کن و عدد بنویس
SELL_RE = re.compile(r"^فروش\s*(?:(?:با\s*)?(بانک)\s*)?(\d+|حداکثر|همه|max)(?:\s*تا)?$", re.I)
BUY_RE = re.compile(r"^(?:(با\s*بانک|بانکی|بانک)\s*)?(\d+|حداکثر|همه|max)(?:\s*تا)?$", re.I)


def reply_unit(rm: Message) -> Optional[tuple[str, str]]:
    """از روی متن پیام ربات می‌فهمه صفحه‌ی کدوم یگانه → (key, base)."""
    t = rm.text or ""
    if "🎒 داری" not in t or not (rm.from_user and rm.from_user.is_bot):
        return None
    first = next((ln for ln in t.split("\n") if ln.strip() and "ریپلای" not in ln), "")
    for k, x in UNITS.items():
        if x["name"] in first and x["emoji"] in first:
            return k, ("s" if "قیمت فروش" in t else "p" if "قیمت تولید" in t else "b")
    return None


async def reply_buy(m: Message, actor: User) -> bool:
    """True یعنی پیام رو مصرف کردیم."""
    rm = m.reply_to_message
    if rm is None:
        return False
    hit = reply_unit(rm)
    if hit is None:
        return False
    txt = war.en_digits((m.text or "").strip())
    if hit[1] == "s":
        ms = SELL_RE.match(txt)
        if ms is None:
            return False
        q = ms.group(2).lower()
        ok, note = await sell_unit(actor.tg_id, hit[0], "0" if q in ("حداکثر", "همه", "max") else q)
        await m.reply(note if ok else f"⚠️ {note}")
        return True
    mt = BUY_RE.match(txt)
    if mt is None:
        return False
    k, base = hit
    al = await eco.alliance_of(actor)
    bank = bool(mt.group(1))
    if bank and al:
        mode = "m" if base == "b" else "n"
    else:
        mode = base
    q = mt.group(2).lower()
    n_s = "0" if q in ("حداکثر", "همه", "max") else q
    ok, note = await buy_unit(actor.tg_id, mode, k, n_s)
    await m.reply(note if ok else f"⚠️ {note}")
    return True


# ───────────────────────── کسب درآمد (ماموریت) ─────────────────────────
def _cat_units(al: Alliance, cat: str) -> dict[str, int]:
    return {k: n for k, n in war.clean(al.army).items() if UNITS[k]["cat"] == cat}


async def render_missions(u: User):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    lines = ["🎖 <b>ماموریت نظامی</b>\n\nنیروهای <b>ارتش اتحاد</b> رو بفرست ماموریت؛ بعد از تموم شدن با پاداش (که می‌ره <b>بانک اتحاد</b>) برمی‌گردن. "
             "تو ماموریت نمی‌تونن دفاع یا حمله کنن.\n"]
    rows = []
    for cat in ("human", "armor", "air"):
        emo, name = C.MIL_CATS[cat]
        m = (al.mission or {}).get(cat)
        have = _cat_units(al, cat)
        if m:
            lines.append(f"{emo} {name}: ⏳ تو ماموریت — {dur(left(m['end']))} دیگه (+{fa(m['reward'])})")
        elif have:
            lines.append(f"{emo} {name}: {war.units_text(have)}")
            if can_manage(u, al):
                rows.append([b(f"{emo} فرستادن {name}", f"al:ms:{cat}")])
        else:
            lines.append(f"{emo} {name}: نیرویی تو اتحاد نیست")
    if not can_manage(u, al):
        lines.append("\n" + NO_PERM)
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
    text = ("💰 <b>کسب درآمد اتحاد</b>\n\n🎖 <b>ماموریت نظامی</b>: نیروهای ارتش اتحاد رو بفرست ماموریت.\n"
            "🚢 <b>تجارت دریایی</b>: از بانک اتحاد سرمایه بذار، کاروان بفرست؛ ریسکش دزدای دریاییه.\n"
            "🔫 <b>قاچاق اسلحه</b>: ریسک بالا، سود بالا؛ اگه پلیس بگیره همه‌ی سرمایه می‌ره.\n"
            "🛢 <b>چاه نفت</b>: چاه مشترک بخر؛ هر ساعت خودکار می‌ره بانک اتحاد.\n\n"
            f"🏦 بانک اتحاد: {fa(al.bank)} · سقف سرمایه‌ی هر سفر (سطح اتحاد): {fa(C.INVEST_CAP[al.level])}")
    return text, kb([b("🎖 ماموریت نظامی", "al:mis"), b("🛢 چاه نفت", "al:oil")],
                    [b("🚢 تجارت دریایی", "al:dl:sea"), b("🔫 قاچاق اسلحه", "al:dl:arms")], back("al", "◀️ اتحاد"))


@router.callback_query(F.data == "al:inc")
async def cb_income(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_income)


# ───────────────────────── تجارت دریایی / قاچاق اسلحه (از بانک اتحاد) ─────────────────────────
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
    d = (al.inc or {}).get(kind)
    if kind == "sea":
        head = "🚢 <b>تجارت دریایی</b>\n\nسرمایه‌ی اتحاد رو با کاروان می‌فرستی؛ برمی‌گرده با سود. اگه دزدای دریایی حمله کنن بخشی از سرمایه می‌ره."
    else:
        head = "🔫 <b>قاچاق اسلحه</b>\n\nمحموله‌ی قاچاق می‌فرستی؛ موفق بشه چند برابر برمی‌گرده، اگه پلیس بگیره <b>همه‌ی سرمایه می‌ره</b>."
    lines = [head, f"\n🏦 بانک اتحاد: {fa(al.bank)} · سقف سرمایه‌ی هر سفر: {fa(cap)} (با ارتقای اتحاد بیشتر و امن‌تر می‌شه)\n"]
    rows = []
    if d:
        lines.append(f"⏳ در جریان: سرمایه {fa(d['amt'])} — {dur(left(d['end']))} دیگه")
    elif not can_manage(u, al):
        lines.append(NO_PERM)
    else:
        for i, cfg in enumerate(_routes(kind), 1):
            ch = int(_chance(kind, cfg, al.level) * 100)
            if kind == "sea":
                lines.append(f"{fa(i)}) {cfg[1]} — برگشت ×{cfg[3]} · شانس حمله‌ی دزدا {fa(ch)}٪ (٪{int(cfg[5] * 100)} سرمایه می‌ره)")
            else:
                lines.append(f"{fa(i)}) {cfg[1]} — برگشت ×{cfg[3]} · شانس موفقیت {fa(ch)}٪")
            rows.append([b(f"{fa(i)}) {fa(cap * p // 100)}", f"al:dv:{kind}:{cfg[0]}:{p}") for p in (25, 50, 100)])
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
    if not can_manage(u, al):
        return await cb.answer(NO_PERM, show_alert=True)
    amt = C.INVEST_CAP[al.level] * int(pct) // 100
    if al.bank < amt:
        return await cb.answer("💸 بانک اتحاد پول کافی نداره!", show_alert=True)
    ch = _chance(kind, cfg, al.level)
    hit = war.RNG.random() < ch
    if kind == "sea":
        bad, ret = hit, int(amt * (1 - cfg[5])) if hit else int(amt * cfg[3])
    else:
        bad, ret = (not hit), int(amt * cfg[3]) if hit else 0
    end = utcnow() + timedelta(seconds=cfg[2])
    r = await alc().update_one({"aid": al.aid, "bank": {"$gte": amt}, f"inc.{kind}": {"$exists": False}},
                               {"$inc": {"bank": -amt}, "$set": {f"inc.{kind}": {"amt": amt, "end": end, "ret": ret, "route": rk, "bad": bad}}})
    if r.matched_count != 1:
        return await cb.answer("⏳ یه سفر در جریانه یا بانک اتحاد پول کافی نداره.", show_alert=True)
    await cb.answer("🚢" if kind == "sea" else "🔫")
    await _page(cb, u.tg_id, render_deal, kind, note=f"✅ {cfg[1]} شروع شد با {fa(amt)} دلار از بانک اتحاد — {dur(cfg[2])} دیگه نتیجه مشخص می‌شه.")


# ───────────────────────── چاه نفت (مشترک؛ درآمد خودکار می‌ره بانک اتحاد) ─────────────────────────
async def render_oil(u: User):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    n, rate, _, _ = oil_info(al)
    mx = oil_max(al)
    text = (f"🛢 <b>چاه نفت اتحاد</b>\n\nچاه‌ها مال کل اتحادن. هر چاه {fa(C.OIL_PRICE)} دلار؛ هر ساعت {C.OIL_YIELD_PER_HOUR * 100:g}٪ قیمتش (+بونوس اتحاد) درآمد می‌ده "
            f"و <b>خودکار می‌ره بانک اتحاد</b> (برداشت دستی نداره؛ حداکثر {fa(C.OIL_STORE_HOURS)} ساعت تولید جمع می‌شه تا یکی پنل رو باز کنه).\n\n"
            f"🛢 چاه‌ها: <b>{fa(n)}/{fa(mx)}</b> (با ارتقای اتحاد و جای عضو بیشتر می‌شه)\n📈 درآمد: {fa(rate)} در ساعت\n"
            f"🏦 بانک اتحاد: {fa(al.bank)}")
    rows = []
    if n < mx and can_manage(u, al):
        rows.append([b(f"🛢 خرید چاه از بانک — {fa(C.OIL_PRICE)}", "al:oil:b")])
    elif n < mx:
        text += "\n\n" + NO_PERM
    rows.append(back("al:inc", "◀️ کسب درآمد"))
    return text, kb(*rows)


@router.callback_query(F.data == "al:oil")
async def cb_oil(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    if user.alliance_id:
        await settle_alliance(user.alliance_id, force_oil=True)
    await _page(cb, user.tg_id, render_oil)


@router.callback_query(F.data == "al:oil:b")
async def cb_oil_buy(cb: CallbackQuery, user: User) -> None:
    u, _ = await fresh(user.tg_id)
    al = await eco.alliance_of(u)
    if not al:
        return await cb.answer("اول عضو اتحاد شو!", show_alert=True)
    if not can_manage(u, al):
        return await cb.answer(NO_PERM, show_alert=True)
    n, _, _, _ = oil_info(al)
    if n >= oil_max(al):
        return await cb.answer("به سقف چاه‌های این سطح اتحاد رسیدی! اتحاد رو ارتقا بده.", show_alert=True)
    await settle_alliance(al.aid, force_oil=True)            # درآمد قبلی اول می‌ره بانک؛ بعد چاه جدید
    r = await alc().update_one({"aid": al.aid, "bank": {"$gte": C.OIL_PRICE}},
                               {"$inc": {"bank": -C.OIL_PRICE, "inc.oil.n": 1}, "$set": {"inc.oil.t": utcnow()}})
    if r.matched_count != 1:
        return await cb.answer("💸 بانک اتحاد پول کافی نداره! (اول به بانک واریز کنید)", show_alert=True)
    await cb.answer("🛢")
    await _page(cb, u.tg_id, render_oil, note="🛢 یه چاه جدید برای اتحاد خریده شد!")


async def render_mission_pick(u: User, cat: str):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    have = _cat_units(al, cat)
    if not have or cat in (al.mission or {}) or not can_manage(u, al):
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
    al = await eco.alliance_of(u)
    if not al:
        return await cb.answer("اول عضو اتحاد شو!", show_alert=True)
    if not can_manage(u, al):
        return await cb.answer(NO_PERM, show_alert=True)
    have = _cat_units(al, cat)
    if not have or cat in (al.mission or {}):
        return await cb.answer("نیرویی برای فرستادن نیست یا ماموریت قبلی تموم نشده.", show_alert=True)
    _, _, sec, rate, loss = cfg
    reward = int(war.value(have) * rate)
    lost = {k: sum(1 for _ in range(n) if war.RNG.random() < loss) for k, n in have.items()}
    lost = {k: n for k, n in lost.items() if n}
    flt = {"aid": al.aid, f"mission.{cat}": {"$exists": False}}
    flt.update({f"army.{k}": {"$gte": n} for k, n in have.items()})
    r = await alc().update_one(flt, {"$inc": {f"army.{k}": -n for k, n in have.items()},
                                     "$set": {f"mission.{cat}": {"u": have, "end": utcnow() + timedelta(seconds=sec), "reward": reward, "lost": lost}}})
    if r.matched_count != 1:
        return await cb.answer("⚠️ نشد؛ دوباره امتحان کن.", show_alert=True)
    await cb.answer("🎖")
    await _page(cb, u.tg_id, render_missions, note=f"🎖 ماموریت شروع شد: {C.MIL_CATS[cat][1]} — {dur(sec)} دیگه برمی‌گردن (+{fa(reward)} دلار به بانک اتحاد)")


# ───────────────────────── حمله (منو) ─────────────────────────
async def render_war(u: User):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    wait = await redis_service.r.ttl(f"cd:war:{al.aid}")
    text = ("⚔️ <b>حمله</b>\n\nفقط به <b>اتحادهای دیگه</b> می‌تونی حمله کنی. نیروها مال کل اتحادن و همه‌ی ارتش و پدافند دشمن خودکار دفاع می‌کنه.\n\n"
            "توی گروه روی پیام یکی از اعضای اتحاد دشمن ریپلای کن و بنویس:\n<code>حمله ۲۰ تانک ابرامز و ۲۰ ارپیجی زن</code>\n"
            "پیوی: <code>حمله @یوزر ۲۰ تانک ابرامز</code>\n\n"
            f"✅ نیروهای آماده‌ی حمله: {war.units_text(al.army)}")
    if not can_manage(u, al):
        text += "\n\n" + NO_PERM
    if wait and wait > 0:
        text += f"\n\n😮‍💨 حمله‌ی بعدیِ اتحاد: {dur(wait)} دیگه"
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
        cap = slots_of(al)
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
    if await User.find(User.alliance_id == al.aid).count() >= slots_of(al):
        return "ظرفیت اتحاد پره! (رهبر می‌تونه با «➕ جای عضو جدید» ظرفیت رو بیشتر کنه.)"
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


# ───────────────────────── نیروها ─────────────────────────
async def render_forces(u: User):
    al = await eco.alliance_of(u)
    if not al:
        return "🤝 اول عضو یه اتحاد شو.", kb(back("al", "◀️ اتحاد"))
    away = away_units(al)
    p = al.prod or {}
    total = war.merge(al.army, al.fort, away)
    lines = ["🎖 <b>ارتش مشترک اتحاد</b>\n", "✅ آماده (خودکار دفاع هم می‌کنن) · 🧱 پدافند · 🎖 تو ماموریت\n"]
    for cat in war.CATS:
        rows = []
        for k, x in UNITS.items():
            if x["cat"] != cat:
                continue
            parts = [f"{e}{fa(n)}" for e, n in (("✅", al.army.get(k, 0)), ("🧱", al.fort.get(k, 0)), ("🎖", away.get(k, 0))) if n > 0]
            if parts:
                rows.append(f"{x['emoji']} {x['name']}: " + " ".join(parts))
        if rows:
            lines.append(f"<b>{C.MIL_CATS[cat][0]} {C.MIL_CATS[cat][1]}</b>")
            lines.extend(rows)
            lines.append("")
    if not total:
        lines.append("هنوز اتحاد نیرویی نداره. از «🛒 خرید نیرو» یا «🏭 تولید» شروع کنید.\n")
    if p.get("key") in UNITS:
        lines.append(f"🏭 در حال تولید: {war.label(p['key'], p['n'])} — {dur(left(p['end']))} دیگه" + "\n")
    lines.append(f"💰 ارزش کل: {fa(war.value(total))}\n🔢 تعداد نیروها: {fa(count_units(al))}")
    lines.append(f"\n⚔️ قدرت کل (ضربه/استقامت):\n{war.power_line(total)}")
    lines.append(f"🛡 قدرت دفاع خودکار:\n{war.power_line(war.merge(al.army, al.fort))}")
    return "\n".join(lines), kb([b("🛒 خرید", "al:uc:b"), b("🏭 تولید", "al:uc:p")], back("al", "◀️ اتحاد"))


@router.callback_query(F.data == "al:forces")
async def cb_forces(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _page(cb, user.tg_id, render_forces)


# ───────────────────────── بانک / ارتقا ─────────────────────────
@router.callback_query(F.data == "al:dep")
async def cb_dep_menu(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al:
        return await cb.answer("عضو اتحادی نیستی!", show_alert=True)
    await cb.answer()
    lead = al.leader_id == user.tg_id
    text = (f"🏦 <b>بانک اتحاد</b>\n\n🏦 موجودی بانک: <b>{fa(al.bank)}</b>\n💵 جیب: {fa(user.cash)}\n\n"
            "بانک برای خرید و تولید نیرو خرج می‌شه: هر کی (رهبر یا عضو؛ اگه رهبر اجازه بده) با <code>بانک 20</code> مستقیم برای ارتش مشترک اتحاد می‌خره. ارتقای سطح از جیب رهبره، جای عضو جدید از بانک.")
    rows = [[b("📥 ۱۰٪", "al:dp:10"), b("📥 ۲۵٪", "al:dp:25"), b("📥 ۵۰٪", "al:dp:50"), b("📥 همه", "al:dp:100")]]
    if lead:
        text += (f"\n\n🔒 پول بانک اتحاد قابل برداشت نیست و فقط خرج اتحاد می‌شه.\n"
                 f"👥 خرج بانک توسط اعضا: <b>{'روشن ✅' if al.member_bank else 'خاموش 🔒'}</b>")
        rows.append([b("👥 خرج اعضا: " + ("خاموش کن" if al.member_bank else "روشن کن"), "al:mb")])
    rows.append(back("al", "◀️ اتحاد"))
    await edit(cb, text, kb(*rows))


@router.callback_query(F.data == "al:mb")
async def cb_member_bank(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("👑 فقط رهبر!", show_alert=True)
    await Alliance.get_motor_collection().update_one({"aid": al.aid}, {"$set": {"member_bank": not al.member_bank}})
    await cb_dep_menu(cb, user)


@router.callback_query(F.data.regexp(r"^al:wd:\d+$"))
async def cb_withdraw(cb: CallbackQuery, user: User) -> None:
    # برداشت از بانک اتحاد برای همه (حتی رهبر) بسته شده؛ پول بانک فقط خرج اتحاد می‌شه.
    await cb.answer("🔒 پول بانک اتحاد قابل برداشت نیست؛ فقط برای خرید نیرو، چاه و جای عضو خرج می‌شه.", show_alert=True)


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
WAR_USAGE = ("⚔️ روی پیام یکی از اعضای اتحاد دشمن ریپلای کن و بنویس:\n<code>حمله ۲۰ تانک ابرامز و ۲۰ ارپیجی زن</code>\n"
             "(پیوی: <code>حمله @یوزر ۲۰ تانک ابرامز</code>)\nنیروها مال کل اتحادن؛ یگان‌ها رو تو «اتحاد ← 🛒 خرید نیرو» ببین.")
_tasks: set = set()


async def _dm(bot, uid: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(uid, text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        pass


def _resolve_amounts(units: dict, have: dict) -> tuple[dict[str, int], Optional[str]]:
    send: dict[str, int] = {}
    for k, n in units.items():
        x = UNITS[k]
        if x["kind"] != "mobile":
            return {}, f"🧱 {x['name']} ثابته و به حمله نمی‌ره؛ خودکار دفاع می‌کنه."
        got = int(have.get(k, 0))
        n = got if n is None else n
        if n <= 0 or got < n:
            return {}, f"🚫 از {war.label(k)} فقط {fa(got)} تا تو ارتش اتحاد آماده‌ست."
        send[k] = n
    return send, None


async def war_text(bot, actor: User, target: Optional[User], text: str, chat_id: int) -> Optional[str]:
    """ورودی مشترک متن «حمله …» نظامی (گروه، پیوی، منو). جنگ بین ارتش مشترک دو اتحاده؛ مدافع خودکار دفاع می‌کنه."""
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
        return "🤝 برای جنگ نظامی باید عضو یه اتحاد باشی (و اتحادت یگان داشته باشه). «اتحاد» رو بزن."
    if not target.alliance_id:
        return f"🏳️ {esc(target.name)} عضو هیچ اتحادی نیست؛ جنگ نظامی فقط بین اتحادهاست."
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    aa, da = await eco.alliance_of(actor), await eco.alliance_of(target)
    if aa is None or da is None:
        return "🤷 اتحاد پیدا نشد."
    if not can_manage(actor, aa):
        return NO_PERM
    units, bad, _ = war.parse_units(text)
    if bad:
        return f"🤔 «{esc(' '.join(bad))}» رو نشناختم.\n" + WAR_USAGE
    send, err = _resolve_amounts(units, aa.army)
    if err:
        return err
    if not send:
        return WAR_USAGE
    wait = await redis_service.cooldown(f"war:{aa.aid}", C.WAR_COOLDOWN_SEC)
    if wait:
        return f"😮‍💨 نیروهای اتحاد هنوز خسته‌ان؛ {dur(wait)} دیگه می‌تونید حمله کنید."
    wait = await redis_service.cooldown(f"warpair:{aa.aid}:{da.aid}", C.WAR_PAIR_COOLDOWN_SEC)
    if wait:
        await redis_service.clear_cooldown(f"war:{aa.aid}")
        return f"⏳ تازه به اتحاد {esc(da.name)} حمله کردید؛ {dur(wait)} دیگه می‌تونید دوباره سراغشون برید (بقیه رو می‌تونید بزنید)."
    flt = {"aid": aa.aid}
    flt.update({f"army.{k}": {"$gte": n} for k, n in send.items()})
    r = await alc().update_one(flt, {"$inc": {f"army.{k}": -n for k, n in send.items()}})
    if r.matched_count != 1:
        await redis_service.clear_cooldown(f"war:{aa.aid}")
        await redis_service.clear_cooldown(f"warpair:{aa.aid}:{da.aid}")
        return "⚠️ نشد (نیروها همین الان تغییر کردن)؛ دوباره امتحان کن."
    await settle_alliance(da.aid)                            # تولید/ماموریت‌های تموم‌شده‌ی مدافع قبل از نبرد تحویل بشه
    bid = await eco.next_seq("battle")
    await Battle(bid=bid, att_id=actor.tg_id, def_id=target.tg_id, att_aid=aa.aid, def_aid=da.aid, att_name=actor.name, def_name=target.name,
                 chat_id=chat_id, att_units=send, resolve_at=now).insert()
    t = asyncio.create_task(_later(bot, bid, 1.0))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    an = mention(actor.tg_id, actor.name)
    return (f"⚔️ <b>جنگ اتحادها!</b>\n{an} از اتحاد <b>{esc(aa.name)}</b> به اتحاد <b>{esc(da.name)}</b> حمله کرد!\n"
            f"🗡 نیروها: {war.units_text(send)}\n🛡 ارتش و پدافند مشترک مدافع خودکار مقابله می‌کنن…")


async def _later(bot, bid: int, sec: float) -> None:
    await asyncio.sleep(sec)
    await resolve(bot, bid)


async def _sub(aid: int, field: str, lost: dict) -> None:
    if not lost:
        return
    stage = {f"{field}.{k}": {"$max": [0, {"$subtract": [{"$ifNull": [f"${field}.{k}", 0]}, n]}]} for k, n in lost.items()}
    await alc().update_one({"aid": aid}, [{"$set": stage}])


def _report(doc: dict, res: dict, names: tuple, parts: tuple, money: dict) -> str:
    an, aa, da = names
    army, fort = parts
    win = res["att_win"]
    dparts = [x for x in (f"ارتش: {war.units_text(army)}" if army else "",
                          f"پدافند: {war.units_text(fort)}" if fort else "") if x]
    lines = [f"⚔️ <b>نتیجه‌ی نبرد</b>\n{aa} 🆚 {da}  (حمله توسط {an})\n",
             f"🗡 مهاجم: {war.units_text(doc['att_units'])}",
             "🛡 مدافع: " + (" · ".join(dparts) if dparts else "بدون نیرو")]
    lines.append("\n<i>میدان‌ها: ضربه‌ی مهاجم→استقامت مدافع | ضربه‌ی مدافع→استقامت مهاجم</i>")
    for c in ("H", "A", "R"):
        a = res["arena"][c]
        if any(a.values()):
            e, nm = C.MIL_CLASSES[c]
            lines.append(f"{e} {nm}: {fa(a['a_off'])}→{fa(a['d_def'])} | {fa(a['d_off'])}→{fa(a['a_def'])}")
    lines.append(f"\n💀 تلفات مهاجم: {war.units_text(res['att_lost'])}\n💀 تلفات مدافع: {war.units_text(res['def_lost'])}")
    lines.append(f"\n🏆 <b>برنده: {aa if win else da}</b>")
    if money.get("got"):
        lines.append(f"💰 غنیمت: {fa(money['got'])} دلار از بانک بازنده رفت تو بانک اتحاد برنده")
    return "\n".join(lines)


async def _refund(doc: dict) -> None:
    """نیروهای مهاجم به ارتش اتحادش برمی‌گرده (نبرد خراب شد)."""
    units = war.clean(doc.get("att_units", {}))
    if units and doc.get("att_aid"):
        await alc().update_one({"aid": doc["att_aid"]}, {"$inc": {f"army.{k}": n for k, n in units.items()}})


async def resolve(bot, bid: int) -> None:
    col = Battle.get_motor_collection()
    doc = await col.find_one_and_update({"bid": bid, "status": "open"}, {"$set": {"status": "resolving", "resolve_at": utcnow()}},
                                        return_document=ReturnDocument.AFTER)
    if not doc:
        return
    try:
        await _apply(bot, doc)
    except Exception:  # noqa: BLE001
        log.exception("war_resolve_error", bid=bid)
        r = await col.update_one({"bid": bid, "status": "resolving"}, {"$set": {"status": "failed"}})
        if r.matched_count == 1:
            await _refund(doc)


async def _apply(bot, doc: dict) -> None:
    col = Battle.get_motor_collection()
    ala = await Alliance.find_one(Alliance.aid == doc["att_aid"])
    ald = await Alliance.find_one(Alliance.aid == doc["def_aid"])
    att, dfn = await eco.get_user(doc["att_id"]), await eco.get_user(doc["def_id"])
    att_units = war.clean(doc["att_units"])
    if ala is None or ald is None:
        await _refund(doc)
        await col.update_one({"bid": doc["bid"]}, {"$set": {"status": "failed"}})
        return
    army, fort = war.clean(ald.army), war.clean(ald.fort)         # کل ارتش و پدافند مشترک مدافع خودکار دفاع می‌کنن
    res = war.simulate(att_units, war.merge(army, fort))
    a_lost, f_lost = war.split_losses(res["def_lost"], army, fort)
    surv_a = {k: n - res["att_lost"].get(k, 0) for k, n in att_units.items()}
    if any(n > 0 for n in surv_a.values()):
        await alc().update_one({"aid": ala.aid}, {"$inc": {f"army.{k}": n for k, n in surv_a.items() if n > 0}})
    await _sub(ald.aid, "army", a_lost)
    await _sub(ald.aid, "fort", f_lost)

    win = res["att_win"]
    w, l = (ala, ald) if win else (ald, ala)
    loser_lost = res["v_def_lost"] if win else res["v_att_lost"]
    money: dict = {}
    bank_l = int((await Alliance.find_one(Alliance.aid == l.aid)).bank)
    amt = war.loot(bank_l, res["margin"], loser_lost)
    if amt > 0:
        r = await alc().update_one({"aid": l.aid, "bank": {"$gte": amt}}, {"$inc": {"bank": -amt}})
        if r.matched_count == 1:
            await alc().update_one({"aid": w.aid}, {"$inc": {"bank": amt}})
            money["got"] = amt
    if att and dfn:
        wu, lu = (att, dfn) if win else (dfn, att)
        await eco.update_user(wu.tg_id, inc={"stats.war_w": 1, "stats.war_loot": money.get("got", 0)})
        await eco.update_user(lu.tg_id, inc={"stats.war_l": 1, "stats.war_lost": money.get("got", 0)})
        await eco.add_xp(wu.tg_id, C.XP["win"])
        await eco.add_xp(lu.tg_id, C.XP["lose"])
    await col.update_one({"bid": doc["bid"]}, {"$set": {"status": "done"}})

    text = _report(doc, res, (mention(doc["att_id"], att.name if att else doc.get("att_name", "")), esc(ala.name), esc(ald.name)),
                   (army, fort), money)
    sent = set()
    for rid in (doc["chat_id"], doc["def_id"], ald.leader_id, ala.leader_id):
        if rid and rid not in sent:
            sent.add(rid)
            await _dm(bot, rid, text)


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


# ───────────────────────── جای عضو جدید (۱ میلیارد، تا سقف ۱۰ نفر) ─────────────────────────
@router.callback_query(F.data == "al:slot")
async def cb_slot(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("👑 فقط رهبر!", show_alert=True)
    n = slots_of(al)
    if n >= C.ALLIANCE_MAX_SLOTS:
        return await cb.answer(f"به سقف {fa(C.ALLIANCE_MAX_SLOTS)} نفر رسیدی!", show_alert=True)
    await cb.answer()
    await edit(cb, (f"➕ <b>جای عضو جدید</b>\n\n👥 ظرفیت الان: {fa(n)} از حداکثر {fa(C.ALLIANCE_MAX_SLOTS)}\n"
                    f"💰 قیمت یک جا: <b>{fa(C.ALLIANCE_SLOT_PRICE)}</b> دلار (از <b>بانک اتحاد</b>)\n"
                    f"🏦 بانک اتحاد: {fa(al.bank)}\n\nمطمئنی؟"),
               kb([b("✅ بخر", "al:slot:ok")], back("al", "◀️ اتحاد")))


@router.callback_query(F.data == "al:slot:ok")
async def cb_slot_ok(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("👑 فقط رهبر!", show_alert=True)
    n = slots_of(al)
    if n >= C.ALLIANCE_MAX_SLOTS:
        return await cb.answer("به سقف رسیدی!", show_alert=True)
    price = C.ALLIANCE_SLOT_PRICE
    r = await Alliance.get_motor_collection().update_one(
        {"aid": al.aid, "bank": {"$gte": price}, "slots": n}, {"$inc": {"bank": -price, "slots": 1}})
    if r.matched_count != 1:
        return await cb.answer(f"💸 بانک اتحاد باید {fa(price)} دلار داشته باشه!", show_alert=True)
    await cb.answer("✅")
    await _show(cb, user.tg_id, f"➕ یه جای عضو جدید باز شد! ظرفیت: {fa(n + 1)}/{fa(C.ALLIANCE_MAX_SLOTS)} ({fa(price)} دلار از بانک اتحاد رفت)")


async def migrate_shared_army() -> dict:
    """یک‌بار در استارت: ارتش/پادگان/پدافند/تولید/ماموریت/چاه/سفرهای تجاری تک‌تک اعضا ← ارتش و دارایی مشترک اتحاد.
    تولید و ماموریت‌های نیمه‌کاره همون لحظه تموم می‌شن (نیرو می‌ره ارتش اتحاد، پاداش/سرمایه می‌ره جیب همون بازیکن)؛
    چاه‌ها جمع می‌شن و درآمد جمع‌شده‌شون به جیب صاحبش می‌ره."""
    uc = eco.col()
    empty = {"army": {}, "garrison": {}, "fort": {}, "prod": {}, "mission": {}, "inc": {}}
    q = {"alliance_id": {"$ne": None}, "$or": [{f: {"$exists": True, "$nin": [{}, None]}} for f in empty]}
    moved = 0
    now = utcnow()
    async for d in uc.find(q, {"tg_id": 1, "alliance_id": 1}):
        al = await alc().find_one({"aid": d["alliance_id"]}, {"level": 1})
        if al is None:
            continue
        old = await uc.find_one_and_update({"tg_id": d["tg_id"], "alliance_id": d["alliance_id"]}, {"$set": empty},
                                           return_document=ReturnDocument.BEFORE)
        if not old:
            continue
        inc: dict[str, int] = {}

        def add(field: str, k: str, n) -> None:
            n = int(n or 0)
            if k in UNITS and n > 0:
                key = f"{field}.{k}"
                inc[key] = inc.get(key, 0) + n

        cash = 0
        for k, n in (old.get("army") or {}).items():
            add("army", k, n)
        for k, n in (old.get("garrison") or {}).items():
            add("army", k, n)
        for k, n in (old.get("fort") or {}).items():
            add("fort", k, n)
        p = old.get("prod") or {}
        if p.get("key") in UNITS:
            add("fort" if UNITS[p["key"]]["kind"] == "fort" else "army", p["key"], p.get("n", 0))
        for m in (old.get("mission") or {}).values():
            for k, n in (m.get("u") or {}).items():
                add("army", k, int(n) - int((m.get("lost") or {}).get(k, 0)))
            cash += int(m.get("reward", 0))
        o = (old.get("inc") or {}).get("oil") or {}
        wells = int(o.get("n", 0))
        if wells > 0:
            t = o.get("t")
            hrs = min(C.OIL_STORE_HOURS, max(0.0, (now - t).total_seconds() / 3600)) if t else 0.0
            cash += int(C.OIL_PRICE * C.OIL_YIELD_PER_HOUR * wells * (1 + C.ALLIANCE_LEVELS[int(al.get("level", 1))][1]) * hrs)
            inc["inc.oil.n"] = wells
        for kind in ("sea", "arms"):
            dd = (old.get("inc") or {}).get(kind)
            if dd:
                cash += int(dd.get("ret", 0))
        upd: dict = {}
        if inc:
            upd["$inc"] = inc
        if wells > 0:
            upd["$set"] = {"inc.oil.t": now}
        if upd:
            await alc().update_one({"aid": d["alliance_id"]}, upd)
        if cash > 0:
            await uc.update_one({"tg_id": d["tg_id"]}, {"$inc": {"cash": cash}})
        moved += 1
    # نبردهای باز قدیمی (قبل از اتحاد-علیه-اتحاد): خراب حساب می‌شن و نیروهاشون برمی‌گرده ارتش اتحاد
    bc = Battle.get_motor_collection()
    old_battles = 0
    async for bd in bc.find({"status": {"$in": ["open", "resolving"]}, "att_aid": {"$exists": False}}):
        r = await bc.update_one({"bid": bd["bid"], "att_aid": {"$exists": False}}, {"$set": {"status": "failed"}})
        if r.matched_count != 1:
            continue
        for uid, units in ((bd.get("att_id"), bd.get("att_units")), (bd.get("def_id"), bd.get("def_units"))):
            usr = await uc.find_one({"tg_id": uid}, {"alliance_id": 1})
            cl = war.clean(units or {})
            if usr and usr.get("alliance_id") and cl:
                await alc().update_one({"aid": usr["alliance_id"]}, {"$inc": {f"army.{k}": n for k, n in cl.items()}})
        old_battles += 1
    return {"members_moved": moved, "old_battles_closed": old_battles}


async def migrate_alliances() -> dict:
    """یک‌بار در استارت: (۱) انبار اتحادِ قدیمی ← پول بانک اتحاد (قیمت کامل)  (۲) ظرفیت اتحادهای قدیمی = تعداد اعضای فعلی (حداقل ۵، حداکثر ۱۰)."""
    alc = Alliance.get_motor_collection()
    arm_n = slot_n = 0
    async for doc in alc.find({"armory": {"$exists": True, "$ne": {}}}):
        old = doc.get("armory") or {}
        r = await alc.update_one({"aid": doc["aid"], "armory": old}, {"$set": {"armory": {}}})
        if r.matched_count != 1:
            continue
        refund = sum(int(UNITS[k]["price"]) * int(n) for k, n in old.items() if k in UNITS and int(n) > 0)
        if refund:
            await alc.update_one({"aid": doc["aid"]}, {"$inc": {"bank": refund}})
        arm_n += 1
    async for doc in alc.find({"slots": {"$exists": False}}):
        members = await User.find(User.alliance_id == doc["aid"]).count()
        await alc.update_one({"aid": doc["aid"], "slots": {"$exists": False}},
                             {"$set": {"slots": int(min(C.ALLIANCE_MAX_SLOTS, max(C.ALLIANCE_BASE_SLOTS, members)))}})
        slot_n += 1
    shared = await migrate_shared_army()
    return {"armory_converted": arm_n, "slots_set": slot_n, **shared}
