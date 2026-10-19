"""🏦💻 هک بانک (بازیکن ← بازیکن) با دفاع زنده.

دستور:  روی پیام طرف ریپلای کن و بنویس  «هک بانک»   (پیوی: «هک بانک @یوزر»)   یا از منوی «مبارزه ← 🏦 هک بانک».

جریان:
  ۱) مهاجم (باید هکر داشته باشه) نفوذ رو شروع می‌کنه ← به قربانی هشدار با دکمه‌ی «🛡 دفاع کن» می‌رسه.
  ۲) BH_WINDOW_SEC ثانیه فرصته. تو این مدت قربانی می‌تونه:
       • دکمه‌ی دفاع رو بزنه (قدرت ضدهکش ×BH_DEFEND_MULT + BH_DEFEND_FLAT می‌شه)
       • یا پولش رو از بانک برداره (غنیمت از موجودی «لحظه‌ی نتیجه» حساب می‌شه)
     اگه کاری نکنه فقط BH_PASSIVE_MULT از قدرت ضدهکش (هکرها و بادیگاردها) کار می‌کنه.
  ۳) نتیجه: قدرت هک ‹در برابر› ضدهک.
       برد مهاجم: BH_PCT_MIN تا BH_PCT_MAX از «موجودی بانک» (سقف BH_CAP) می‌ره تو جیب مهاجم.
       باخت مهاجم: اگه قربانی دفاع کرده بود، مهاجم لو می‌ره و BH_FINE_PCT از جیبش (سقف BH_FINE_CAP) جریمه‌ی قربانی می‌شه.

همه‌ی عددها رو می‌تونی تو config.py با همین اسم‌ها بذاری؛ اگه نبودن مقدار پیش‌فرض همین بالا استفاده می‌شه.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import actions
import bank
import bankx
import combat
import config as C
import eco
import h_fight
import h_target
import rules
from db import User
from redis_service import redis_service
from ui import b, kb
from util import dur, esc, fa, mention, utcnow

router = Router()
log = structlog.get_logger("bankhack")

# ───────────────────────── بالانس ─────────────────────────
BH_MIN_BANK = int(getattr(C, "BH_MIN_BANK", 200_000))              # موجودی بانکِ کمتر از این ارزش هک نداره
BH_PCT_MIN = float(getattr(C, "BH_PCT_MIN", 0.03))                 # کمترین سهم غنیمت از موجودی بانک
BH_PCT_MAX = float(getattr(C, "BH_PCT_MAX", 0.15))                 # بیشترین سهم (وقتی اختلاف قدرت خیلی زیاده)
BH_CAP = int(getattr(C, "BH_CAP", 3_000_000))                      # سقف غنیمت هر هک بانک
BH_WINDOW_SEC = int(getattr(C, "BH_WINDOW_SEC", 45))               # فرصت دفاع قربانی
BH_COOLDOWN_SEC = int(getattr(C, "BH_COOLDOWN_SEC", 3 * 3600))     # هر مهاجم هر چند ثانیه یک هک بانک
BH_PAIR_COOLDOWN_SEC = int(getattr(C, "BH_PAIR_COOLDOWN_SEC", 6 * 3600))   # روی هر هدفِ مشخص
BH_PASSIVE_MULT = float(getattr(C, "BH_PASSIVE_MULT", 0.6))        # ضدهک وقتی قربانی دفاع نکرده
BH_DEFEND_MULT = float(getattr(C, "BH_DEFEND_MULT", 2.0))          # ضدهک وقتی قربانی دکمه‌ی دفاع رو زده
BH_DEFEND_FLAT = int(getattr(C, "BH_DEFEND_FLAT", 100))            # + این‌قدر قدرت ثابت (برای کسی که هکر نداره هم دفاع معنی بده)
BH_FINE_PCT = float(getattr(C, "BH_FINE_PCT", 0.05))               # جریمه‌ی مهاجمِ لوفته = این سهم از جیبش
BH_FINE_CAP = int(getattr(C, "BH_FINE_CAP", 500_000))

BUSY = "bh:busy:{t}"          # همزمان فقط یک نفر می‌تونه یه قربانی رو هک کنه
SESSION = "bh:s:{sid}"
DEFENDED = "bh:d:{sid}"
PERSONAL = "bh:cd:{a}"

bankx.KIND.setdefault("bhack", ("💻", "هک بانک"))       # تو «صورت‌حساب» قربانی/مهاجم خوشگل دیده بشه
_TASKS: set[asyncio.Task] = set()                       # نگه‌داشتن رفرنس تسک‌ها (وگرنه garbage collect می‌شن)


def _pair_key(a: int, t: int) -> str:
    return f"bh:pair:{a}:{t}"


async def _ttl(key: str) -> int:
    t = await redis_service.r.ttl(key)
    return t if t and t > 0 else 0


# ───────────────────────── شروع هک ─────────────────────────
async def bank_hack(bot, actor: User, target: User, chat_id: int, in_group: bool) -> Optional[str]:
    now = utcnow()
    err = await combat.hostile_checks(actor, target, now)
    if err:
        return err
    atk = rules.hack_power(actor.forces)
    if atk <= 0:
        return "💻 هکر نداری! از «خونه ← نیروها ← هکر» استخدام کن."
    await bank.accrue(target.tg_id)
    victim = await eco.get_user(target.tg_id)
    if victim is None or victim.bank < BH_MIN_BANK:
        return f"🕳 بانک {esc(target.name)} تقریباً خالیه (کمتر از {fa(BH_MIN_BANK)})؛ ارزش نفوذ نداره."

    wait = await _ttl(PERSONAL.format(a=actor.tg_id))
    if wait:
        return f"😮‍💨 هکرهات هنوز خستن؛ {dur(wait)} دیگه می‌تونی بانک هک کنی."
    wait = await _ttl(_pair_key(actor.tg_id, target.tg_id))
    if wait:
        return f"🏦 تازه به بانک {esc(target.name)} نفوذ کردی؛ {dur(wait)} دیگه می‌تونی دوباره سراغش بری."
    sid = uuid.uuid4().hex[:10]
    if not await redis_service.r.set(BUSY.format(t=target.tg_id), sid, nx=True, ex=BH_WINDOW_SEC + 30):
        return f"🚨 همین الان یکی دیگه داره به بانک {esc(target.name)} نفوذ می‌کنه؛ چند لحظه صبر کن."

    await redis_service.r.set(PERSONAL.format(a=actor.tg_id), "1", ex=BH_COOLDOWN_SEC)
    await redis_service.r.set(_pair_key(actor.tg_id, target.tg_id), "1", ex=BH_PAIR_COOLDOWN_SEC)
    await combat.drop_shield(actor.tg_id, actor, now)
    await redis_service.r.set(SESSION.format(sid=sid), json.dumps(
        {"a": actor.tg_id, "t": target.tg_id, "chat": chat_id, "an": actor.name, "tn": target.name}),
        ex=BH_WINDOW_SEC + 120)

    an, tn = mention(actor.tg_id, actor.name), mention(target.tg_id, target.name)
    alert = (f"🚨💻 <b>هشدار نفوذ به بانک!</b>\n\n"
             f"{an} هکرهاش رو انداخت به جون بانکِ {tn}!\n"
             f"⏳ <b>{fa(BH_WINDOW_SEC)} ثانیه</b> وقت داری:\n"
             f"🛡 دکمه‌ی «دفاع» رو بزن (ضدهکت ≈ دو برابر می‌شه)\n"
             f"📤 یا پولت رو از بانک بکش بیرون (غنیمت از موجودیِ لحظه‌ی نتیجه حساب می‌شه)")
    markup = kb([b("🛡 دفاع کن!", f"bh:d:{sid}")])
    if in_group:
        await actions.send(bot, chat_id, alert, markup)
    else:
        await actions.send(bot, chat_id, f"💻 نفوذ به بانک {tn} شروع شد؛ نتیجه تا {fa(BH_WINDOW_SEC)} ثانیه‌ی دیگه…")
    if not (in_group and chat_id == target.tg_id):
        ok = await actions.send(bot, target.tg_id, alert, markup)         # پیوی قربانی (اگه تو گروه نبود هم خبردار بشه)
        if not ok and not in_group:
            await actions.send(bot, chat_id, f"⚠️ {tn} ربات رو تو پیوی استارت نکرده؛ نمی‌تونه دفاع کنه (فقط ضدهک خودکار کار می‌کنه).")

    task = asyncio.create_task(_finish(bot, sid, atk))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return None          # پیام‌ها بالا خودشون فرستاده شدن (دکمه‌ی دفاع لازم بود)


# ───────────────────────── دکمه‌ی دفاع ─────────────────────────
@router.callback_query(F.data.startswith("bh:d:"))
async def cb_defend(cb: CallbackQuery) -> None:
    sid = cb.data.split(":")[2]
    raw = await redis_service.r.get(SESSION.format(sid=sid))
    if not raw:
        return await cb.answer("⌛ این نفوذ تموم شده.", show_alert=True)
    s = json.loads(raw)
    if cb.from_user.id != s["t"]:
        return await cb.answer("🛡 این بانک مال تو نیست!", show_alert=True)
    if not await redis_service.r.set(DEFENDED.format(sid=sid), "1", nx=True, ex=BH_WINDOW_SEC + 120):
        return await cb.answer("🛡 دفاعت از قبل فعاله!", show_alert=True)
    await cb.answer("🛡 دفاع فعال شد! ضدهکت ≈ دو برابر شد.", show_alert=True)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass


# ───────────────────────── نتیجه ─────────────────────────
async def _finish(bot, sid: str, atk: int) -> None:
    s: Optional[dict] = None
    try:
        await asyncio.sleep(BH_WINDOW_SEC)
        raw = await redis_service.r.get(SESSION.format(sid=sid))
        if not raw:
            return
        s = json.loads(raw)
        defended = bool(await redis_service.r.get(DEFENDED.format(sid=sid)))
        text, dm_victim = await _resolve(bot, s, atk, defended)
        chat = s["chat"]
        await actions.send(bot, chat, text)
        if chat != s["t"]:
            await actions.send(bot, s["t"], dm_victim or text)
        if chat != s["a"]:
            await actions.send(bot, s["a"], text)
    except Exception:  # noqa: BLE001
        log.exception("bankhack_finish_error", sid=sid)
    finally:
        keys = [SESSION.format(sid=sid), DEFENDED.format(sid=sid)]
        if s:
            keys.append(BUSY.format(t=s["t"]))
        try:
            await redis_service.r.delete(*keys)
        except Exception:  # noqa: BLE001
            pass


async def _resolve(bot, s: dict, atk: int, defended: bool) -> tuple[str, Optional[str]]:
    a_id, t_id = s["a"], s["t"]
    actor, victim = await eco.get_user(a_id), await eco.get_user(t_id)
    an, tn = mention(a_id, s["an"]), mention(t_id, s["tn"])
    if actor is None or victim is None:
        return "💻 نفوذ نیمه‌کاره موند (بازیکن پیدا نشد).", None
    await bank.accrue(t_id)
    victim = await eco.get_user(t_id)

    base = rules.hack_power(victim.forces)
    dfn = int(base * BH_DEFEND_MULT) + BH_DEFEND_FLAT if defended else int(base * BH_PASSIVE_MULT)
    win, a, d = rules.roll_fight(atk, dfn)
    mode = "🛡 <b>دفاع فعال</b>" if defended else "😴 دفاع خودکار"
    head = (f"🏦💻 <b>نتیجه‌ی نفوذ به بانک</b>\n{an} ‹در برابر› {tn}\n"
            f"💻 قدرت هک: <b>{fa(atk)}</b>   🛡 ضدهک: <b>{fa(dfn)}</b> ({mode})\n\n")

    if not win:
        extra = ""
        if defended:
            fine = max(0, min(int(actor.cash * BH_FINE_PCT), BH_FINE_CAP))
            if fine > 0 and await eco.transfer(a_id, t_id, fine):
                extra = f"\n🚨 {an} لو رفت و <b>{fa(fine)}</b> دلار جریمه داد به {tn}!"
        await eco.add_xp(t_id, C.XP.get("lose", 4))
        return (head + f"🛡 <b>هک بانک خنثی شد!</b> {tn} جلوی نفوذ رو گرفت." + extra,
                None)

    margin = min(1.0, max(0.0, (a - d) / a)) if a > 0 else 0.0
    pct = BH_PCT_MIN + (BH_PCT_MAX - BH_PCT_MIN) * margin
    bank_now = victim.bank
    amt = min(int(bank_now * pct), BH_CAP)
    if amt <= 0:
        return head + "💨 هک شد ولی بانک خالی بود (قربانی به‌موقع پولش رو کشید بیرون!)", None
    r = await eco.col().update_one({"tg_id": t_id, "bank": {"$gte": amt}}, {"$inc": {"bank": -amt}})
    if r.matched_count != 1:
        return head + "💨 هک شد ولی موجودی بانک همون لحظه تغییر کرد؛ چیزی نصیبت نشد.", None
    await eco.gain(a_id, amt)
    await eco.update_user(a_id, inc={"stats.loot": amt})
    await eco.update_user(t_id, inc={"stats.lost": amt})
    left_bank = max(0, bank_now - amt)
    await bankx.log_tx(t_id, "bhack", -amt, left_bank, f"هک بانک توسط {s['an']}", a_id)
    await eco.add_xp(a_id, C.XP.get("steal", 12))
    win_text = (head + f"💥 <b>هک بانک موفق!</b> {an} مبلغ <b>{fa(amt)}</b> دلار ({pct * 100:.0f}٪ بانک) از حساب {tn} کشید!"
                + ("\n🛡 دفاعش جواب نداد!" if defended else "\n😴 قربانی دفاع نکرد!"))
    dm = win_text + "\n\n💡 دفعه‌ی بعد دکمه‌ی «🛡 دفاع» رو به‌موقع بزن یا هکر قوی‌تر بخر."
    return win_text, dm


# ───────────────────────── اتصال به بقیه‌ی بازی ─────────────────────────
if not getattr(actions.run, "_bankhack", False):
    _prev_run = actions.run

    async def _run_with_bankhack(bot, action, actor, target, chat_id, in_group=False, extra=""):
        if action == "bhack":
            actor = await eco.get_user(actor.tg_id) or actor
            target = await eco.get_user(target.tg_id) or target
            return await bank_hack(bot, actor, target, chat_id, in_group)
        return await _prev_run(bot, action, actor, target, chat_id, in_group, extra)

    _run_with_bankhack._bankhack = True              # type: ignore[attr-defined]
    actions.run = _run_with_bankhack

h_target.TITLES["bhack"] = "🏦 هک بانک"

# دکمه‌ی «🏦 هک بانک» تو منوی مبارزه (بدون دست‌زدن به h_fight.py)
if not getattr(h_fight.render_fight, "_bankhack", False):
    _prev_render = h_fight.render_fight

    async def _render_fight_with_bankhack(u: User):
        text, markup = await _prev_render(u)
        try:
            markup.inline_keyboard.insert(min(3, len(markup.inline_keyboard)), [b("🏦 هک بانک", "fight:bhack")])
        except Exception:  # noqa: BLE001
            pass
        return text, markup

    _render_fight_with_bankhack._bankhack = True     # type: ignore[attr-defined]
    h_fight.render_fight = _render_fight_with_bankhack


@router.callback_query(F.data == "fight:bhack")
async def cb_bhack(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await h_target.ask_target(
        cb, state, "bhack",
        f"🏦 هکرهات ‹در برابر› ضدهک طرف؛ برنده بشی {int(BH_PCT_MIN * 100)} تا {int(BH_PCT_MAX * 100)}٪ بانکش مال تو می‌شه.\n"
        f"طرف {fa(BH_WINDOW_SEC)} ثانیه وقت داره با دکمه‌ی «دفاع» جلوت رو بگیره!")
