"""بانک و وام: سپرده با سود روزانه، وام با قسط روزانه، یادآوری و ضبط دارایی.

- پول بانک امنه (غارت/هک/دزد گاوصندوق بهش نمی‌رسه).
- سود: هر ۲۴ ساعت، ۴٪ مرکب روی کل موجودی. سقف و سطح بانک نداره (فقط یه جای امنه).
- وام: سقف = min(ارزش کاسبی‌ها + خونه ، سقف شغلی × ضریب سطح حساب × ضریب امتیاز اعتباری). ۷ روز، بهره ۵٪ (منهای تخفیف سطح)، قسط روزانه.
- کارت، کارت‌به‌کارت، سطح حساب، صورت‌حساب و امتیاز اعتباری: bankx.py
  ۳ روز نپرداختن قسط = ضبط همه‌ی کاسبی‌ها و خونه.
- تا وقتی وام فعاله «واریز به بانک» بسته‌ست (وگرنه وام ۵٪ می‌گرفتن و تو بانک ۴٪ روزانه می‌خوردن).
"""
from __future__ import annotations

import asyncio
import math
import sys
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog

import bankx
import config as C
import eco
import rules
from db import User
from util import dur, esc, fa, utcnow

log = structlog.get_logger("bank")
DAY = 86400


def _emit(text: str) -> None:
    gl = sys.modules.get("gamelog")
    if gl is not None:
        try:
            gl.emit(text)
        except Exception:  # noqa: BLE001
            pass


async def _dm(bot, uid: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(uid, text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        pass


# ───────────────────────── فیلدهای جدید روی بازیکن‌های قدیمی ─────────────────────────
# بازیکن‌هایی که قبل از آپدیت بانک ساخته شدن این فیلدها تو دیتابیسشون نیست؛ مدل پایتونی مقدار پیش‌فرض می‌ده
# ولی فیلتر Mongo (مثل bank >= 0 یا bank_level == 1) روی فیلدِ «وجود ندارد» جواب نمی‌ده و پرداخت قسط/واریز
# با «دوباره امتحان کن» بی‌نتیجه می‌موند. پس فیلدها قبل از هر عملیات تضمین می‌شن.
_DEFAULTS: dict[str, Any] = {"bank": 0, "bank_level": 1, "bank_at": None, "loan_principal": 0, "loan_total": 0,
                             "loan_paid": 0, "loan_daily": 0, "loan_start": None, "loan_remind": 0,
                             "xp": 0, "antirob": False}
_MISSING = {"$or": [{f: {"$exists": False}} for f in ("bank", "bank_level", "bank_at", "loan_total", "loan_paid",
                                                        "loan_daily", "loan_remind", "xp", "antirob")]}
_FILL = [{"$set": {f: {"$ifNull": [f"${f}", v]} for f, v in _DEFAULTS.items()}}]


async def ensure_fields(uid: int) -> None:
    await eco.col().update_one({"tg_id": uid, **_MISSING}, _FILL)


async def migrate_fields() -> int:
    """موقع استارت: برای همه‌ی بازیکن‌های قدیمی فیلدهای بانک/وام/سطح ساخته می‌شه (idempotent)."""
    r = await eco.col().update_many(_MISSING, _FILL)
    return r.modified_count


# ───────────────────────── ارزش دارایی‌ها (سقف وام) ─────────────────────────
def biz_value(u: User) -> int:
    """ارزش کاسبی‌ها: قیمت هر شعبه + کارگرهای استخدام‌شده‌اش + پولی که خرج ارتقای لولش شده."""
    total = 0
    for k in (u.businesses or {}):
        bk = rules.base_key(k)
        bz = rules.BIZ_INDEX.get(bk)
        if bz:
            n = rules.branch_no(k)
            total += rules.branch_price(bk, n - 1) if n > 1 else bz["price"]
            total += sum(c * bz["workers"][i]["price"] for i, c in enumerate(rules.staff_counts(u, k)))
            total += rules.upgrade_spent(bk, rules.biz_level(u, k))
    return total


def house_value(u: User) -> int:
    """ارزش خونه: مجموع قیمت همه‌ی سطح‌هایی که خریده."""
    return sum(C.HOUSES[l][1] for l in range(1, min(u.house, C.MAX_HOUSE) + 1))


def collateral(u: User) -> int:
    return biz_value(u) + house_value(u)


def has_loan(u: User) -> bool:
    return u.loan_total > 0


def job_limit(u: User) -> int:
    return rules.job_loan_limit(u.shifts)


def max_loan(u: User) -> int:
    """سقف وام = min(ارزش کاسبی‌ها + خونه، سقف شغلی)."""
    if has_loan(u):
        return 0
    return min(collateral(u), int(job_limit(u) * bankx.loan_mult(u)))       # وثیقه سقف سخته؛ ضریب‌ها فقط روی سقف شغلی


# ───────────────────────── سپرده و سود ─────────────────────────
def daily_interest(u: User) -> int:
    return int(u.bank * C.BANK_INTEREST)


async def accrue(uid: int) -> int:
    """سودِ روزهای کامل گذشته رو (مرکب) به موجودی بانک اضافه می‌کنه. خروجی: مبلغ سود."""
    await ensure_fields(uid)
    for _ in range(3):
        u = await eco.get_user(uid)
        if u is None or u.bank <= 0 or u.bank_at is None:
            return 0
        now = utcnow()
        days = int((now - u.bank_at).total_seconds() // DAY)
        if days <= 0:
            return 0
        days = min(days, 365)
        bal, total = u.bank, 0
        for _d in range(days):
            i = int(bal * C.BANK_INTEREST)
            bal += i
            total += i
        r = await eco.col().update_one({"tg_id": uid, "bank": u.bank, "bank_at": u.bank_at},
                                       {"$inc": {"bank": total}, "$set": {"bank_at": u.bank_at + timedelta(days=days)}})
        if r.matched_count == 1:
            await bankx.log_tx(uid, "interest", total, u.bank + total, f"{days} روز")
            return total
    return 0


async def deposit(uid: int, amount: int) -> tuple[bool, str]:
    await accrue(uid)
    u = await eco.get_user(uid)
    if u is None or amount <= 0:
        return False, "چیزی برای واریز نیست."
    if has_loan(u):
        return False, "🚫 تا وقتی وام داری نمی‌تونی به بانک واریز کنی. اول وامت رو صاف کن."
    amount = min(amount, u.cash)
    if amount <= 0:
        return False, "💸 پول نقد کافی نداری."
    inc: dict[str, Any] = {"cash": -amount, "bank": amount}
    set_: dict[str, Any] = {}
    if u.bank <= 0:
        set_["bank_at"] = utcnow()
    upd: dict[str, Any] = {"$inc": inc}
    if set_:
        upd["$set"] = set_
    r = await eco.col().update_one({"tg_id": uid, "cash": {"$gte": amount}, "bank": u.bank}, upd)
    if r.matched_count != 1:
        return False, "⏳ دوباره امتحان کن."
    await bankx.log_tx(uid, "dep", amount, u.bank + amount)
    return True, f"📥 <b>{fa(amount)}</b> دلار به بانک واریز شد."


async def withdraw(uid: int, amount: int) -> tuple[bool, str]:
    await accrue(uid)
    u = await eco.get_user(uid)
    if u is None or u.bank <= 0:
        return False, "بانکت خالیه."
    amount = min(amount, u.bank)
    if amount <= 0:
        return False, "چیزی برای برداشت نیست."
    r = await eco.col().update_one({"tg_id": uid, "bank": {"$gte": amount}}, {"$inc": {"bank": -amount, "cash": amount}})
    if r.matched_count != 1:
        return False, "⏳ دوباره امتحان کن."
    await bankx.log_tx(uid, "wd", -amount, u.bank - amount)
    return True, f"📤 <b>{fa(amount)}</b> دلار از بانک اومد تو جیبت."


# ───────────────────────── وام ─────────────────────────
def loan_state(u: User, now: Optional[datetime] = None) -> dict:
    """وضعیت وام. due_j = موعد اولین قسطِ پرداخت‌نشده؛ overdue_days = چند روز از اون موعد گذشته."""
    now = now or utcnow()
    daily = max(1, u.loan_daily)
    remaining = max(0, u.loan_total - u.loan_paid)
    start = u.loan_start or now
    k = int(max(0, (now - start).total_seconds()) // DAY)             # چند موعد گذشته
    paid_inst = u.loan_paid // daily
    j = paid_inst + 1                                                 # اولین قسط پرداخت‌نشده
    due_j = start + timedelta(days=j)
    overdue = max(0.0, (now - due_j).total_seconds())
    overdue_days = int(overdue // DAY)
    behind = max(0, k - paid_inst)                                    # قسط‌های موعدگذشته‌ی پرداخت‌نشده
    owed_now = min(remaining, behind * daily)
    return {"remaining": remaining, "daily": daily, "k": k, "paid_inst": paid_inst, "behind": behind,
            "owed_now": owed_now, "next_due": due_j, "overdue_days": overdue_days,
            "seize": remaining > 0 and overdue_days >= C.LOAN_MAX_OVERDUE_DAYS,
            "days_left": max(0, C.LOAN_MAX_OVERDUE_DAYS - overdue_days)}


async def take_loan(uid: int, amount: int) -> tuple[bool, str]:
    await ensure_fields(uid)
    u = await eco.get_user(uid)
    if u is None:
        return False, "اول /start بزن."
    if has_loan(u):
        return False, "🚫 یه وام فعال داری؛ اول صافش کن."
    cap = max_loan(u)
    if cap < C.LOAN_MIN:
        return False, "🏚 وثیقه‌ای نداری! وام فقط به کسی می‌دن که کاسبی یا خونه داره."
    score = bankx.credit(u)["score"]
    if score < C.LOAN_MIN_SCORE:
        return False, f"📊 امتیاز اعتباریت ({fa(score)}) برای وام کمه؛ حداقل {fa(C.LOAN_MIN_SCORE)} لازمه."
    amount = min(int(amount), cap)
    if amount < C.LOAN_MIN:
        return False, f"💸 حداقل وام {fa(C.LOAN_MIN)} دلاره."
    rate = bankx.loan_rate(u)
    total = math.ceil(amount * (1 + rate))
    daily = math.ceil(total / C.LOAN_DAYS)
    now = utcnow()
    r = await eco.col().update_one(
        {"tg_id": uid, "$or": [{"loan_total": 0}, {"loan_total": {"$exists": False}}]},
        {"$inc": {"cash": amount},
         "$set": {"loan_principal": amount, "loan_total": total, "loan_paid": 0, "loan_daily": daily,
                  "loan_start": now, "loan_remind": 0}})
    if r.matched_count != 1:
        return False, "⏳ دوباره امتحان کن."
    await bankx.log_tx(uid, "loan", amount, -1, f"بدهی {fa(total)} · بهره {bankx.pct(rate)}٪")
    _emit(f"🏦 وام: {esc(u.name)} <code>{uid}</code> گرفت {fa(amount)} (بدهی {fa(total)})")
    return True, (f"🏦 وام <b>{fa(amount)}</b> دلاری اومد تو جیبت!\n"
                  f"📄 کل بدهی: <b>{fa(total)}</b> (بهره {bankx.pct(rate)}٪)\n"
                  f"📅 قسط روزانه: <b>{fa(daily)}</b> دلار تا {fa(C.LOAN_DAYS)} روز\n"
                  f"⚠️ {fa(C.LOAN_MAX_OVERDUE_DAYS)} روز قسط ندی، خونه و کاسبی‌هات ضبط می‌شن!")


async def pay_loan(uid: int, amount: Optional[int] = None) -> tuple[bool, str]:
    """پرداخت قسط (amount=None → یک قسط روزانه؛ عدد → همون مبلغ). اول از جیب، بعد از بانک."""
    await ensure_fields(uid)
    for _ in range(3):
        u = await eco.get_user(uid)
        if u is None or not has_loan(u):
            return False, "وامی نداری."
        st = loan_state(u)
        want = st["daily"] if amount is None else amount
        # قسط عقب‌افتاده هم یک‌جا با «پرداخت قسط» صاف بشه
        if amount is None and st["owed_now"] > st["daily"]:
            want = st["owed_now"]
        want = min(want, st["remaining"])
        if want <= 0:
            return False, "چیزی برای پرداخت نیست."
        c = min(u.cash, want)
        bk = want - c
        if bk > u.bank:
            return False, f"💸 پول کافی نداری (جیب {fa(u.cash)} + بانک {fa(u.bank)})."
        r = await eco.col().update_one(
            {"tg_id": uid, "cash": {"$gte": c}, "bank": {"$gte": bk}, "loan_paid": u.loan_paid, "loan_total": u.loan_total},
            {"$inc": {"cash": -c, "bank": -bk, "loan_paid": want}})
        if r.matched_count != 1:
            continue
        await _credit_stats(uid, u, st, want, u.loan_paid + want >= u.loan_total)
        await bankx.log_tx(uid, "payoff" if u.loan_paid + want >= u.loan_total else "inst", -want,
                           (u.bank - bk) if bk else -1, f"از جیب {fa(c)} · از بانک {fa(bk)}")
        if u.loan_paid + want >= u.loan_total:
            await _clear_loan(uid)
            _emit(f"✅ وام صاف شد: {esc(u.name)} <code>{uid}</code>")
            return True, f"🎉 پرداخت <b>{fa(want)}</b> دلار انجام شد و وامت کامل صاف شد! 🏦"
        left_ = u.loan_total - u.loan_paid - want
        return True, f"💳 <b>{fa(want)}</b> دلار قسط پرداخت شد. بدهی باقی‌مونده: <b>{fa(left_)}</b>"
    return False, "⏳ دوباره امتحان کن."


async def _credit_stats(uid: int, u: User, st: dict, paid: int, full: bool) -> None:
    """آمار امتیاز اعتباری. قسط به‌موقع/تسویه فقط وقتی حساب می‌شه که وام حداقل یک روز عمر داشته باشه (ضد فارم امتیاز)."""
    inc: dict[str, int] = {}
    if st["behind"] > 0:
        inc["bstats.late"] = 1
    elif utcnow() - (u.loan_start or utcnow()) >= timedelta(days=1):
        if paid >= min(st["daily"], st["remaining"]):
            inc["bstats.ontime"] = 1
        if full:
            inc["bstats.repaid"] = 1
    if inc:
        await eco.col().update_one({"tg_id": uid}, {"$inc": inc})


async def _clear_loan(uid: int) -> None:
    await eco.col().update_one({"tg_id": uid}, {"$set": {"loan_principal": 0, "loan_total": 0, "loan_paid": 0,
                                                         "loan_daily": 0, "loan_start": None, "loan_remind": 0}})


async def seize(bot, uid: int) -> bool:
    """ضبط: همه‌ی کاسبی‌ها (با کارگرها) و خونه گرفته می‌شن و وام بسته می‌شه."""
    await eco.settle(uid)                       # درآمد جمع‌شده تا لحظه‌ی ضبط مال خودش
    u = await eco.get_user(uid)
    if u is None or not has_loan(u):
        return False
    lost_biz, lost_house = biz_value(u), house_value(u)
    names = [f"{rules.BIZ_INDEX[rules.base_key(k)]['emoji']} {rules.inst_name(k)}" for k in u.businesses if rules.base_key(k) in rules.BIZ_INDEX]
    r = await eco.col().update_one({"tg_id": uid, "loan_total": u.loan_total, "loan_paid": u.loan_paid},
                                   {"$set": {"businesses": {}, "staff": {}, "biz_level": {}, "house": 0, "income_since": utcnow(),
                                             "loan_principal": 0, "loan_total": 0, "loan_paid": 0, "loan_daily": 0,
                                             "loan_start": None, "loan_remind": 0}})
    if r.matched_count != 1:
        return False
    await eco.col().update_one({"tg_id": uid}, {"$inc": {"bstats.seized": 1}})
    await bankx.log_tx(uid, "seize", 0, -1, f"ضبط ارزش {fa(lost_biz + lost_house)}")
    lost = lost_biz + lost_house
    if lost > 0:                                # ارزش «دارایی» تو رتبه‌بندی هم کم بشه (نه زیر صفر)
        await eco.col().update_one({"tg_id": uid}, [{"$set": {"invested": {"$max": [0, {"$subtract": ["$invested", lost]}]}}}])
    what = ("، ".join(names) if names else "کاسبی نداشت") + (f" و 🏠 خونه سطح {fa(u.house)}" if u.house else "")
    _emit(f"🚨 ضبط دارایی: {esc(u.name)} <code>{uid}</code> — {what}")
    await _dm(bot, uid, "🚨 <b>دارایی‌هات ضبط شد!</b>\n\n"
                        f"چون {fa(C.LOAN_MAX_OVERDUE_DAYS)} روز قسط وامت رو ندادی، بانک {what} رو ازت گرفت "
                        "و وامت بسته شد.\nاز نو شروع کن؛ این بار قسط‌ها رو سر وقت بده 💪")
    return True


async def check_default(bot, uid: int) -> bool:
    u = await eco.get_user(uid)
    if u is None or not has_loan(u):
        return False
    if loan_state(u)["seize"]:
        return await seize(bot, uid)
    return False


# ───────────────────────── کارگر پس‌زمینه: یادآوری + ضبط ─────────────────────────
def reminder_text(u: User, st: dict) -> str:
    t = (f"⏰ <b>یادآوری قسط وام</b>\n\n💳 قسط روزانه: <b>{fa(st['daily'])}</b> دلار\n"
         f"📌 مبلغ عقب‌افتاده: <b>{fa(st['owed_now'])}</b> دلار\n🧾 بدهی باقی‌مونده: <b>{fa(st['remaining'])}</b>")
    if st["overdue_days"] >= 1:
        t += (f"\n\n🚨 <b>{fa(st['days_left'])} روز</b> دیگه وقت داری، وگرنه خونه و کاسبی‌هات ضبط می‌شن!")
    else:
        t += f"\n\n⚠️ اگه قسط رو ندی، بعد از {fa(C.LOAN_MAX_OVERDUE_DAYS)} روز خونه و کاسبی‌هات ضبط می‌شن."
    return t + "\n\nدکمه‌ی پایین رو بزن (از جیبت و اگه نبود از بانکت کم می‌شه)."


async def _tick(bot) -> None:
    from ui import b, kb
    now = utcnow()
    async for doc in eco.col().find({"loan_total": {"$gt": 0}}, {"tg_id": 1}):
        uid = doc["tg_id"]
        try:
            u = await eco.get_user(uid)
            if u is None or not has_loan(u):
                continue
            st = loan_state(u, now)
            if st["seize"]:
                await seize(bot, uid)
                continue
            if st["behind"] <= 0:
                continue
            # مرحله‌ی یادآوری: هر روز دو بار (سر موعد و ۱۲ ساعت بعدش) تا وقتی پرداخت‌نشده
            into = (now - (u.loan_start or now)).total_seconds() - st["k"] * DAY
            stage = st["k"] * 2 + (1 if into >= DAY / 2 else 0)
            if stage <= u.loan_remind:
                continue
            r = await eco.col().update_one({"tg_id": uid, "loan_remind": u.loan_remind}, {"$set": {"loan_remind": stage}})
            if r.matched_count == 1:
                await _dm(bot, uid, reminder_text(u, st), kb([b("💳 پرداخت قسط", "bank:pay")]))
        except Exception:  # noqa: BLE001
            log.exception("loan_tick_error", uid=uid)
        await asyncio.sleep(0.05)


async def worker(bot) -> None:
    await asyncio.sleep(10)
    while True:
        try:
            await _tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("loan_worker_error")
        await asyncio.sleep(C.LOAN_WORKER_SEC)
