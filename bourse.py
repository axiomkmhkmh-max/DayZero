"""بورس: سهام کسب‌وکارها + حلقه‌ی اتفاق‌های اقتصادی و قیمت‌گذاری.

مدل قیمت (استخر ثابت‌حاصل‌ضرب، مثل صرافی‌های غیرمتمرکز):
  • هر سهم یه قیمت مشترک p و یه ذخیره‌ی سهام s داره (سقف تعداد سهام قابل معامله).
  • خرید q سهم:  هزینه = p·s·q/(s−q) ، بعدش قیمت = p·(s/(s−q))²   → هر خرید قیمت رو برای همه بالا می‌بره.
  • فروش q سهم:  دریافتی = p·s·q/(s+q) ، بعدش قیمت = p·(s/(s+q))² → هر فروش قیمت رو برای همه پایین میاره.
  • هر دور (STOCK_TICK_SEC) قیمت به «ارزش واقعی» = قیمت پایه × اثر اتفاق اقتصادی × نوسان تصادفی برمی‌گرده.
تعارض هم‌زمان دو بازیکن روی یه سهم با شماره‌ی نسخه (rev) اتمیک حل می‌شه.
"""
from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from typing import Optional

import structlog
from pymongo import ReturnDocument

import config as C
import eco
import econ
import rules
from db import EconEvent, MarketState, Stock
from redis_service import redis_service
from util import esc, fa, utcnow

log = structlog.get_logger("bourse")
RNG = rules.RNG
SPARK = "▁▂▃▄▅▆▇█"


def _col():
    return Stock.get_motor_collection()


# ───────────────────────── قیمت و کوت ─────────────────────────
def base_price(key: str) -> float:
    return float(max(500, round(rules.BIZ_INDEX[key]["income"] * C.STOCK_PRICE_PER_INCOME / 100) * 100))


def stock_name(key: str) -> str:
    bz = rules.BIZ_INDEX[key]
    return f"{bz['emoji']} {bz['name']}"


def max_trade(s: float) -> int:
    return max(1, int(s * C.STOCK_MAX_TRADE_FRAC))


def buy_cost(p: float, s: float, q: int) -> int:
    return math.ceil(p * s * q / (s - q) * (1 + C.STOCK_FEE))


def sell_gain(p: float, s: float, q: int) -> int:
    return int(p * s * q / (s + q) * (1 - C.STOCK_FEE))


def after_buy(p: float, s: float, q: int) -> tuple[float, float]:
    s2 = s - q
    return p * (s / s2) ** 2, s2


def after_sell(p: float, s: float, q: int) -> tuple[float, float]:
    s2 = s + q
    return p * (s / s2) ** 2, s2


def spark(hist: list[float]) -> str:
    if len(hist) < 2:
        return "▁"
    lo, hi = min(hist), max(hist)
    if hi - lo < 1e-9:
        return SPARK[3] * len(hist)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in hist)


def change_pct(st: dict) -> float:
    h = st.get("hist") or []
    return (float(st["p"]) / h[0] - 1.0) * 100 if h and h[0] else 0.0


def arrow(pct_: float) -> str:
    return "🟢▲" if pct_ >= 0.5 else "🔴▼" if pct_ <= -0.5 else "⚪️▬"


async def get_stocks() -> list[dict]:
    docs = {d["key"]: d for d in await _col().find({}).to_list(100)}
    return [docs[k] for k in rules.BIZ_INDEX if k in docs]


async def get_stock(key: str) -> Optional[dict]:
    return await _col().find_one({"key": key})


async def ensure_stocks() -> None:
    """موقع استارت: سهم‌های نبوده ساخته می‌شن و قیمت پایه‌ی جدید (اگه بالانس عوض شده) ست می‌شه."""
    now = utcnow()
    for key in rules.BIZ_INDEX:
        p0 = base_price(key)
        await _col().update_one(
            {"key": key}, {"$set": {"p0": p0},
                           "$setOnInsert": {"p": p0, "s": float(C.STOCK_SHARES0), "x": 0.0, "rev": 0,
                                            "hist": [p0], "hist_at": now}}, upsert=True)
    await econ.refresh()


# ───────────────────────── سود سهام ─────────────────────────
async def settle_div(uid: int) -> int:
    """سود سهام انباشته تا الان رو به «سود دریافت‌نشده»ی بازیکن اضافه می‌کنه.
    قبل از هر خرید/فروش صدا زده می‌شه تا سهم تازه‌خریده‌شده سودِ گذشته نگیره."""
    for _ in range(3):
        u = await eco.get_user(uid)
        if u is None:
            return 0
        now = utcnow()
        old = u.stock_div_at
        amount = 0.0
        if old and u.stocks:
            hours = min(float(C.STOCK_DIV_MAX_HOURS), max(0.0, (now - old).total_seconds() / 3600))
            mults = econ.avg_income_mults(old, now)
            for k, n in u.stocks.items():
                if n > 0 and k in rules.BIZ_INDEX:
                    amount += n * econ.PRICES.get(k, 0.0) * C.STOCK_DIV_PER_HOUR * hours * mults.get(C.SECTOR_OF.get(k, ""), 1.0)
        r = await eco.col().update_one({"tg_id": uid, "stock_div_at": old},
                                       {"$set": {"stock_div_at": now}, "$inc": {"stock_div": int(amount)}})
        if r.matched_count == 1:
            return int(amount)
    return 0


async def claim_div(uid: int) -> int:
    await settle_div(uid)
    u = await eco.get_user(uid)
    amount = int(u.stock_div) if u else 0
    if amount <= 0:
        return 0
    if not await eco.update_user(uid, inc={"stock_div": -amount}, flt={"stock_div": {"$gte": amount}}):
        return 0
    await eco.gain(uid, amount)
    return amount


# ───────────────────────── معامله ─────────────────────────
async def buy(uid: int, key: str, q: int) -> tuple[bool, str, dict]:
    """خروجی: (موفق؟, کد, اطلاعات). کدها: ok · bad · big · hold · cash · race"""
    if key not in rules.BIZ_INDEX or q <= 0:
        return False, "bad", {}
    if q > C.STOCK_MAX_HOLD:
        return False, "hold", {}
    await settle_div(uid)
    field = f"stocks.{key}"
    for _ in range(4):
        st = await get_stock(key)
        if not st:
            return False, "bad", {}
        p, s, rev = float(st["p"]), float(st["s"]), int(st["rev"])
        if q > max_trade(s):
            return False, "big", {"max": max_trade(s)}
        cost = buy_cost(p, s, q)
        hold_flt = {"$expr": {"$lte": [{"$ifNull": [f"${field}", 0]}, C.STOCK_MAX_HOLD - q]}}
        if not await eco.spend(uid, cost, inc={field: q, f"stock_cost.{key}": cost}, flt=hold_flt):
            u = await eco.get_user(uid)
            return False, ("cash" if u is not None and u.cash < cost else "hold"), {"cost": cost}
        p2, s2 = after_buy(p, s, q)
        r = await _col().update_one({"key": key, "rev": rev}, {"$set": {"p": p2, "s": s2}, "$inc": {"rev": 1}})
        if r.matched_count == 1:
            econ.PRICES[key] = p2
            return True, "ok", {"cost": cost, "price": p2}
        await eco.update_user(uid, inc={field: -q, f"stock_cost.{key}": -cost}, cash=cost)   # کس دیگه‌ای قیمت رو عوض کرد؛ برگردون و دوباره
    return False, "race", {}


async def sell(uid: int, key: str, q: int) -> tuple[bool, str, dict]:
    """کدها: ok · bad · held · big · race"""
    if key not in rules.BIZ_INDEX or q <= 0:
        return False, "bad", {}
    await settle_div(uid)
    field = f"stocks.{key}"
    for _ in range(4):
        u = await eco.get_user(uid)
        held = int(u.stocks.get(key, 0)) if u else 0
        if held < q:
            return False, "held", {}
        st = await get_stock(key)
        if not st:
            return False, "bad", {}
        p, s, rev = float(st["p"]), float(st["s"]), int(st["rev"])
        if q > max_trade(s):
            return False, "big", {"max": max_trade(s)}
        basis = int(u.stock_cost.get(key, 0)) * q // held
        gain = sell_gain(p, s, q)
        if not await eco.update_user(uid, inc={field: -q, f"stock_cost.{key}": -basis}, flt={field: {"$gte": q}}):
            return False, "held", {}
        p2, s2 = after_sell(p, s, q)
        r = await _col().update_one({"key": key, "rev": rev}, {"$set": {"p": p2, "s": s2}, "$inc": {"rev": 1}})
        if r.matched_count == 1:
            await eco.gain(uid, gain)
            econ.PRICES[key] = p2
            return True, "ok", {"gain": gain, "basis": basis, "price": p2}
        await eco.update_user(uid, inc={field: q, f"stock_cost.{key}": basis})
    return False, "race", {}


def max_affordable(p: float, s: float, cash: int, room: int) -> int:
    """بیشترین تعداد سهمی که با این پول و تو این استخر و سقف مالکیت قابل خریده."""
    hi = min(room, max_trade(s))
    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if buy_cost(p, s, mid) <= cash:
            lo = mid
        else:
            hi = mid - 1
    return lo


# ───────────────────────── اتفاق‌های اقتصادی و قیمت‌ها ─────────────────────────
def make_event(now, last_eid: str = "") -> dict:
    pool = [e for e in C.ECON_EVENTS if e[0] != last_eid] or list(C.ECON_EVENTS)
    e = RNG.choices(pool, weights=[x[6] for x in pool], k=1)[0]
    hours = RNG.uniform(*C.ECON_EVENT_HOURS)
    return {"eid": e[0], "emoji": e[1], "title": e[2], "desc": e[3], "start": now,
            "end": now + timedelta(hours=hours), "income": dict(e[4]), "stock": dict(e[5])}


async def _announce(bot, ev: dict) -> None:
    if not C.NEWS_CHANNEL or bot is None:
        return
    text = ("📰 <b>خبر فوری اقتصادی</b>\n\n" + econ.describe_event(ev) +
            f"\n\n📈 قیمت سهام این بخش‌ها همین الان تکون خورد! بورس رو چک کن: @{esc(C.BOT_USERNAME)}")
    try:
        await bot.send_message(C.NEWS_CHANNEL, text)
    except Exception:  # noqa: BLE001
        log.warning("news_send_failed")


async def tick(bot=None) -> None:
    """یک دور: اتفاق جدید اگه وقتشه، بعدش به‌روزرسانی قیمت همه‌ی سهم‌ها."""
    now = utcnow()
    M = MarketState.get_motor_collection()
    state = await M.find_one_and_update(
        {"key": "m"}, {"$setOnInsert": {"key": "m", "next_event_at": now, "last_eid": "", "seen_event": ""}},
        upsert=True, return_document=ReturnDocument.AFTER)
    await econ.refresh()
    cur = econ.current_event(now)
    nxt = state.get("next_event_at")
    if cur is None and (nxt is None or now >= nxt):
        ev = make_event(now, state.get("last_eid", ""))
        await EconEvent.get_motor_collection().insert_one(dict(ev))
        econ.add_event(ev)
        gap = RNG.uniform(*C.ECON_GAP_HOURS)
        await M.update_one({"key": "m"}, {"$set": {"next_event_at": ev["end"] + timedelta(hours=gap), "last_eid": ev["eid"]}})
        await _announce(bot, ev)
        cur = ev
    cur_id = f"{cur['eid']}@{int(cur['start'].timestamp())}" if cur else ""
    changed = cur_id != state.get("seen_event", "")
    pull = C.STOCK_EVENT_PULL if changed else C.STOCK_REVERT
    for st in await _col().find({}).to_list(100):
        for _ in range(3):
            key, p0 = st["key"], float(st["p0"])
            x = float(st.get("x", 0.0)) * (1 - C.STOCK_NOISE_REVERT) + RNG.gauss(0, C.STOCK_NOISE)
            fair = p0 * econ.stock_factor(key, now) * math.exp(x)
            p = float(st["p"]) + (fair - float(st["p"])) * pull
            p = min(p0 * C.STOCK_MAX_X, max(p0 * C.STOCK_MIN_X, p))
            s = float(st["s"]) + (C.STOCK_SHARES0 - float(st["s"])) * 0.05
            upd: dict = {"$set": {"p": p, "s": s, "x": x}, "$inc": {"rev": 1}}
            h_at = st.get("hist_at")
            if h_at is None or (now - h_at).total_seconds() >= 600:
                hist = (list(st.get("hist") or []) + [round(p)])[-36:]
                upd["$set"].update({"hist": hist, "hist_at": now})
            r = await _col().update_one({"key": key, "rev": int(st["rev"])}, upd)
            if r.matched_count == 1:
                econ.PRICES[key] = p
                break
            st = await get_stock(key) or st            # همزمان معامله شد؛ با نسخه‌ی تازه دوباره
    if changed:
        await M.update_one({"key": "m"}, {"$set": {"seen_event": cur_id}})


async def worker(bot) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            if await redis_service.r.set("bourse:tick", "1", nx=True, ex=max(10, C.STOCK_TICK_SEC - 10)):
                await tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("bourse_tick_error")
        await asyncio.sleep(C.STOCK_TICK_SEC)
