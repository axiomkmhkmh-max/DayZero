"""کارهای روی بازیکن دیگر: حمله، هک، دزدی از خانه، ازدواج، طلاق، بچه، جن، دزد گاوصندوق، طلسم‌ها.
هر تابع متن پاسخ را برمی‌گرداند؛ مشترک بین پیوی (داشبورد) و گروه (دستور متنی)."""
from __future__ import annotations

import random
import time
from datetime import timedelta
from typing import Optional

from aiogram.types import InlineKeyboardMarkup

import combat
import config as C
import eco
import rules
from db import GiftCode, User
from redis_service import redis_service
from ui import b, kb
from util import dur, esc, fa, left, mention, utcnow


async def send(bot, chat_id: int, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> bool:
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
        return True
    except Exception:  # noqa: BLE001
        return False


def _pair(a: int, b_: int) -> str:
    return f"{min(a, b_)}:{max(a, b_)}"


def steal_key(kind: str, attacker: int, target: int) -> str:
    """کلید کولداون «روی هر نفر» برای هک (hack) / دزدی از خانه (htheft) / دزد گاوصندوق (vthief)."""
    return f"pcd:{kind}:{attacker}:{target}"


async def steal_gate(kind: str, attacker: int, target: int) -> int:
    """کولداون مهاجم روی همین هدف رو شروع می‌کنه. مجاز بود 0، وگرنه ثانیه‌ی باقی‌مانده."""
    key = steal_key(kind, attacker, target)
    if await redis_service.r.set(key, "1", nx=True, ex=int(C.PAIR_STEAL_COOLDOWN_SEC)):
        return 0
    return max(1, await redis_service.r.ttl(key))


# ───────────────────────── پول تو جیبی ─────────────────────────
async def pocket(uid: int) -> tuple[bool, str]:
    """«جیبی» / «پول تو جیبی»: ۱٬۰۰۰ تا ۱۰٬۰۰۰ دلار شانسی، ساعتی یک بار."""
    u = await eco.get_user(uid)
    if u is None:
        return False, "اول /start بزن."
    w = left(u.pocket_ready_at)
    if w > 0:
        return False, f"⏳ پول تو جیبیت رو گرفتی؛ {dur(w)} دیگه دوباره بیا."
    now = utcnow()
    amount = rules.roll_pocket()
    ok = await eco.update_user(uid, cash=amount,
                               set_={"pocket_ready_at": now + timedelta(hours=C.POCKET_COOLDOWN_H)},
                               flt={"$or": [{"pocket_ready_at": None}, {"pocket_ready_at": {"$lte": now}}]})
    if not ok:
        return False, "⏳ یه لحظه بعد دوباره امتحان کن."
    return True, (f"💸 <b>پول تو جیبی!</b> +{fa(amount)} دلار اومد تو جیبت 😎\n"
                  f"⏳ دفعه‌ی بعد: {fa(C.POCKET_COOLDOWN_H)} ساعت دیگه")


# ───────────────────────── وقت عاشقانه (بکنش / بزن توش) ─────────────────────────
async def intimacy_wait(uid: int) -> int:
    """ثانیه‌ی باقی‌مانده‌ی کولداون مشترک زوج (۰ = آزاد یا مجرد)."""
    u = await eco.get_user(uid)
    if u is None or not u.spouse_id:
        return 0
    sp = await eco.get_user(u.spouse_id)
    if sp is None or sp.spouse_id != uid:
        return 0
    ttl = await redis_service.r.ttl(f"intim:{_pair(uid, sp.tg_id)}")
    return max(0, ttl)


async def intimacy(bot, uid: int, group: bool = False) -> str:
    """جایزه‌ی زوج‌ها: هر ۲ دقیقه یک بار (کولداون مشترک)، هر کدوم ۵۰۰ تا ۳٬۰۰۰ دلار شانسی.
    متن اضافه‌ای برمی‌گردونه که به پیام «بزن توش» چسبونده می‌شه ('' = چیزی نشون نده)."""
    u = await eco.get_user(uid)
    if u is None:
        return ""
    if not u.spouse_id:
        return "\n\n💍 <i>جایزه‌ی وقت عاشقانه مخصوص زوج‌هاست؛ اول ازدواج کن (ثبت ازدواج).</i>"
    sp = await eco.get_user(u.spouse_id)
    if sp is None or sp.spouse_id != uid:
        return ""
    key = f"intim:{_pair(uid, sp.tg_id)}"
    cd = C.INTIMACY_COOLDOWN_MIN * 60
    if not await redis_service.r.set(key, "1", nx=True, ex=cd):
        ttl = await redis_service.r.ttl(key)
        return f"\n\n⏳ جایزه‌ی زوجی بعد از <b>{dur(max(1, ttl))}</b> دیگه آماده‌ست."
    mine, theirs = rules.roll_intimacy(), rules.roll_intimacy()
    await eco.gain(uid, mine)
    await eco.gain(sp.tg_id, theirs)
    if not group:
        await send(bot, sp.tg_id, f"💞 {esc(u.name)} باهات وقت عاشقانه گذروند! <b>+{fa(theirs)}</b> دلار گرفتی 💸")
    return (f"\n\n💞 <b>جایزه‌ی زوجی!</b>\n"
            f"{mention(uid, u.name)}: <b>+{fa(mine)}</b> دلار\n"
            f"{mention(sp.tg_id, sp.name)}: <b>+{fa(theirs)}</b> دلار\n"
            f"⏳ دفعه‌ی بعد: {fa(C.INTIMACY_COOLDOWN_MIN)} دقیقه دیگه")


# ───────────────────────── کد هدیه ─────────────────────────
async def redeem_gift(uid: int, raw_code: str) -> tuple[bool, str]:
    code = raw_code.strip().upper()
    col = GiftCode.get_motor_collection()
    doc = await col.find_one({"code": code})
    if doc is None:
        return False, "🎁 این کد هدیه وجود نداره."
    if uid in doc.get("used_by", []):
        return False, "🎁 قبلاً از این کد استفاده کردی."
    r = await col.update_one(      # اتمیک: فقط اگه ظرفیت مونده و این بازیکن قبلاً نزده
        {"code": code, "used_by": {"$ne": uid}, "$expr": {"$lt": [{"$size": "$used_by"}, "$max_uses"]}},
        {"$push": {"used_by": uid}})
    if r.matched_count != 1:
        return False, "🎁 ظرفیت این کد هدیه تموم شده."
    amount = int(doc["amount"])
    await eco.gain(uid, amount)
    return True, f"🎉 کد هدیه ثبت شد! <b>+{fa(amount)}</b> دلار اومد تو جیبت 🎁"


# ───────────────────────── بچه ─────────────────────────
BED_LINE = "🛏 حسابی باهم روی تخت عشق حال کردین 😏🔥"
KID_NAME_TTL = 15 * 60
KID_NAME_MAX = 20


async def baby(uid: int, group: bool = False) -> tuple[bool, str]:
    """بچه‌دار شدن (دستور «بزن توش» و دکمه‌ی داشبورد). group=True: متن کامل با جمله‌ی تخت و اسم‌گذاری.
    اگه متاهل باشی، همسرت هم یه بچه می‌گیره (اگه خونه، ظرفیت و تایمر تمومش‌شده داشته باشه) و کولداون برای هر دو شروع می‌شه.
    فقط تایمر خودِ بچه‌دارکننده چک می‌شه، پس موز/معجونی که خریده کار می‌کنه."""
    u = await eco.get_user(uid)
    if u is None:
        return False, "اول /start بزن."
    if not u.house:
        return False, f"🏠 اول باید خونه‌ی سطح ۱ بخری ({fa(C.HOUSES[1][1])} دلار): داشبورد ← خونه ← ارتقای خونه"
    cap = C.HOUSES[u.house][0]
    if u.kids >= cap:
        return False, f"🏠 خونه‌ات ظرفیت {fa(cap)} بچه داره و پره؛ خونه رو ارتقا بده."

    sp = None
    if u.spouse_id:
        sp = await eco.get_user(u.spouse_id)
        if sp is not None and sp.spouse_id != uid:
            sp = None
    w = left(u.child_ready_at)                              # فقط تایمر خودت جلوی بچه‌دار شدن رو می‌گیره (موز/معجون برای خودته)
    if w > 0:
        if not group:
            return False, f"⏳ هنوز زوده! {dur(w)} دیگه (موز و معجون تایمر رو کم می‌کنن)."
        return False, (f"{BED_LINE}\n\n⏳ ولی بچه‌دار نشدین! شما تا <b>{dur(w)}</b> دیگه نمی‌تونین بچه‌دار بشین."
                       f"\n(🍌 موز و 🧪 معجون تایمر رو کم می‌کنن)")

    await eco.settle(uid)
    cd_at = utcnow() + timedelta(hours=C.CHILD_COOLDOWN_H)
    ok = await eco.update_user(uid, inc={"kids": 1}, flt={"kids": {"$lt": cap}}, set_={"child_ready_at": cd_at})
    if not ok:
        return False, "🏠 ظرفیت خونه پره!"

    await eco.add_xp(uid, C.XP["baby"])
    sp_line = ""
    if sp is not None:                                     # همسر هم بچه‌دار می‌شه (اگه تایمر خودش هم تموم شده باشه)
        if left(sp.child_ready_at) > 0:
            sp_line = f"\n⏳ تایمر {esc(sp.name)} هنوز تموم نشده؛ بچه فقط برای تو ثبت شد."
        elif not sp.house:
            sp_line = f"\n💍 {esc(sp.name)} هنوز خونه نداره، برای همین بچه‌ی همسر ثبت نشد."
        else:
            sp_cap = C.HOUSES[sp.house][0]
            await eco.settle(sp.tg_id)
            sp_ok = await eco.update_user(sp.tg_id, inc={"kids": 1}, flt={"kids": {"$lt": sp_cap}},
                                          set_={"child_ready_at": cd_at})
            if sp_ok:
                sp_line = f"\n👶 {esc(sp.name)} هم یه بچه گرفت! ({fa(sp.kids + 1)}/{fa(sp_cap)})"
            else:
                sp_line = f"\n🏠 خونه‌ی {esc(sp.name)} پره؛ بچه فقط برای تو ثبت شد."

    hungry = "" if (u.cook or left(u.fed_until) > 0) else "\n🍽 یادت نره بهشون غذا بدی تا کار کنن!"
    if not group:
        return True, (f"👶 مبارک! بچه‌ی جدیدت به دنیا اومد 🎉\nخانواده: {fa(u.kids + 1)}/{fa(cap)} بچه — هر بچه‌ی سیر +۲۵٪ درآمد!"
                      f"{sp_line}\n⏳ بچه‌ی بعدی: {fa(C.CHILD_COOLDOWN_H)} ساعت دیگه{hungry}")
    return True, (f"{BED_LINE}\n\n👶 مبارک! بچه‌دار شدین 🎉\nخانواده: {fa(u.kids + 1)}/{fa(cap)} بچه — هر بچه‌ی سیر +۲۵٪ درآمد!"
                  f"{sp_line}\n⏳ بچه‌ی بعدی: {fa(C.CHILD_COOLDOWN_H)} ساعت دیگه{hungry}"
                  f"\n\n✏️ <b>اسم بچه رو چی بذاریم؟</b> روی همین پیام ریپلای کن و اسمش رو بفرست "
                  f"({fa(KID_NAME_TTL // 60)} دقیقه فرصت داری).")


# ───────────────────────── ازدواج ─────────────────────────
async def marry(bot, actor: User, target: User, chat_id: int, in_group: bool, mahr: int = 0) -> Optional[str]:
    """«ثبت ازدواج [مهریه]»: پیشنهاد با دکمه‌ی قبول/رد برای طرف. مهریه (اختیاری) رو پیشنهاددهنده به طرف بدهکاره."""
    if actor.tg_id == target.tg_id:
        return "😅 با خودت که نمی‌شه ازدواج کرد!"
    if actor.spouse_id:
        return "💍 تو که متاهلی! اول «درخواست طلاق» بده."
    if target.spouse_id:
        return f"💔 {esc(target.name)} متاهله."
    if await redis_service.r.exists(f"nomarry:{_pair(actor.tg_id, target.tg_id)}"):
        return "🚫 به‌خاطر طلسم طلاق، این دو نفر تا مدتی نمی‌تونن با هم ازدواج کنن."
    mahr = max(0, min(int(mahr or 0), C.MAHR_MAX))
    if mahr > actor.cash + actor.vault:
        return f"💰 مهریه‌ای که گفتی ({fa(mahr)}) از کل پولت (جیب + گاوصندوق) بیشتره!"
    if not await redis_service.r.set(f"mrprop:{actor.tg_id}:{target.tg_id}", "1", nx=True, ex=60):
        return "⏳ همین الان پیشنهاد دادی؛ یه کم صبر کن."
    mahr_line = f"\n💰 مهریه: <b>{fa(mahr)}</b> دلار (اگه طلاق بدی و {esc(target.name)} مطالبه کنه، باید بپردازی)" if mahr else ""
    text = (f"💍 {mention(actor.tg_id, actor.name)} به {mention(target.tg_id, target.name)} پیشنهاد ازدواج داد!{mahr_line}\n"
            f"بونوس ازدواج: +{int(C.MARRIAGE_BONUS * 100)}٪ درآمد کاسبی برای هر دو. قبول می‌کنی؟")
    markup = kb([b("✅ قبول می‌کنم", f"mr:y:{actor.tg_id}:{target.tg_id}:{mahr}"),
                 b("❌ رد", f"mr:n:{actor.tg_id}:{target.tg_id}:{mahr}")])
    if in_group:
        await send(bot, chat_id, text, markup)
        return None
    if not await send(bot, target.tg_id, text, markup):
        return "😕 نتونستم به طرف پیام بدم (باید ربات رو استارت کرده باشه). توی گروه با ریپلای امتحان کن."
    return f"📨 پیشنهاد ازدواج برای {esc(target.name)} فرستاده شد."


async def random_marry(actor: User, target: User) -> str:
    """«ازدواج رندوم»: بدون پیشنهاد و دکمه‌ی قبول/رد؛ همون لحظه ازدواج ثبت می‌شه (بدون مهریه).
    اگه خوششون نیومد، هرکدوم «درخواست طلاق» بنویسه، فوری و بدون نظر طرف مقابل طلاق می‌گیره."""
    if actor.spouse_id:
        return "💍 تو که متاهلی! اول «درخواست طلاق» بده."
    if target.spouse_id:
        return f"💔 {esc(target.name)} متاهله."
    if await redis_service.r.exists(f"nomarry:{_pair(actor.tg_id, target.tg_id)}"):
        return "🚫 به‌خاطر طلسم طلاق، این دو نفر تا مدتی نمی‌تونن با هم ازدواج کنن."
    if await redis_service.r.exists(f"rmrecent:{_pair(actor.tg_id, target.tg_id)}"):
        return "🎲 این دو نفر تازه رندوم ازدواج کرده بودن؛ یه مدت دیگه می‌تونن دوباره."
    if not await do_marry(actor.tg_id, target.tg_id, rand=True):
        return "💔 نشد! یکی‌تون همین الان ازدواج کرد؛ دوباره امتحان کن."
    await redis_service.r.set(f"rmrecent:{_pair(actor.tg_id, target.tg_id)}", "1",
                              ex=int(C.RANDOM_MARRY_REPEAT_HOURS * 3600))
    return (f"💍🎉 {mention(actor.tg_id, actor.name)} و {mention(target.tg_id, target.name)} ازدواج کردن! مبارک باشه 💞\n"
            f"+{int(C.MARRIAGE_BONUS * 100)}٪ درآمد کاسبی برای هر دو.\n"
            f"<i>خوشت نیومد؟ بنویس «درخواست طلاق» — بدون نیاز به قبول طرف، همون لحظه طلاق ثبت می‌شه.</i>")


async def do_marry(a: int, b_: int, lock_hours: int = 0, mahr: int = 0, rand: bool = False) -> bool:
    """ازدواج اتمیک (هر دو باید مجرد باشن)."""
    await eco.settle_many(a, b_)
    lock = utcnow() + timedelta(hours=lock_hours) if lock_hours else None
    to = b_ if mahr else None                      # مهریه رو a (پیشنهاددهنده) به b_ بدهکاره
    if not await eco.update_user(a, set_={"spouse_id": b_, "love_lock_until": lock, "mahr": mahr, "mahr_to": to, "rand_marriage": rand}, flt={"spouse_id": None}):
        return False
    if not await eco.update_user(b_, set_={"spouse_id": a, "love_lock_until": lock, "mahr": mahr, "mahr_to": to, "rand_marriage": rand}, flt={"spouse_id": None}):
        await eco.update_user(a, set_={"spouse_id": None, "love_lock_until": None, "mahr": 0, "mahr_to": None, "rand_marriage": False})
        return False
    return True


async def do_divorce(a: int, b_: int, ban_hours: int = 0) -> None:
    await eco.settle_many(a, b_)
    for uid in (a, b_):
        await eco.update_user(uid, set_={"spouse_id": None, "love_lock_until": None, "mahr": 0, "mahr_to": None, "rand_marriage": False})
    if ban_hours:
        await redis_service.r.set(f"nomarry:{_pair(a, b_)}", "1", ex=ban_hours * 3600)


async def pay_mahr(payer: int, receiver: int, amount: int) -> bool:
    """پرداخت اتمیک مهریه: اول از جیب، بعد از گاوصندوق. اگه کل مبلغ نبود هیچی کم نمی‌شه."""
    if amount <= 0:
        return True
    u = await eco.get_user(payer)
    if u is None:
        return False
    c = min(u.cash, amount)
    v = amount - c
    if v > u.vault:
        return False
    r = await eco.col().update_one({"tg_id": payer, "cash": {"$gte": c}, "vault": {"$gte": v}},
                                   {"$inc": {"cash": -c, "vault": -v}})
    if r.matched_count != 1:
        return False
    await eco.gain(receiver, amount)
    return True


async def divorce_request(bot, actor: User, chat_id: int, in_group: bool) -> Optional[str]:
    """«درخواست طلاق»: برای همسر می‌ره؛ اگه قبول کنه طلاق ثبت می‌شه. (بعد ۲۴ ساعت می‌شه یک‌طرفه گرفت.)"""
    if not actor.spouse_id:
        return "💍 تو مجردی!"
    lock = left(actor.love_lock_until)
    if lock > 0:
        return f"💘 طلسم عاشقی فعاله؛ {dur(lock)} دیگه نمی‌تونی طلاق بگیری."
    sp = await eco.get_user(actor.spouse_id)
    if sp is None:
        await do_divorce(actor.tg_id, actor.spouse_id)
        return "💔 طلاق ثبت شد."
    if actor.rand_marriage:                                   # ازدواج رندوم: طلاق فوری، بدون قبول/رد
        await do_divorce(actor.tg_id, sp.tg_id)
        await redis_service.r.delete(f"divreq:{actor.tg_id}")
        text = f"💔 {mention(actor.tg_id, actor.name)} و {mention(sp.tg_id, sp.name)} از هم جدا شدن (طلاق ازدواج رندوم)."
        if in_group:
            await send(bot, chat_id, text)
        else:
            await send(bot, sp.tg_id, f"💔 {esc(actor.name)} طلاق گرفت (ازدواج رندوم).")
        return None if in_group else "💔 طلاق ثبت شد."
    key = f"divreq:{actor.tg_id}"
    await redis_service.r.set(key, str(int(time.time())), nx=True, ex=7 * 86400)
    owes = actor.mahr > 0 and actor.mahr_to == sp.tg_id          # درخواست‌دهنده بدهکار مهریه‌ست
    text = (f"💔 {mention(actor.tg_id, actor.name)} از {mention(sp.tg_id, sp.name)} <b>درخواست طلاق</b> داد.\n"
            f"{esc(sp.name)}، قبول می‌کنی؟\n<i>(اگه ۲۴ ساعت جواب نده، درخواست‌دهنده می‌تونه یک‌طرفه طلاق بگیره)</i>")
    rows = [[b("✅ قبول طلاق", f"dv:y:{actor.tg_id}:{sp.tg_id}"), b("❌ رد", f"dv:n:{actor.tg_id}:{sp.tg_id}")]]
    if owes:
        text += (f"\n\n💰 مهریه: <b>{fa(actor.mahr)}</b> دلار — اگه راضی نیستی، «مطالبه‌ی مهریه» رو بزن؛ "
                 f"مهریه از {esc(actor.name)} کم و به تو داده می‌شه و طلاق ثبت می‌شه.")
        rows[0].append(b("💰 مطالبه‌ی مهریه", f"dv:m:{actor.tg_id}:{sp.tg_id}"))
    elif actor.mahr > 0:
        text += f"\n\n💰 مهریه‌ی {fa(actor.mahr)} دلاری با قبول طلاق بخشیده می‌شه."
    rows.append([b("⚖️ طلاق یک‌طرفه (بعد ۲۴ ساعت)", f"dv:f:{actor.tg_id}:{sp.tg_id}")])
    markup = kb(*rows)
    if in_group:
        await send(bot, chat_id, text, markup)
        return None
    if not await send(bot, sp.tg_id, text, markup):
        return "😕 نتونستم به همسرت پیام بدم. توی گروه با دستور «درخواست طلاق» امتحان کن."
    return "📨 درخواست طلاق برای همسرت فرستاده شد."


# ───────────────────────── آیتم‌های دارک وب ─────────────────────────
async def _consume(uid: int, item: str) -> bool:
    return await eco.update_user(uid, inc={f"items.{item}": -1}, flt={f"items.{item}": {"$gte": 1}})


async def jinn(bot, actor: User, target: User) -> str:
    """حمله‌ی اجنه: یک بچه از طرف می‌دزده و می‌آره تو خونه‌ی مهاجم. اگه ظرفیت خونه نبود، جن می‌بردش. هر حمله فقط یک بچه."""
    now = utcnow()
    if actor.items.get("jinn", 0) < 1:
        return "👹 جن نداری! از «بازار ← دارک وب» بخر."
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    if target.kids < 1:
        return f"👶 {esc(target.name)} بچه‌ای نداره که جن بدزده! (جنت مصرف نشد)"
    if not await _consume(actor.tg_id, "jinn"):
        return "👹 جن نداری!"
    await combat.drop_shield(actor.tg_id, actor, now)
    an, tn = mention(actor.tg_id, actor.name), mention(target.tg_id, target.name)
    if target.items.get("jinngir", 0) >= 1 and await _consume(target.tg_id, "jinngir"):
        rem = target.items.get("jinngir", 1) - 1
        await send(bot, target.tg_id, f"🧿 جنگیرت جنِ {esc(actor.name)} رو دفع کرد! ({fa(rem)} بار دفاع باقی مونده)")
        return f"🧿 جنِ {an} به {tn} خورد به <b>جنگیر</b> و دفع شد! جن از بین رفت."

    await eco.settle_many(actor.tg_id, target.tg_id)       # درآمد قبل از تغییر تعداد بچه‌ها حساب بشه
    t = await eco.get_user(target.tg_id)
    if t is None or t.kids < 1:
        return f"👶 {esc(target.name)} دیگه بچه‌ای نداره؛ جن دست‌خالی برگشت 😅"
    names = list(t.kid_names or [])
    idx = random.randrange(t.kids)
    name = names.pop(idx) if idx < len(names) else None
    if not await eco.update_user(target.tg_id, inc={"kids": -1}, set_={"kid_names": names},
                                 flt={"kids": {"$gte": 1}}):
        return f"👶 {esc(target.name)} دیگه بچه‌ای نداره؛ جن دست‌خالی برگشت 😅"
    kid = f"<b>{esc(name)}</b>" if name else "یه بچه"
    cap = C.HOUSES[actor.house][0] if actor.house else 0
    home = False
    if cap:
        home = await eco.update_user(actor.tg_id, inc={"kids": 1}, flt={"kids": {"$lt": cap}})
        if home and name:
            await eco.col().update_one({"tg_id": actor.tg_id}, {"$push": {"kid_names": name}})
    if home:
        await send(bot, target.tg_id, f"👹 {esc(actor.name)} یه جن فرستاد و {kid} رو از خونه‌ت دزدید! 😱 "
                                      f"({fa(t.kids - 1)} بچه برات موند) — با «جنگیر» جلوش رو بگیر.")
        return f"👹 جنِ {an} رفت و {kid} رو از {tn} دزدید و آورد تو خونه‌ی تو! 👶😈"
    await send(bot, target.tg_id, f"👹 {esc(actor.name)} یه جن فرستاد و جن {kid} رو برد! 😱 "
                                  f"({fa(t.kids - 1)} بچه برات موند) — با «جنگیر» جلوش رو بگیر.")
    return (f"👹 جنِ {an} {kid} رو از {tn} دزدید ولی خونه‌ی تو ظرفیت نداشت؛ "
            f"<b>جن برد</b>! 💨 (برای نگه‌داشتن بچه‌ی دزدیده‌شده خونه‌ات رو ارتقا بده)")


async def vthief(bot, actor: User, target: User) -> str:
    now = utcnow()
    if actor.items.get("vthief", 0) < 1:
        return "🥷 دزد گاوصندوق نداری! از «بازار ← دارک وب» بخر."
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    if target.vault < 1_000:
        return f"🕳 گاوصندوق {esc(target.name)} تقریباً خالیه."
    wait = await steal_gate("vthief", actor.tg_id, target.tg_id)
    if wait:
        return f"🥷 تازه به گاوصندوق {esc(target.name)} زدی؛ {dur(wait)} دیگه می‌تونی دوباره سراغ همین یکی بری (دزدت مصرف نشد)."
    if not await _consume(actor.tg_id, "vthief"):
        await redis_service.r.delete(steal_key("vthief", actor.tg_id, target.tg_id))
        return "🥷 دزد نداری!"
    await combat.drop_shield(actor.tg_id, actor, now)
    dfn, _, _ = rules.defense_power(target.gear, target.forces)
    pct = rules.RNG.uniform(C.VTHIEF_MIN, C.VTHIEF_MAX) * (1 - 0.6 * dfn / (dfn + 50_000))
    fresh = await eco.get_user(target.tg_id)
    amount = int(fresh.vault * pct)
    an, tn = mention(actor.tg_id, actor.name), mention(target.tg_id, target.name)
    if amount <= 0 or not await eco.transfer(target.tg_id, actor.tg_id, amount, field="vault"):
        return f"🥷 دزد رفت ولی دست‌خالی برگشت! {tn} تونست جلوش رو بگیره."
    await send(bot, target.tg_id, f"🥷 یه دزد حرفه‌ای زد به گاوصندوقت و <b>{fa(amount)}</b> دلار برد! (از طرف {esc(actor.name)})")
    return f"🥷 دزد {an} زد به گاوصندوق {tn}: <b>{fa(amount)}</b> دلار ({pct * 100:.0f}٪) برداشت!"


async def htheft(bot, actor: User, target: User) -> str:
    """«دزدی از خانه»: ابزار دزدی دزد ‹در برابر› تجهیزات امنیتی + نگهبان‌های طرف.
    دزد برنده بشه یه وسیله‌ی تصادفی از کمد طرف می‌ره تو کمد خودش؛ قدرت برابر = ۵۰/۵۰."""
    now = utcnow()
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    victim = await eco.get_user(target.tg_id)
    if rules.pick_closet_item(victim.gear, victim.items) is None:
        return f"🗄 کمد {esc(target.name)} خالیه؛ چیزی برای دزدیدن نیست."
    wait = await steal_gate("htheft", actor.tg_id, target.tg_id)
    if wait:
        return f"🥷 تازه از خونه‌ی {esc(target.name)} دزدی کردی؛ {dur(wait)} دیگه می‌تونی دوباره سراغ همین یکی بری (بقیه رو می‌تونی بزنی)."
    await combat.drop_shield(actor.tg_id, actor, now)
    atk = rules.theft_power(actor.gear)
    dfn, sec = rules.home_defense(victim.gear, victim.forces)
    win, _, _ = rules.roll_fight(atk, dfn)
    an, tn = mention(actor.tg_id, actor.name), mention(target.tg_id, target.name)
    head = (f"🥷 {an} رفت سراغ خونه‌ی {tn}!\n"
            f"🗡 قدرت دزد: <b>{fa(atk)}</b>   🛡 امنیت خونه + نگهبان: <b>{fa(dfn)}</b>\n")
    if not win:
        by = sec["name"] if sec else "نگهبان‌هاش"
        await send(bot, target.tg_id, f"🚨 {esc(actor.name)} می‌خواست از کمدت دزدی کنه ولی «{esc(by)}» جلوش رو گرفت!")
        return head + f"🚨 <b>دزد لو رفت!</b> امنیت خونه‌ی {tn} جلوش رو گرفت و دست‌خالی در رفت."
    stolen = None
    for _ in range(3):                        # اگه همزمان چیزی جابجا شد دوباره امتحان کن
        pick = rules.pick_closet_item(victim.gear, victim.items)
        if pick is None:
            break
        kind, key = pick
        if kind == "gear":
            price = rules.SHOP_INDEX[key]["price"]
            r = await eco.col().update_one({"tg_id": target.tg_id, f"gear.{key}": {"$exists": True}},
                                           {"$unset": {f"gear.{key}": ""}, "$inc": {"invested": -price}})
            if r.matched_count == 1:
                await eco.update_user(actor.tg_id, set_={f"gear.{key}": 1}, inc={"invested": price})
                stolen = rules.SHOP_INDEX[key]
        else:
            if await _consume(target.tg_id, key):
                await eco.update_user(actor.tg_id, inc={f"items.{key}": 1})
                stolen = rules.DARK_INDEX[key]
        if stolen:
            break
        victim = await eco.get_user(target.tg_id)
    if not stolen:
        return head + "💨 تا رسیدی، کمد خالی شده بود؛ دست‌خالی برگشتی!"
    await send(bot, target.tg_id, f"🥷 {esc(actor.name)} زد به خونه‌ات و «{stolen['emoji']} {esc(stolen['name'])}» رو از کمدت دزدید!")
    return head + f"💥 <b>دزدی موفق!</b> {an} «{stolen['emoji']} {esc(stolen['name'])}» رو از کمد {tn} کش رفت و گذاشت تو کمد خودش! 🗄"


async def hack(bot, actor: User, target: User) -> str:
    """«هکش کن»: هکرهای مهاجم ‹در برابر› هکرهای مدافع (هکرها هم هک می‌کنن هم جلوی هک رو می‌گیرن).
    هک موفق = ۵ تا ۵۰٪ پول طرف (جیب و گاوصندوق) کم می‌شه و می‌ره تو جیب هکر؛ قدرت برابر = ۵۰/۵۰."""
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
    wait = await steal_gate("hack", actor.tg_id, target.tg_id)
    if wait:
        return f"💻 تازه {esc(target.name)} رو هک کردی؛ {dur(wait)} دیگه می‌تونی دوباره همین یکی رو هک کنی (بقیه رو می‌تونی بزنی)."
    await combat.drop_shield(actor.tg_id, actor, now)
    dfn = rules.hack_power(victim.forces)
    win, a, d = rules.roll_fight(atk, dfn)
    an, tn = mention(actor.tg_id, actor.name), mention(target.tg_id, target.name)
    head = (f"💻 {an} هکرهاش رو انداخت به جون {tn}!\n"
            f"💻 قدرت هک: <b>{fa(atk)}</b>   🛡 ضدهک: <b>{fa(dfn)}</b>\n")
    if not win:
        await send(bot, target.tg_id, f"🛡 هکرهات جلوی هک {esc(actor.name)} رو گرفتن!")
        return head + f"🛡 <b>هک خنثی شد!</b> هکرهای {tn} جلوی نفوذ رو گرفتن."
    pct = rules.hack_percent(a, d)
    got_cash = got_vault = 0
    fresh = await eco.get_user(target.tg_id)
    amt = int(fresh.cash * pct)
    if amt > 0 and await eco.transfer(target.tg_id, actor.tg_id, amt):
        got_cash = amt
    fresh = await eco.get_user(target.tg_id)
    amt = int(fresh.vault * pct)
    if amt > 0 and await eco.transfer(target.tg_id, actor.tg_id, amt, field="vault"):
        got_vault = amt
    total = got_cash + got_vault
    if total <= 0:
        return head + "💨 هک شد ولی پولی پیدا نکردی!"
    await eco.update_user(actor.tg_id, inc={"stats.loot": total})
    await eco.update_user(target.tg_id, inc={"stats.lost": total})
    await send(bot, target.tg_id, f"💻 {esc(actor.name)} هکت کرد و <b>{fa(total)}</b> دلار ({pct * 100:.0f}٪ پولت) رفت! هکر قوی‌تر بخر.")
    return head + (f"💥 <b>هک موفق!</b> {an} مقدار <b>{fa(total)}</b> دلار ({pct * 100:.0f}٪ پول) از حساب {tn} کشید!"
                   f"\n💵 جیب: {fa(got_cash)}   🔐 گاوصندوق: {fa(got_vault)}")


async def love(bot, actor: User, target: User) -> str:
    now = utcnow()
    if actor.items.get("love", 0) < 1:
        return "💘 طلسم عاشقی نداری! از «بازار ← دارک وب» بخر."
    if actor.tg_id == target.tg_id:
        return "😅 خودت رو طلسم می‌کنی؟"
    if actor.spouse_id or target.spouse_id:
        return "💔 هر دو نفر باید مجرد باشید."
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    if not await _consume(actor.tg_id, "love"):
        return "💘 طلسم نداری!"
    if not await do_marry(actor.tg_id, target.tg_id, C.LOVE_LOCK_HOURS):
        await eco.update_user(actor.tg_id, inc={"items.love": 1})     # برگردوندن طلسم
        return "💔 طلسم نگرفت (یکی‌تون همین الان ازدواج کرد). طلسمت سالمه."
    await send(bot, target.tg_id, f"💘 {esc(actor.name)} طلسمت کرد! حالا همسرشی و {fa(C.LOVE_LOCK_HOURS)} ساعت نمی‌تونی طلاق بگیری 😅")
    return (f"💘 طلسم عاشقی گرفت! {mention(actor.tg_id, actor.name)} و {mention(target.tg_id, target.name)} همسر شدن "
            f"و {fa(C.LOVE_LOCK_HOURS)} ساعت کنار هم می‌مونن 💞")


async def split(bot, actor: User, target: User) -> str:
    now = utcnow()
    if actor.items.get("divorce", 0) < 1:
        return "💔 طلسم طلاق نداری! از «بازار ← دارک وب» بخر."
    if not target.spouse_id:
        return f"💍 {esc(target.name)} متاهل نیست."
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    if left(target.love_lock_until, now) > 0:
        return "💘 این زوج زیر طلسم عاشقی‌ان و جدا نمی‌شن (تا " + dur(left(target.love_lock_until, now)) + " دیگه)."
    sp = target.spouse_id
    if not await _consume(actor.tg_id, "divorce"):
        return "💔 طلسم نداری!"
    await combat.drop_shield(actor.tg_id, actor, now)
    await do_divorce(target.tg_id, sp, C.SPLIT_BAN_HOURS)
    for uid in (target.tg_id, sp):
        await send(bot, uid, f"💔 {esc(actor.name)} با طلسم طلاق شما رو از هم جدا کرد! {fa(C.SPLIT_BAN_HOURS)} ساعت نمی‌تونید با هم ازدواج کنید.")
    return f"💔 طلسم طلاق! {mention(target.tg_id, target.name)} از همسرش جدا شد و تا {fa(C.SPLIT_BAN_HOURS)} ساعت نمی‌تونن دوباره ازدواج کنن."


async def _steal_xp(actor: User, text: Optional[str], marker: str) -> Optional[str]:
    """هک / دزدی از خانه‌ی موفق XP می‌ده (از روی متن نتیجه تشخیص داده می‌شه تا با نسخه‌های جایگزین‌شده هم کار کنه)."""
    if isinstance(text, str) and marker in text:
        await eco.add_xp(actor.tg_id, C.XP["steal"])
    return text


# ───────────────────────── مسیریاب (پیوی) ─────────────────────────
async def run(bot, action: str, actor: User, target: User, chat_id: int, in_group: bool = False, extra: str = "") -> Optional[str]:
    actor = await eco.get_user(actor.tg_id) or actor
    target = await eco.get_user(target.tg_id) or target
    if action == "attack":
        return await combat.do_attack(bot, actor, target, extra)
    if action == "marry":
        from util import parse_amount
        mahr = next((a for a in (parse_amount(t, actor.cash + actor.vault) for t in extra.split()) if a), 0)
        return await marry(bot, actor, target, chat_id, in_group, mahr)
    if action == "jinn":
        return await jinn(bot, actor, target)
    if action == "vthief":
        return await vthief(bot, actor, target)
    if action == "htheft":
        return await _steal_xp(actor, await htheft(bot, actor, target), "دزدی موفق")
    if action == "hack":
        return await _steal_xp(actor, await hack(bot, actor, target), "هک موفق")
    if action == "love":
        return await love(bot, actor, target)
    if action == "split":
        return await split(bot, actor, target)
    return "؟"
