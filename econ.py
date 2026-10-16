"""اتفاق‌های اقتصادی شهر + کش قیمت سهام.

این فایل به eco وابسته نیست (eco خودش اینو ایمپورت می‌کنه تا درآمد کاسبی با اتفاق‌ها حساب بشه).
اتفاق‌ها تو Mongo ذخیره می‌شن (EconEvent) و یه کپی تو حافظه نگه داشته می‌شه که هر دور بورس تازه می‌شه.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import config as C
import rules
from util import dur, fa, utcnow

_EVENTS: list[dict] = []          # اتفاق‌های ۱۴ روز اخیر (به ترتیب شروع)
PRICES: dict[str, float] = {}     # کلید کسب‌وکار → قیمت فعلی سهم (برای نمایش و ثروت کل)
KEEP_DAYS = 14


# ───────────────────────── کش ─────────────────────────
async def refresh() -> None:
    """اتفاق‌ها و قیمت‌ها رو از Mongo می‌خونه."""
    from db import EconEvent, Stock
    now = utcnow()
    evs = await EconEvent.get_motor_collection().find({"end": {"$gte": now - timedelta(days=KEEP_DAYS)}}).sort("start", 1).to_list(1000)
    _EVENTS[:] = [{"eid": e["eid"], "emoji": e.get("emoji", ""), "title": e.get("title", ""), "desc": e.get("desc", ""),
                   "start": e["start"], "end": e["end"], "income": e.get("income", {}), "stock": e.get("stock", {})}
                  for e in evs]
    for st in await Stock.get_motor_collection().find({}, {"key": 1, "p": 1}).to_list(100):
        PRICES[st["key"]] = float(st["p"])


def add_event(ev: dict) -> None:
    _EVENTS.append(ev)
    _EVENTS.sort(key=lambda e: e["start"])


def events() -> list[dict]:
    return list(_EVENTS)


def current_event(now: Optional[datetime] = None) -> Optional[dict]:
    now = now or utcnow()
    for e in reversed(_EVENTS):
        if e["start"] <= now < e["end"]:
            return e
    return None


# ───────────────────────── اثر روی درآمد ─────────────────────────
def sector_of(key: str) -> str:
    return C.SECTOR_OF.get(key, "")


def _inst_income(u, key: str) -> float:
    """درآمد هر ۶ ساعت یه کسب‌وکار بازیکن (پایه × ضریب کارگرها)."""
    return rules.BIZ_INDEX[key]["income"] * rules.biz_multiplier(u.businesses, key, u.staff)


def avg_income_mults(since: datetime, now: datetime) -> dict[str, float]:
    """میانگین زمانی ضریب درآمد هر بخش تو بازه‌ی [since, now] (جاهایی که اتفاقی نبوده ضریب ۱)."""
    el = (now - since).total_seconds()
    if el <= 0:
        return {}
    acc: dict[str, float] = {}
    for e in _EVENTS:
        ov = (min(now, e["end"]) - max(since, e["start"])).total_seconds()
        if ov <= 0:
            continue
        for sec, m in e["income"].items():
            acc[sec] = acc.get(sec, 0.0) + ov * (m - 1.0)
    return {sec: 1.0 + v / el for sec, v in acc.items()}


def adjusted_base_income(u, now: Optional[datetime] = None) -> int:
    """درآمد پایه‌ی هر ۶ ساعت با در نظر گرفتن اتفاق‌های اقتصادی از زمان آخرین برداشت تا حالا."""
    now = now or utcnow()
    since = u.income_since
    if C.INCOME_CAP_HOURS:
        since = max(since, now - timedelta(hours=C.INCOME_CAP_HOURS))
    mults = avg_income_mults(since, now)
    if not mults:
        return rules.base_income(u.businesses, u.staff)
    total = 0.0
    for k in u.businesses:
        if k not in rules.BIZ_INDEX:
            continue
        total += _inst_income(u, k) * mults.get(sector_of(k), 1.0)
    return int(total)


def current_effect(u, now: Optional[datetime] = None) -> float:
    """نسبت درآمد بازیکن با اثر اتفاق فعلی به درآمد بدون اتفاق (۱٫۰ = بدون اثر)."""
    ev = current_event(now)
    if not ev or not u.businesses:
        return 1.0
    base = cur = 0.0
    for k in u.businesses:
        if k not in rules.BIZ_INDEX:
            continue
        inc = _inst_income(u, k)
        base += inc
        cur += inc * ev["income"].get(sector_of(k), 1.0)
    return cur / base if base else 1.0


def stock_factor(key: str, now: Optional[datetime] = None) -> float:
    """ضریب «ارزش واقعی» سهم تحت اتفاق فعلی."""
    ev = current_event(now)
    return ev["stock"].get(C.SECTOR_OF.get(key, ""), 1.0) if ev else 1.0


# ───────────────────────── ارزش سهم‌های بازیکن ─────────────────────────
def holdings_value(u) -> int:
    return int(sum(n * PRICES.get(k, 0.0) for k, n in (getattr(u, "stocks", None) or {}).items() if n > 0))


# ───────────────────────── متن ─────────────────────────
def pct(m: float) -> str:
    v = round((m - 1.0) * 100)
    return f"+{fa(v)}٪" if v >= 0 else f"−{fa(-v)}٪"


def effects_lines(ev: dict) -> list[str]:
    lines = []
    for sec, m in sorted(ev["income"].items(), key=lambda kv: -kv[1]):
        emo, name, _ = C.SECTORS[sec]
        lines.append(f"{emo} {name}: درآمد {pct(m)} {'🟢' if m >= 1 else '🔴'}")
    return lines


def remaining_text(ev: dict, now: Optional[datetime] = None) -> str:
    """زمان باقی‌مونده، تقریبی (رُند به نیم‌ساعت) تا کسی دقیق وقت پایان رو حدس نزنه."""
    left = max(0.0, (ev["end"] - (now or utcnow())).total_seconds())
    half = max(1, int(-(-left // 1800)))
    return f"حدود {dur(half * 1800)}"


def describe_event(ev: dict, now: Optional[datetime] = None) -> str:
    return (f"{ev['emoji']} <b>{ev['title']}</b>\n{ev['desc']}\n" + "\n".join(effects_lines(ev))
            + f"\n⏳ {remaining_text(ev, now)} دیگه ادامه داره")
