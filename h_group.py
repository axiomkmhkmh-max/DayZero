"""دستورهای متنی (گروه و پیوی): حمله، دوز، تاس، ثبت ازدواج، درخواست طلاق، بزن توش (بچه‌دار شدن)، حمله اجنه، جیبی، کد هدیه، دزد، طلسم‌ها …
روی پیام طرف ریپلای کن یا @یوزرنیمش رو بنویس."""
from __future__ import annotations

import contextvars
import re
import time
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

import actions
import combat
import config as C
import duel
import eco
import h_biz
import h_casino
import h_home
import h_market
import h_menu
import rules
from db import User
from redis_service import redis_service
from ui import claim, kb, replying_to_bot, url_b
from util import dur, esc, fa, mention, norm, parse_amount, utcnow

router = Router()

# alias (نرمال‌شده) → (دستور, فقط-وقتی-تنهاست)
_RAW = {
    "attack": (["حمله", "attack"], False),
    "ttt": (["دوز", "ttt"], False),
    "dice": (["تاس", "تاسبازی", "dice"], False),
    "marry": (["ثبت ازدواج", "ازدواج", "marry"], False),
    "divorce": (["درخواست طلاق", "طلاق", "divorce"], True),
    "baby": (["بزن توش", "بکنش", "baby"], True),
    "jinn": (["حمله اجنه", "jinn"], False),
    "pocket": (["جیبی", "پول تو جیبی", "پول جیبی", "پولجیبی", "جیبی بگیر", "جیبی بده", "pocket"], True),
    "slot": (["اسلات", "slot", "slots"], False),
    "wheel": (["گردونه", "چرخونه", "wheel"], False),
    "coin": (["شیرخط", "شیر یا خط", "coin"], False),
    "games": (["بازی", "بازی ها", "بازیها", "قمارخونه", "قمار", "کازینو", "games", "casino"], True),
    "transfer": (["انتقال پول", "انتقال", "transfer", "pay"], False),
    "gift": (["کد هدیه", "gift"], False),
    "vthief": (["دزد گاوصندوق", "دزد", "thief"], False),
    "htheft": (["دزدی از خانه", "دزدی از خونه", "دزدی از کمد", "steal"], False),
    "hack": (["هکش کن", "هک کن", "هک", "hack"], False),
    "market": (["بازار", "market"], True),
    "home": (["خونه", "خانه", "home"], True),
    "biz": (["کاسبی", "biz", "business"], True),
    "love": (["طلسم عاشقی", "love"], False),
    "split": (["طلسم طلاق", "split"], False),
    "wallet": (["جیب", "موجودی", "کیف", "me", "wallet"], True),
    "menu": (["داشبورد", "پنل", "dashboard"], True),
    # 🎲 رندوم: طرف از بین کسانی که تو همین گروه پیام دادن انتخاب می‌شه
    "rmarry": (["ازدواج رندوم", "ثبت ازدواج رندوم", "rmarry"], True),
    "rhack": (["هک رندوم", "هکش کن رندوم", "rhack"], True),
    "rsteal": (["دزد رندوم", "دزدی رندوم", "دزدی از خانه رندوم", "rsteal"], True),
}
RANDOM_CMDS = {"rmarry", "rhack", "rsteal"}
ALIASES = {norm(a): (cmd, solo) for cmd, (als, solo) in _RAW.items() for a in als}
_BOT_RE = re.compile(rf"(?i)@{re.escape(C.BOT_USERNAME)}\b")

USAGE = {
    "attack": "⚔️ روی پیام طرف ریپلای کن و بنویس:\n<code>حمله</code> · <code>حمله کلاشینکف</code> · <code>حمله کلاشینکف و شمشیر</code>",
    "ttt": "❌⭕ روی پیام طرف ریپلای کن و بنویس: <code>دوز 50000</code>",
    "dice": "🎲 روی پیام طرف ریپلای کن و بنویس: <code>تاس 50000</code>",
    "transfer": "💸 روی پیام طرف ریپلای کن و بنویس: <code>انتقال 1000</code>\n(پیوی: <code>انتقال @یوزر 1000</code>)",
    "marry": "💍 روی پیام طرف ریپلای کن و بنویس: <code>ثبت ازدواج</code>\n💰 با مهریه: <code>ثبت ازدواج 1m</code>",
    "jinn": "👹 روی پیام طرف ریپلای کن و بنویس: <code>حمله اجنه</code>",
    "vthief": "🥷 روی پیام طرف ریپلای کن و بنویس: <code>دزد</code>",
    "htheft": "🥷 روی پیام طرف ریپلای کن و بنویس: <code>دزدی از خانه</code>\n(ابزار دزدی تو ‹در برابر› امنیت و نگهبان‌های اون؛ برنده بشی یه وسیله از کمدش مال تو می‌شه)",
    "hack": "💻 روی پیام طرف ریپلای کن و بنویس: <code>هکش کن</code>\n(هکرهای تو ‹در برابر› هکرهای اون؛ برنده بشی ۵ تا ۵۰٪ پولش مال تو می‌شه)",
    "love": "💘 روی پیام طرف ریپلای کن و بنویس: <code>طلسم عاشقی</code>",
    "split": "💔 روی پیام یکی از زوج‌ها ریپلای کن و بنویس: <code>طلسم طلاق</code>",
}


SECTIONS = {"market": h_market.render_market, "home": h_home.render_home, "biz": h_biz.render_biz}


# ═════════════════════ 🎲 رندوم + سقف غنیمت هک/دزدی ═════════════════════
# (همه‌چیز اینجاست تا فقط همین یه فایل عوض بشه؛ اگه خواستی عددها رو بذاری تو config.py با همین اسم‌ها، از اونجا خونده می‌شن)
HACK_CAP = int(getattr(C, "HACK_CAP", 5_000_000))                  # سقف غنیمت هر هک / ارزش وسیله‌ی قابل دزدیدن
RANDOM_LOOT_MULT = float(getattr(C, "RANDOM_LOOT_MULT", 0.2))      # غنیمت هک/دزدی رندوم = ۲۰٪ حالت ریپلای
RANDOM_MARRY_COOLDOWN_SEC = int(getattr(C, "RANDOM_MARRY_COOLDOWN_SEC", 60))
ACTIVE_DAYS = int(getattr(C, "RANDOM_ACTIVE_DAYS", 7))             # فقط کسانی که تو این چند روز تو گروه پیام دادن
_loot_mult: contextvars.ContextVar[float] = contextvars.ContextVar("loot_mult", default=1.0)


async def _capped_hack(bot, actor: User, target: User) -> str:
    """همون «هکش کن» قبلی (actions.hack) ولی با سقف غنیمت ۵ میلیون؛ تو حالت رندوم غنیمت ×۰٫۲ هم می‌شه.
    امضا و متن‌های کلیدی ('هک موفق!') عین قبله تا لاگ و داشبورد سالم بمونن."""
    now = utcnow()
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    atk = rules.hack_power(actor.forces)
    if atk <= 0:
        return "💻 هکر نداری! از «خونه ← نیروها ← هکر» استخدام کن."
    victim = await eco.get_user(target.tg_id)
    if victim.cash + victim.vault < C.HACK_MIN_MONEY:
        return f"🕳 {esc(target.name)} تقریباً پولی نداره؛ ارزش هک نداره."
    if not await redis_service.r.set(f"hack:{actor.tg_id}", "1", nx=True, ex=C.HACK_COOLDOWN_SEC):
        wait = await redis_service.r.ttl(f"hack:{actor.tg_id}")
        return f"💻 هکرهات هنوز خستن؛ {dur(max(wait, 1))} دیگه می‌تونی دوباره هک کنی."
    await combat.drop_shield(actor.tg_id, actor, now)
    dfn = rules.hack_power(victim.forces)
    win, a, d = rules.roll_fight(atk, dfn)
    an, tn = mention(actor.tg_id, actor.name), mention(target.tg_id, target.name)
    head = (f"💻 {an} هکرهاش رو انداخت به جون {tn}!\n"
            f"💻 قدرت هک: <b>{fa(atk)}</b>   🛡 ضدهک: <b>{fa(dfn)}</b>\n")
    if not win:
        await actions.send(bot, target.tg_id, f"🛡 هکرهات جلوی هک {esc(actor.name)} رو گرفتن!")
        return head + f"🛡 <b>هک خنثی شد!</b> هکرهای {tn} جلوی نفوذ رو گرفتن."
    pct = rules.hack_percent(a, d)
    mult = _loot_mult.get()
    fresh = await eco.get_user(target.tg_id)
    want_cash, want_vault = int(fresh.cash * pct), int(fresh.vault * pct)
    scale = min(1.0, HACK_CAP / (want_cash + want_vault)) if (want_cash + want_vault) > 0 else 1.0
    capped = scale < 1.0
    got_cash = got_vault = 0
    amt = int(want_cash * scale * mult)
    if amt > 0 and await eco.transfer(target.tg_id, actor.tg_id, amt):
        got_cash = amt
    amt = int(want_vault * scale * mult)
    if amt > 0 and await eco.transfer(target.tg_id, actor.tg_id, amt, field="vault"):
        got_vault = amt
    total = got_cash + got_vault
    if total <= 0:
        return head + "💨 هک شد ولی پولی پیدا نکردی!"
    await eco.update_user(actor.tg_id, inc={"stats.loot": total})
    await eco.update_user(target.tg_id, inc={"stats.lost": total})
    await actions.send(bot, target.tg_id, f"💻 {esc(actor.name)} هکت کرد و <b>{fa(total)}</b> دلار از پولت رفت! هکر قوی‌تر بخر.")
    notes = ""
    if capped:
        notes += f"\n🧱 سقف غنیمت هک: {fa(HACK_CAP)} دلار"
    if mult < 1:
        notes += f"\n🎲 هک رندوم فقط {fa(int(mult * 100))}٪ غنیمت داره"
    return head + (f"💥 <b>هک موفق!</b> {an} مقدار <b>{fa(total)}</b> دلار از حساب {tn} کشید!"
                   f"\n💵 جیب: {fa(got_cash)}   🔐 گاوصندوق: {fa(got_vault)}{notes}")


if not getattr(actions.hack, "_capped", False):
    _capped_hack._capped = True                     # type: ignore[attr-defined]
    actions.hack = _capped_hack                     # actions.run و داشبورد هم از همین استفاده می‌کنن (قبل از لاگ نصب می‌شه)

# «دزدی از خانه»: فقط وسیله‌هایی با ارزش ≤ سقف قابل دزدیدنن (تو حالت رندوم ≤ سقف × ۰٫۲).
# پس تنها راه دزدی حسابی «دزد گاوصندوق» بازار سیاهه.
if not getattr(rules.pick_closet_item, "_capped", False):
    _orig_pick = rules.pick_closet_item

    def _cheap_pick(gear: dict, items: dict, *a, **k):
        limit = HACK_CAP * _loot_mult.get()
        g = {n: v for n, v in gear.items() if n in rules.SHOP_INDEX and rules.SHOP_INDEX[n]["price"] <= limit}
        i = {n: v for n, v in items.items() if n in rules.DARK_INDEX and rules.DARK_INDEX[n]["price"] <= limit}
        return _orig_pick(g, i, *a, **k)

    _cheap_pick._capped = True                      # type: ignore[attr-defined]
    rules.pick_closet_item = _cheap_pick


# ───────── ردیابی کسانی که تو گروه پیام دادن ─────────
async def _track(m: Message) -> None:
    if m.chat.type == "private" or m.from_user is None or m.from_user.is_bot:
        return
    try:
        key = f"gmem:{m.chat.id}"
        await redis_service.r.zadd(key, {str(m.from_user.id): time.time()})
        await redis_service.r.expire(key, ACTIVE_DAYS * 86400)
    except Exception:  # noqa: BLE001
        pass


async def _random_target(m: Message, actor: User, accept) -> Optional[User]:
    """یه بازیکن شانسی از بین کسانی که تو همین گروه پیام دادن (و ثبت‌نام کردن) که accept(u) رو قبول کنه."""
    key = f"gmem:{m.chat.id}"
    await redis_service.r.zremrangebyscore(key, 0, time.time() - ACTIVE_DAYS * 86400)
    ids = [int(x) for x in await redis_service.r.zrange(key, 0, -1)]
    rules.RNG.shuffle(ids)
    for uid in ids[:80]:
        if uid == actor.tg_id:
            continue
        u = await eco.get_user(uid)
        if u is not None and await accept(u):
            return u
    return None


async def random_cmd(m: Message, cmd: str, actor: User) -> None:
    if m.chat.type == "private":
        return await m.answer("🎲 دستورهای رندوم فقط توی گروه کار می‌کنن (طرف از بین کسایی که تو گروه پیام دادن انتخاب می‌شه).")
    reply, now, r = m.reply, utcnow(), redis_service.r
    none_msg = "😕 کسی پیدا نشد! باید توی همین گروه چند نفر پیام داده باشن و ثبت‌نام کرده باشن (و برای هک/دزدی بی‌محافظ باشن)."

    if cmd == "rmarry":
        if actor.spouse_id:
            return await reply("💍 تو که متاهلی! اول «درخواست طلاق» بده.")
        wait = await redis_service.cooldown(f"rmarry:{actor.tg_id}", RANDOM_MARRY_COOLDOWN_SEC)
        if wait:
            return await reply(f"⏳ {dur(wait)} دیگه می‌تونی دوباره ازدواج رندوم بزنی.")

        async def ok(u: User) -> bool:
            return not u.spouse_id and not await r.exists(f"nomarry:{min(actor.tg_id, u.tg_id)}:{max(actor.tg_id, u.tg_id)}")
        target = await _random_target(m, actor, ok)
        if target is None:
            await redis_service.clear_cooldown(f"rmarry:{actor.tg_id}")
            return await reply(none_msg.replace(" (و برای هک/دزدی بی‌محافظ باشن)", "") + "\n(باید مجرد باشن)")
        await reply(f"🎲💍 <b>ازدواج رندوم!</b> قرعه به نام {mention(target.tg_id, target.name)} افتاد 😏")
        res = await actions.marry(m.bot, actor, target, m.chat.id, True)
        if res:
            await reply(res)
        return

    mult = RANDOM_LOOT_MULT
    if cmd == "rhack":
        if rules.hack_power(actor.forces) <= 0:
            return await reply("💻 هکر نداری! از «خونه ← نیروها ← هکر» استخدام کن.")
        ttl = await r.ttl(f"hack:{actor.tg_id}")
        if ttl > 0:
            return await reply(f"💻 هکرهات هنوز خستن؛ {dur(ttl)} دیگه می‌تونی دوباره هک کنی.")

        async def ok(u: User) -> bool:
            return await combat.hostile_checks(actor, u, now) is None and u.cash + u.vault >= C.HACK_MIN_MONEY
        label, action = "هک رندوم", "hack"
    else:  # rsteal
        ttl = await r.ttl(f"htheft:{actor.tg_id}")
        if ttl > 0:
            return await reply(f"🥷 هنوز خسته‌ای؛ {dur(ttl)} دیگه می‌تونی دوباره بری دزدی.")

        async def ok(u: User) -> bool:
            return await combat.hostile_checks(actor, u, now) is None and rules.pick_closet_item(u.gear, u.items) is not None
        label, action = "دزد رندوم", "htheft"

    tok = _loot_mult.set(mult)
    try:
        target = await _random_target(m, actor, ok)
        if target is None:
            return await reply(none_msg)
        text = await actions.run(m.bot, action, actor, target, m.chat.id, True)
    finally:
        _loot_mult.reset(tok)
    if text:
        extra = f"\n🎲 دزد رندوم فقط وسیله‌های تا {fa(int(HACK_CAP * mult))} دلاری رو می‌تونه بدزده" if action == "htheft" else ""
        await reply(f"🎲 <b>{label}</b>: قرعه به نام {mention(target.tg_id, target.name)} افتاد!\n\n{text}{extra}")


# ───────── صفحه‌ی «دستورات» تو راهنما ─────────
_CMD_PAGE = f"""📜 <b>همه‌ی دستورات</b> <i>(گروه: ریپلای روی طرف · پیوی: @یوزر)</i>

<b>💰 پول و بازی</b>
<code>جیب</code> / <code>موجودی</code> · <code>جیبی</code> (ساعتی) · <code>انتقال 1000</code> · <code>کد هدیه کدت</code> (فقط پیوی)
<code>بازار</code> · <code>خونه</code> · <code>کاسبی</code> · <code>پنل</code> / <code>داشبورد</code>
<code>بازی</code> / <code>قمارخونه</code> · <code>اسلات 5000</code> · <code>گردونه 5000</code> · <code>شیرخط 5000 شیر</code>
<code>دوز 50000</code> · <code>تاس 50000</code> (با ریپلای = شرط با آدم، بدون ریپلای = با خونه)

<b>⚔️ مبارزه</b>
<code>حمله</code> · <code>حمله کلاشینکف</code> · <code>حمله کلاشینکف و شمشیر</code>
<code>هکش کن</code> (سقف غنیمت {fa(HACK_CAP)}) · <code>دزدی از خانه</code> (فقط وسیله‌های تا {fa(HACK_CAP)} دلاری)
<code>دزد</code> (دزد گاوصندوق، ۲۰ تا ۵۰٪ گاوصندوق؛ از بازار سیاه)
<code>حمله اجنه</code> · <code>طلسم عاشقی</code> · <code>طلسم طلاق</code> (از دارک وب)

<b>💍 خانواده</b>
<code>ثبت ازدواج</code> · <code>ثبت ازدواج 1m</code> (با مهریه) · <code>درخواست طلاق</code> · <code>بزن توش</code> / <code>بکنش</code>

<b>🎲 رندوم</b> <i>(طرف از بین کسایی که تو همین گروه پیام دادن)</i>
<code>ازدواج رندوم</code> ← پیشنهاد با دکمه‌ی قبول/رد
<code>هک رندوم</code> ← غنیمت {fa(int(RANDOM_LOOT_MULT * 100))}٪ حالت ریپلای
<code>دزد رندوم</code> ← دزدی از خونه‌ی یه نفر؛ فقط وسیله‌های تا {fa(int(HACK_CAP * RANDOM_LOOT_MULT))} دلاری"""

if not any(name == "📜 دستورات" for name, _ in h_menu.HELP_PAGES):
    h_menu.HELP_PAGES.append(("📜 دستورات", _CMD_PAGE))


def match_command(text: str) -> Optional[tuple[str, bool, list[str]]]:
    """(دستور, با-اسلش؟, توکن‌های باقی‌مانده) یا None."""
    text = _BOT_RE.sub("", text).strip()
    toks = text.split()
    if not toks:
        return None
    slash = toks[0].startswith("/")
    if slash:
        toks[0] = re.sub(r"@\w+$", "", toks[0])
    for n in (3, 2, 1):
        if len(toks) < n:
            continue
        hit = ALIASES.get(norm("".join(toks[:n])))
        if hit:
            cmd, solo = hit
            if solo and len(toks) != n:
                continue
            return cmd, slash, toks[n:]
    return None


def _has_target_hint(m: Message) -> bool:
    """آیا پیام برای یک بازیکن دیگه‌ست (ریپلای / @یوزر / #آیدی)؟ برای تشخیص «تاس با آدم» از «تاس با خونه»."""
    r = m.reply_to_message
    if r and r.from_user and not r.from_user.is_bot and r.from_user.id != m.from_user.id:
        return True
    if any(e.type in ("mention", "text_mention") for e in (m.entities or [])):
        return True
    return any(t.startswith("#") and t[1:].isdigit() for t in (m.text or "").split())


async def find_target(m: Message) -> Optional[User]:
    r = m.reply_to_message
    if r and r.from_user and not r.from_user.is_bot and r.from_user.id != m.from_user.id:
        return (await eco.ensure_user(r.from_user))[0]
    for ent in m.entities or []:
        if ent.type == "text_mention" and ent.user and not ent.user.is_bot and ent.user.id != m.from_user.id:
            return (await eco.ensure_user(ent.user))[0]
        if ent.type == "mention":
            name = m.text[ent.offset + 1: ent.offset + ent.length]
            if name.lower() == C.BOT_USERNAME.lower() or name.lower() == (m.from_user.username or "").lower():
                continue
            t = await eco.find_by_username(name)
            if t:
                return t
    for tok in (m.text or "").split():
        if tok.startswith("#") and tok[1:].isdigit():
            t = await eco.get_user(int(tok[1:]))
            if t:
                return t
    return None


async def process(m: Message, cmd: str, slash: bool, rest: list[str], actor: User, in_group: bool) -> None:
    reply = m.reply if in_group else m.answer

    if cmd in RANDOM_CMDS:
        return await random_cmd(m, cmd, actor)
    if cmd == "menu":
        return await h_menu.open_menu(m, actor)
    if cmd in SECTIONS:                      # «بازار» / «خونه» / «کاسبی»: همون بخش مستقیم باز می‌شه
        text, markup = await SECTIONS[cmd](actor)
        sent = await reply(text, reply_markup=markup)
        if in_group:
            await claim(sent, actor.tg_id)
        return sent
    if cmd == "wallet":
        atk, _ = rules.attack_power(actor.gear, actor.forces)
        dfn, _, _ = rules.defense_power(actor.gear, actor.forces)
        return await reply(f"👤 <b>{esc(actor.name)}</b> جان، اینم وضعیتت:\n"
                           f"💵 جیب: <b>{fa(actor.cash)}</b>   🔐 گاوصندوق: <b>{fa(actor.vault)}</b>\n"
                           f"👶 بچه: {fa(actor.kids)}   ⚔️ حمله: {fa(atk)}   🛡 دفاع: {fa(dfn)}")
    if cmd == "games":
        text, markup = await h_casino.render_hub(actor)
        sent = await reply(text, reply_markup=markup)
        if in_group:
            await claim(sent, actor.tg_id)
        return sent
    if cmd in ("slot", "wheel", "coin") or (cmd == "dice" and not _has_target_hint(m)):
        if cmd == "dice" and not [t for t in rest if parse_amount(t, actor.cash)]:
            if in_group and not slash:                     # «تاس» خالی توی گفتگوی عادی: ساکت
                return
        return await h_casino.text_play(m, actor, cmd, rest, in_group)
    if cmd == "baby":
        ok, text = await actions.baby(actor.tg_id, group=in_group)
        text += await actions.intimacy(m.bot, actor.tg_id, group=in_group)
        sent = await reply(text)
        if ok and in_group:        # فقط ریپلای روی «همین» پیام اسم بچه می‌شه (نه هر پیام رباتی)
            await redis_service.r.set(f"kidname:{actor.tg_id}", f"{sent.chat.id}:{sent.message_id}", ex=actions.KID_NAME_TTL)
        return sent
    if cmd == "pocket":
        ok, text = await actions.pocket(actor.tg_id)
        return await reply(text)
    if cmd == "gift":
        if in_group:                       # کد نباید جلوی بقیه استفاده بشه
            return await reply("🎁 کد هدیه رو تو <b>پیوی ربات</b> بفرست: <code>کد هدیه کدت</code>")
        code = next((t for t in rest if not t.startswith("@")), "")
        if not code:
            return await reply("🎁 کد هدیه رو این‌جوری بفرست: <code>کد هدیه کدت</code>")
        ok, text = await actions.redeem_gift(actor.tg_id, code)
        return await reply(text)
    if cmd == "divorce":
        res = await actions.divorce_request(m.bot, actor, m.chat.id, in_group)
        if res:
            await reply(res)
        return

    target = await find_target(m)
    if target is None:
        if slash or not in_group:           # توی گروه برای پیام‌های معمولی ساکت می‌مونیم
            await reply(USAGE.get(cmd, "🎯 روی پیام طرف ریپلای کن یا @یوزرنیمش رو بنویس."))
        return

    if cmd in ("ttt", "dice"):
        amount = None
        for tok in rest:
            if tok.startswith(("@", "#")):
                continue
            amount = parse_amount(tok, actor.cash)
            if amount:
                break
        if not amount:
            return await reply(USAGE[cmd] + f"\n(مبلغ بین {fa(C.MIN_BET)} و {fa(C.MAX_BET)})")
        err = await duel.create(m.bot, cmd, actor, await eco.get_user(target.tg_id), amount, m.chat.id, in_group)
        if err:
            await reply(err)
        return

    if cmd == "transfer":
        amount = None
        for tok in rest:
            if tok.startswith(("@", "#")):
                continue
            amount = parse_amount(tok, actor.cash)
            if amount:
                break
        if not amount:
            return await reply(USAGE["transfer"])
        if amount < C.MIN_TRANSFER:
            return await reply(f"💸 حداقل مبلغ انتقال {fa(C.MIN_TRANSFER)} دلاره.")
        if not await eco.transfer(actor.tg_id, target.tg_id, amount):
            return await reply("💸 پول کافی تو جیبت نداری!")
        await actions.send(m.bot, target.tg_id, f"💸 {esc(actor.name)} مبلغ <b>{fa(amount)}</b> دلار برات فرستاد!")
        return await reply(f"💸 <b>{fa(amount)}</b> دلار از {mention(actor.tg_id, actor.name)} "
                           f"به {mention(target.tg_id, target.name)} منتقل شد ✅")

    extra = " ".join(t for t in rest if not t.startswith(("@", "#")))
    action = {"attack": "attack", "marry": "marry", "jinn": "jinn", "vthief": "vthief", "htheft": "htheft", "hack": "hack", "love": "love", "split": "split"}[cmd]
    text = await actions.run(m.bot, action, actor, target, m.chat.id, in_group, extra)
    if text:
        await reply(text)


async def kid_name(m: Message) -> None:
    """بعد از «بزن توش» موفق (فقط توی گروه): ریپلای روی پیام ربات می‌شه اسم بچه."""
    if m.chat.type == "private" or m.text.startswith("/") or not replying_to_bot(m):
        return
    key = f"kidname:{m.from_user.id}"
    want = await redis_service.r.get(key)
    r = m.reply_to_message
    if not want or not r or want != f"{m.chat.id}:{r.message_id}":
        return
    reply = m.reply
    name = " ".join(m.text.split())
    if name in ("لغو", "cancel"):
        await redis_service.r.delete(key)
        return await reply("👌 باشه، بچه‌ات بی‌اسم موند. (بعداً می‌تونی اسمش رو بذاری.)")
    if len(name) > actions.KID_NAME_MAX:
        return await reply(f"✏️ اسم خیلی بلنده؛ حداکثر {fa(actions.KID_NAME_MAX)} حرف. یه اسم کوتاه‌تر بفرست.")
    if not await redis_service.r.delete(key):          # فقط یک بار مصرف می‌شه
        return
    await eco.col().update_one({"tg_id": m.from_user.id}, {"$push": {"kid_names": name}})
    await reply(f"🍼 اسم بچه شد <b>{esc(name)}</b> 💙 خدا حفظش کنه!")


@router.message(F.text, StateFilter(None))
async def on_text(m: Message, user: User = None) -> None:
    if m.from_user is None or m.from_user.is_bot or not m.text:
        return
    await _track(m)
    hit = match_command(m.text)
    if hit is None:
        return await kid_name(m)
    cmd, slash, rest = hit
    if m.chat.type == "private":                       # پیوی: میان‌افزار قبلاً قفل و کاربر رو آماده کرده
        return await process(m, cmd, slash, rest, user, False)
    if not await redis_service.r.set(f"gthr:{m.from_user.id}", "1", nx=True, px=900):
        return
    async with redis_service.lock(f"u:{m.from_user.id}", ttl=20, wait=6):
        actor, _ = await eco.ensure_user(m.from_user)
        await process(m, cmd, slash, rest, actor, True)


@router.message(~F.text, F.chat.type.in_({"group", "supergroup"}))
async def on_other(m: Message) -> None:
    """استیکر/عکس/… هم یعنی تو گروه پیام داده (برای انتخاب رندوم)."""
    await _track(m)
