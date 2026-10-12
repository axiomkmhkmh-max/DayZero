"""کارهای روی بازیکن دیگر: حمله، ازدواج، طلاق، بچه، جن، دزد گاوصندوق، طلسم‌ها.
هر تابع متن پاسخ را برمی‌گرداند؛ مشترک بین پیوی (منو) و گروه (دستور متنی)."""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Optional

from aiogram.types import InlineKeyboardMarkup

import combat
import config as C
import eco
import rules
from db import User
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


# ───────────────────────── بچه ─────────────────────────
async def baby(uid: int) -> tuple[bool, str]:
    """بچه‌دار شدن (دستور «بزن» و دکمه‌ی منو)."""
    u = await eco.get_user(uid)
    if u is None:
        return False, "اول /start بزن."
    if not u.house:
        return False, f"🏠 اول باید خونه‌ی سطح ۱ بخری ({fa(C.HOUSES[1][1])} دلار): منو ← خونه ← ارتقای خونه"
    cap = C.HOUSES[u.house][0]
    if u.kids >= cap:
        return False, f"🏠 خونه‌ات ظرفیت {fa(cap)} بچه داره و پره؛ خونه رو ارتقا بده."
    w = left(u.child_ready_at)
    if w > 0:
        return False, f"⏳ هنوز زوده! {dur(w)} دیگه (موز و معجون تایمر رو کم می‌کنن)."
    await eco.settle(uid)
    ok = await eco.update_user(uid, inc={"kids": 1}, flt={"kids": {"$lt": cap}},
                               set_={"child_ready_at": utcnow() + timedelta(hours=C.CHILD_COOLDOWN_H)})
    if not ok:
        return False, "🏠 ظرفیت خونه پره!"
    hungry = "" if (u.cook or left(u.fed_until) > 0) else "\n🍽 یادت نره بهشون غذا بدی تا کار کنن!"
    return True, (f"👶 مبارک! بچه‌ی جدیدت به دنیا اومد 🎉\nخانواده: {fa(u.kids + 1)}/{fa(cap)} بچه — هر بچه‌ی سیر +۲۵٪ درآمد!"
                  f"\n⏳ بچه‌ی بعدی: {fa(C.CHILD_COOLDOWN_H)} ساعت دیگه{hungry}")


# ───────────────────────── ازدواج ─────────────────────────
async def marry(bot, actor: User, target: User, chat_id: int, in_group: bool) -> Optional[str]:
    """«ثبت ازدواج»: پیشنهاد با دکمه‌ی قبول/رد برای طرف."""
    if actor.tg_id == target.tg_id:
        return "😅 با خودت که نمی‌شه ازدواج کرد!"
    if actor.spouse_id:
        return "💍 تو که متاهلی! اول «درخواست طلاق» بده."
    if target.spouse_id:
        return f"💔 {esc(target.name)} متاهله."
    if await redis_service.r.exists(f"nomarry:{_pair(actor.tg_id, target.tg_id)}"):
        return "🚫 به‌خاطر طلسم طلاق، این دو نفر تا مدتی نمی‌تونن با هم ازدواج کنن."
    if not await redis_service.r.set(f"mrprop:{actor.tg_id}:{target.tg_id}", "1", nx=True, ex=60):
        return "⏳ همین الان پیشنهاد دادی؛ یه کم صبر کن."
    text = (f"💍 {mention(actor.tg_id, actor.name)} به {mention(target.tg_id, target.name)} پیشنهاد ازدواج داد!\n"
            f"بونوس ازدواج: +{int(C.MARRIAGE_BONUS * 100)}٪ درآمد کاسبی برای هر دو. قبول می‌کنی؟")
    markup = kb([b("✅ قبول می‌کنم", f"mr:y:{actor.tg_id}:{target.tg_id}"),
                 b("❌ رد", f"mr:n:{actor.tg_id}:{target.tg_id}")])
    if in_group:
        await send(bot, chat_id, text, markup)
        return None
    if not await send(bot, target.tg_id, text, markup):
        return "😕 نتونستم به طرف پیام بدم (باید ربات رو استارت کرده باشه). توی گروه با ریپلای امتحان کن."
    return f"📨 پیشنهاد ازدواج برای {esc(target.name)} فرستاده شد."


async def do_marry(a: int, b_: int, lock_hours: int = 0) -> bool:
    """ازدواج اتمیک (هر دو باید مجرد باشن)."""
    await eco.settle_many(a, b_)
    lock = utcnow() + timedelta(hours=lock_hours) if lock_hours else None
    if not await eco.update_user(a, set_={"spouse_id": b_, "love_lock_until": lock}, flt={"spouse_id": None}):
        return False
    if not await eco.update_user(b_, set_={"spouse_id": a, "love_lock_until": lock}, flt={"spouse_id": None}):
        await eco.update_user(a, set_={"spouse_id": None, "love_lock_until": None})
        return False
    return True


async def do_divorce(a: int, b_: int, ban_hours: int = 0) -> None:
    await eco.settle_many(a, b_)
    for uid in (a, b_):
        await eco.update_user(uid, set_={"spouse_id": None, "love_lock_until": None})
    if ban_hours:
        await redis_service.r.set(f"nomarry:{_pair(a, b_)}", "1", ex=ban_hours * 3600)


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
    key = f"divreq:{actor.tg_id}"
    await redis_service.r.set(key, str(int(time.time())), nx=True, ex=7 * 86400)
    text = (f"💔 {mention(actor.tg_id, actor.name)} از {mention(sp.tg_id, sp.name)} <b>درخواست طلاق</b> داد.\n"
            f"{esc(sp.name)}، قبول می‌کنی؟\n<i>(اگه ۲۴ ساعت جواب نده، درخواست‌دهنده می‌تونه یک‌طرفه طلاق بگیره)</i>")
    markup = kb([b("✅ قبول طلاق", f"dv:y:{actor.tg_id}:{sp.tg_id}"), b("❌ رد", f"dv:n:{actor.tg_id}:{sp.tg_id}")],
                [b("⚖️ طلاق یک‌طرفه (بعد ۲۴ ساعت)", f"dv:f:{actor.tg_id}:{sp.tg_id}")])
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
    now = utcnow()
    if actor.items.get("jinn", 0) < 1:
        return "👹 جن نداری! از «بازار ← دارک وب» بخر."
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    if left(target.cursed_until, now) > 0:
        return "👹 این بنده‌خدا از قبل جن‌زده‌ست!"
    if not await _consume(actor.tg_id, "jinn"):
        return "👹 جن نداری!"
    await combat.drop_shield(actor.tg_id, actor, now)
    an, tn = mention(actor.tg_id, actor.name), mention(target.tg_id, target.name)
    if target.items.get("jinngir", 0) >= 1 and await _consume(target.tg_id, "jinngir"):
        rem = target.items.get("jinngir", 1) - 1
        await send(bot, target.tg_id, f"🧿 جنگیرت جنِ {esc(actor.name)} رو دفع کرد! ({fa(rem)} بار دفاع باقی مونده)")
        return f"🧿 جنِ {an} به {tn} خورد به <b>جنگیر</b> و دفع شد! جن از بین رفت."
    await eco.settle(target.tg_id)         # درآمد قبل از طلسم سالم حساب بشه
    until = utcnow() + timedelta(hours=C.CURSE_HOURS)
    await eco.update_user(target.tg_id, set_={"cursed_until": until})
    await send(bot, target.tg_id, f"👹 {esc(actor.name)} یه جن فرستاد سراغت! تا {fa(C.CURSE_HOURS)} ساعت درآمد کاسبی و کار بچه‌هات قطعه. "
                                  f"(با «جنگیر» جلوش رو بگیر)")
    return f"👹 {an} جن رو فرستاد سراغ {tn}! تا {fa(C.CURSE_HOURS)} ساعت درآمدش قطعه 😈"


async def vthief(bot, actor: User, target: User) -> str:
    now = utcnow()
    if actor.items.get("vthief", 0) < 1:
        return "🥷 دزد گاوصندوق نداری! از «بازار ← دارک وب» بخر."
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    if target.vault < 1_000:
        return f"🕳 گاوصندوق {esc(target.name)} تقریباً خالیه."
    if not await _consume(actor.tg_id, "vthief"):
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


# ───────────────────────── مسیریاب (پیوی) ─────────────────────────
async def run(bot, action: str, actor: User, target: User, chat_id: int, in_group: bool = False, extra: str = "") -> Optional[str]:
    actor = await eco.get_user(actor.tg_id) or actor
    target = await eco.get_user(target.tg_id) or target
    if action == "attack":
        return await combat.do_attack(bot, actor, target, extra)
    if action == "marry":
        return await marry(bot, actor, target, chat_id, in_group)
    if action == "jinn":
        return await jinn(bot, actor, target)
    if action == "vthief":
        return await vthief(bot, actor, target)
    if action == "love":
        return await love(bot, actor, target)
    if action == "split":
        return await split(bot, actor, target)
    return "؟"
