"""Day Zero — لاگ بازی توی چنل تلگرام (فایل مستقل).

استفاده: این فایل رو کنار main.py بذار و بالای main.py یک خط اضافه کن:   import gamelog
(یا بدون دست‌زدن به main.py، استارت‌کامند رو بذار:
   python -c "import gamelog,runpy;runpy.run_path('main.py',run_name='__main__')" )

خودش موقع شروع polling به بقیه‌ی کد وصل می‌شه و اتفاق‌ها رو دسته‌ای (هر ۳ ثانیه) توی چنل می‌فرسته.
ربات باید توی چنل ادمین باشه (Post Messages). متغیرهای اختیاری: LOG_CHANNEL ، LOG_CASINO_MIN
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import sys
from typing import Any, Optional

import structlog
from aiogram import BaseMiddleware, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import CallbackQuery, Message

from util import esc, fa

log = structlog.get_logger("gamelog")

CHANNEL = os.getenv("LOG_CHANNEL", "@DayZeroGameChannelLog")
try:
    CASINO_MIN = int(os.getenv("LOG_CASINO_MIN", "100000"))
except ValueError:
    CASINO_MIN = 100_000

_q: asyncio.Queue = asyncio.Queue(maxsize=3000)
_bot = None
_running = False
_installed = False
_ctx: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar("gamelog_ctx", default=None)


# ───────────────────────── API پایه ─────────────────────────
def setup(bot) -> None:
    global _bot
    _bot = bot


def who(u: Any) -> str:
    """اسم + آیدی (+ یوزرنیم) — هم برای مدل بازیکن، هم برای کاربر تلگرام."""
    uid = getattr(u, "tg_id", None) or getattr(u, "id", 0)
    name = getattr(u, "name", None) or getattr(u, "full_name", None) or "?"
    un = getattr(u, "username", None)
    return f"<b>{esc(name)}</b> <code>{uid}</code>" + (f" @{esc(un)}" if un else "")


def emit(text: str) -> None:
    if not CHANNEL:
        return
    try:
        _q.put_nowait(text)
    except asyncio.QueueFull:
        pass


async def _send(text: str) -> None:
    for _ in range(3):
        try:
            await _bot.send_message(CHANNEL, text, disable_notification=True)
            return
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            log.warning("log_channel_unavailable", error=str(e), hint="ربات رو ادمین چنل لاگ کن")
            return
        except Exception:  # noqa: BLE001
            log.exception("log_channel_error")
            return


async def worker() -> None:
    global _running
    if not CHANNEL or _running:
        return
    _running = True
    emit("✅ <b>Day Zero روشن شد</b>")
    while True:
        try:
            first = await _q.get()
            await asyncio.sleep(3)
            items = [first]
            while not _q.empty():
                items.append(_q.get_nowait())
            chunk = ""
            for it in items:
                if len(chunk) + len(it) + 2 > 3800:
                    await _send(chunk)
                    chunk = ""
                chunk += ("\n\n" if chunk else "") + it
            if chunk:
                await _send(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("gamelog_worker_error")


# ───────────────────────── اتصال خودکار به بقیه‌ی کد ─────────────────────────
def _rec() -> Optional[list]:
    return _ctx.get()


def _mod(name: str):
    return sys.modules.get(name)


def _wrap(mod, name: str, factory) -> None:
    orig = getattr(mod, name, None)
    if orig is not None:
        setattr(mod, name, factory(orig))


def _hook_modules() -> None:
    eco, combat, actions, duel = _mod("eco"), _mod("combat"), _mod("actions"), _mod("duel")
    if eco is None or hasattr(eco, "gamelog"):      # نسخه‌ای که خودش لاگ داره → دوباره وصل نکن
        return

    def f_ensure(orig):
        async def w(tg, *a, **k):
            user, new = await orig(tg, *a, **k)
            if new:
                emit(f"🆕 <b>بازیکن جدید</b>: {who(user)}")
            return user, new
        return w

    def f_update(orig):
        async def w(uid, **kw):
            ok = await orig(uid, **kw)
            r = _rec()
            if r is not None:
                r.append(("upd", uid, kw, ok))
            return ok
        return w

    def f_settle(orig):
        async def w(uid):
            amount = await orig(uid)
            r = _rec()
            if r is not None:
                r.append(("settle", uid, amount))
            return amount
        return w

    _wrap(eco, "ensure_user", f_ensure)
    _wrap(eco, "update_user", f_update)
    _wrap(eco, "settle", f_settle)

    if combat is not None:
        def f_attack(orig):
            async def w(*a, **k):
                text = await orig(*a, **k)
                if isinstance(text, str) and ("حمله موفق" in text or "حمله دفع شد" in text):
                    emit(f"⚔️ <b>حمله</b>: {who(a[1])} ← {who(a[2])}\n{text}")
                return text
            return w
        _wrap(combat, "do_attack", f_attack)

    if actions is not None:
        def f_baby(orig):
            async def w(uid, *a, **k):
                ok, text = await orig(uid, *a, **k)
                if ok:
                    u = await eco.get_user(uid)
                    emit(f"👶 بچه‌دار شد: {who(u)} ({fa(u.kids)} بچه)")
                return ok, text
            return w

        def f_marry(orig):
            async def w(a, b, lock_hours=0, *args, **kwargs):
                ok = await orig(a, b, lock_hours, *args, **kwargs)
                if ok:
                    A, B = await eco.get_user(a), await eco.get_user(b)
                    if A and B:
                        emit(f"💍 ازدواج: {who(A)} + {who(B)}" + (" (طلسم عاشقی 💘)" if lock_hours else ""))
                return ok
            return w

        def f_divorce(orig):
            async def w(a, b, ban_hours=0):
                await orig(a, b, ban_hours)
                A, B = await eco.get_user(a), await eco.get_user(b)
                if A and B:
                    emit(f"💔 طلاق: {who(A)} ✂️ {who(B)}" + (" (طلسم طلاق)" if ban_hours else ""))
            return w

        def f_item(label: str, markers: tuple):
            def factory(orig):
                async def w(bot, actor, target):
                    text = await orig(bot, actor, target)
                    if isinstance(text, str) and any(m in text for m in markers):
                        emit(f"{label}: {who(actor)} ← {who(target)}")
                    return text
                return w
            return factory

        _wrap(actions, "baby", f_baby)
        _wrap(actions, "do_marry", f_marry)
        _wrap(actions, "do_divorce", f_divorce)
        _wrap(actions, "jinn", f_item("👹 جن", ("دزدید", "دفع شد")))
        _wrap(actions, "vthief", f_item("🥷 دزد گاوصندوق", ("زد به گاوصندوق",)))
        _wrap(actions, "htheft", f_item("🥷 دزدی از خانه", ("دزدی موفق!",)))
        _wrap(actions, "hack", f_item("💻 هک", ("هک موفق!",)))
        _wrap(actions, "love", f_item("💘 طلسم عاشقی", ("طلسم عاشقی گرفت",)))
        _wrap(actions, "split", f_item("💔 طلسم طلاق", ("طلسم طلاق!",)))

    if duel is not None:
        kinds = getattr(duel, "KINDS", {"ttt": ("❌⭕", "دوز"), "dice": ("🎲", "تاس")})

        def f_create(orig):
            async def w(bot, kind, p1, p2, stake, *a, **k):
                err = await orig(bot, kind, p1, p2, stake, *a, **k)
                if err is None:
                    emit(f"🆚 چالش {kinds[kind][1]}: {who(p1)} ← {who(p2)} شرط {fa(stake)}")
                return err
            return w

        def f_finish(orig):
            async def w(bot, m, winner, *a, **k):
                ok = await orig(bot, m, winner, *a, **k)
                if ok:
                    res = "🤝 مساوی" if winner is None else f"🏆 برنده {esc(m.n1 if winner == m.p1 else m.n2)}"
                    emit(f"{kinds[m.kind][0]} نتیجه {kinds[m.kind][1]}: {esc(m.n1)} <code>{m.p1}</code> 🆚 "
                         f"{esc(m.n2)} <code>{m.p2}</code> شرط {fa(m.stake)} — {res}")
                return ok
            return w
        _wrap(duel, "create", f_create)
        _wrap(duel, "finish", f_finish)


# ───────────────────────── لاگ خریدها و کارهای دکمه‌ای ─────────────────────────
def _label(d: str) -> Optional[str]:
    rules = _mod("rules")
    if rules is None:
        return None
    p = d.split(":")
    try:
        if d.startswith("biz:buy:"):
            b = rules.BIZ_INDEX[p[2]]; return f"🏪 کسب‌وکار جدید ← {b['emoji']} {b['name']}"
        if d.startswith("biz:hire:"):
            b = rules.BIZ_INDEX[p[2]]
            w = b["workers"][int(p[3])] if len(p) > 3 and p[3].isdigit() and int(p[3]) < len(b["workers"]) else b["workers"][0]
            return f"👷 استخدام {w['name']} ← {b['emoji']} {b['name']}"
        if d.startswith(("bo:b:", "bo:x:")):
            b = rules.BIZ_INDEX[p[2]]
            return f"📈 {'خرید' if p[1] == 'b' else 'فروش'} سهام ← {b['emoji']} {b['name']} ({p[3]})"
        if d.startswith("mk:buy:"):
            i = rules.SHOP_INDEX[p[2]]; return f"🛒 خرید از فروشگاه ← {i['emoji']} {i['name']}"
        if d.startswith("mk:dbuy:"):
            i = rules.DARK_INDEX[p[2]]; return f"🕶 خرید از دارک وب ← {i['emoji']} {i['name']}"
        if d.startswith("forces:buy:"):
            f = rules.FORCE_INDEX[p[2]]; return f"💂 استخدام نیرو ← {f['emoji']} {f['name']}"
        if d.startswith("fam:eat:"):
            f = rules.FOOD_INDEX[p[2]]; return f"🍽 غذای بچه‌ها ← {f['emoji']} {f['name']}"
    except KeyError:
        return None
    if d.startswith("al:dp:"):
        return "🏦 واریز به بانک اتحاد"
    return {"bo:div": "💰 سود سهام", "house:up": "🏠 ارتقای خونه", "fam:cook": "👨‍🍳 استخدام آشپز", "fam:banana": "🍌 موز",
            "fam:potion": "🧪 معجون"}.get(d)


def _after(event: Any, rec: list) -> None:
    tg = getattr(event, "from_user", None)
    if tg is None or not rec:
        return
    upds = [r for r in rec if r[0] == "upd" and r[3]]
    spent = next((-r[2]["cash"] for r in upds if r[2].get("cash", 0) < 0), 0)
    if isinstance(event, CallbackQuery):
        d = event.data or ""
        if d.startswith("g:") and "play" in d:                         # قمار
            net = next((r[2]["inc"]["stats.casino_net"] for r in upds if "stats.casino_net" in (r[2].get("inc") or {})), None)
            if net is not None and spent >= CASINO_MIN:
                game = d.split(":")[1]
                emit(f"🎰 قمار {game}: {who(tg)} شرط {fa(spent)} ← {'+' if net > 0 else ''}{fa(net)}")
            return
        if d == "daily":
            r = next((r for r in upds if "daily_at" in (r[2].get("set_") or {})), None)
            if r:
                emit(f"🎁 جایزه‌ی روزانه: {who(tg)} +{fa(r[2].get('cash', 0))}")
            return
        if d == "biz:collect":
            amt = next((r[2] for r in rec if r[0] == "settle" and r[2] > 0), 0)
            if amt:
                emit(f"💰 برداشت درآمد: {who(tg)} +{fa(amt)}")
            return
        if d.startswith("al:join:") or d == "al:leave":
            r = next((r for r in upds if "alliance_id" in (r[2].get("set_") or {})), None)
            if r:
                emit(("➕ پیوستن به اتحاد: " if d != "al:leave" else "🚪 خروج از اتحاد: ") + who(tg))
            return
        if spent > 0:
            label = _label(d)
            if label:
                emit(f"{label} ({fa(spent)}): {who(tg)}")
    elif isinstance(event, Message) and spent >= 1 and event.text:     # ساخت اتحاد (پیام اسم)
        if any((r[2].get("flt") or {}).get("alliance_id", 0) is None for r in upds):
            emit(f"🤝 اتحاد جدید «{esc(event.text[:30])}» ({fa(spent)}): {who(tg)}")


class _LogMW(BaseMiddleware):
    async def __call__(self, handler, event, data):
        rec: list = []
        tok = _ctx.set(rec)
        try:
            return await handler(event, data)
        finally:
            _ctx.reset(tok)
            try:
                _after(event, rec)
            except Exception:  # noqa: BLE001
                log.exception("gamelog_after_error")


def _install(dp) -> None:
    global _installed
    if _installed:
        return
    _installed = True
    try:
        _hook_modules()
        eco = _mod("eco")
        if eco is not None and not hasattr(eco, "gamelog"):
            mw = _LogMW()
            dp.message.outer_middleware(mw)
            dp.callback_query.outer_middleware(mw)
    except Exception:  # noqa: BLE001
        log.exception("gamelog_install_error")


_orig_start_polling = Dispatcher.start_polling


async def _start_polling(self, *bots, **kw):
    if bots:
        setup(bots[0])
    _install(self)
    task = asyncio.create_task(worker()) if (CHANNEL and not _running) else None
    try:
        return await _orig_start_polling(self, *bots, **kw)
    finally:
        if task:
            task.cancel()


Dispatcher.start_polling = _start_polling      # با import این فایل، خودکار فعال می‌شه


try:                                            # اعلان آپدیت (اگه announce.py کنارش بود) خودکار فعال می‌شه
    import announce  # noqa: F401
except ImportError:
    pass
