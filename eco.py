"""لایه‌ی اقتصاد: عملیات اتمیک روی پول + تسویه‌ی درآمد کاسبی."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from aiogram.types import User as TgUser
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import config as C
import rules
from db import Alliance, Counter, User
from redis_service import redis_service
from util import utcnow


def col():
    return User.get_motor_collection()


async def get_user(uid: int) -> Optional[User]:
    return await User.find_one(User.tg_id == uid)


async def ensure_user(tg: TgUser) -> tuple[User, bool]:
    """کاربر را می‌سازد/به‌روز می‌کند. خروجی: (کاربر, تازه‌ساخته‌شد؟)"""
    uname = tg.username.lower() if tg.username else None
    user = await get_user(tg.id)
    if user is None:
        now = utcnow()
        from datetime import timedelta
        user = User(tg_id=tg.id, name=tg.full_name or "Player", username=uname, created_at=now, income_since=now,
                    shield_until=now + timedelta(hours=C.NEWBIE_SHIELD_HOURS))
        try:
            await user.insert()
            return user, True
        except DuplicateKeyError:
            return await get_user(tg.id), False  # type: ignore[return-value]
    patch: dict[str, Any] = {}
    if user.name != tg.full_name and tg.full_name:
        patch["name"] = tg.full_name
    if user.username != uname:
        patch["username"] = uname
    if (utcnow() - user.last_seen).total_seconds() > 60:
        patch["last_seen"] = utcnow()
    if patch:
        await col().update_one({"tg_id": tg.id}, {"$set": patch})
        for k, v in patch.items():
            setattr(user, k, v)
    return user, False


async def find_by_username(username: str) -> Optional[User]:
    return await User.find_one(User.username == username.lower().lstrip("@"))


# ───────── آدم‌هایی که تو گروه دیده شدن ولی هنوز بازی نکردن ─────────
SEEN_TTL = 30 * 86400


async def remember_seen(tg: TgUser) -> None:
    """هر کسی که تو گروه پیام می‌ده (اسم + یوزرنیم) یادداشت می‌شه تا حتی قبل از بازی کردن بشه بهش پیشنهاد ازدواج داد."""
    r = redis_service.r
    if not await r.set(f"seenw:{tg.id}", "1", nx=True, ex=600):     # حداکثر هر ۱۰ دقیقه یک بار بنویس
        return
    await r.hset(f"seen:{tg.id}", mapping={"name": tg.full_name or "Player", "username": tg.username or ""})
    await r.expire(f"seen:{tg.id}", SEEN_TTL)
    if tg.username:
        await r.set(f"uname:{tg.username.lower()}", tg.id, ex=SEEN_TTL)


async def user_from_seen(uid: int) -> Optional[User]:
    """بازیکنِ ثبت‌شده، یا اگه تو گروه دیده شده ولی هنوز بازی نکرده، همون لحظه براش پروفایل می‌سازه."""
    u = await get_user(uid)
    if u is not None:
        return u
    d = await redis_service.r.hgetall(f"seen:{uid}")
    if not d:
        return None
    tg = TgUser(id=uid, is_bot=False, first_name=d.get("name") or "Player", username=d.get("username") or None)
    return (await ensure_user(tg))[0]


async def resolve_username(name: str) -> Optional[User]:
    u = await find_by_username(name)
    if u is not None:
        return u
    uid = await redis_service.r.get(f"uname:{name.lower().lstrip('@')}")
    return await user_from_seen(int(uid)) if uid else None


# ───────────────────────── پول ─────────────────────────
async def update_user(uid: int, *, inc: Optional[dict] = None, set_: Optional[dict] = None,
                      flt: Optional[dict] = None, cash: int = 0) -> bool:
    """به‌روزرسانی اتمیک؛ cash مثبت = دریافت، منفی = پرداخت (با شرط کافی بودن موجودی)."""
    f: dict[str, Any] = {"tg_id": uid}
    if cash < 0:
        f["cash"] = {"$gte": -cash}
    if flt:
        f.update(flt)
    u: dict[str, Any] = {}
    inc = dict(inc or {})
    if cash:
        inc["cash"] = cash
    if inc:
        u["$inc"] = inc
    if set_:
        u["$set"] = set_
    if not u:
        return (await col().count_documents(f, limit=1)) == 1
    r = await col().update_one(f, u)
    return r.matched_count == 1


async def spend(uid: int, amount: int, *, inc: Optional[dict] = None, set_: Optional[dict] = None,
                flt: Optional[dict] = None, invest: int = 0) -> bool:
    inc = dict(inc or {})
    if invest:
        inc["invested"] = invest
    return await update_user(uid, inc=inc, set_=set_, flt=flt, cash=-amount)


async def gain(uid: int, amount: int) -> bool:
    return await update_user(uid, cash=amount) if amount else True


async def transfer(src: int, dst: int, amount: int, field: str = "cash") -> bool:
    """انتقال اتمیک (از جیب یا گاوصندوق src به جیب dst)."""
    if amount <= 0:
        return False
    r = await col().update_one({"tg_id": src, field: {"$gte": amount}}, {"$inc": {field: -amount}})
    if r.matched_count != 1:
        return False
    await col().update_one({"tg_id": dst}, {"$inc": {"cash": amount}})
    return True


async def to_vault(uid: int, amount: int) -> bool:
    r = await col().update_one({"tg_id": uid, "cash": {"$gte": amount}}, {"$inc": {"cash": -amount, "vault": amount}})
    return r.matched_count == 1


async def from_vault(uid: int, amount: int) -> bool:
    r = await col().update_one({"tg_id": uid, "vault": {"$gte": amount}}, {"$inc": {"vault": -amount, "cash": amount}})
    return r.matched_count == 1


async def next_seq(key: str) -> int:
    doc = await Counter.get_motor_collection().find_one_and_update(
        {"key": key}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    return int(doc["seq"])


# ───────────────────────── درآمد کاسبی ─────────────────────────
async def alliance_of(u: User) -> Optional[Alliance]:
    return await Alliance.find_one(Alliance.aid == u.alliance_id) if u.alliance_id else None


async def calc_pending(u: User, now: Optional[datetime] = None) -> int:
    now = now or utcnow()
    if not u.businesses:
        return 0
    mult = 1.0 + (C.MARRIAGE_BONUS if u.spouse_id else 0.0)
    al = await alliance_of(u)
    if al:
        mult += C.ALLIANCE_LEVELS[al.level][1]
    return rules.calc_income(rules.base_income(u.businesses), u.income_since, now, kids=u.kids,
                             fed_until=u.fed_until, cook=u.cook, banana_until=u.banana_until,
                             potion_until=u.potion_until, cursed_until=u.cursed_until, base_mult=mult)


async def settle(uid: int) -> int:
    """درآمد انباشته را اتمیک به جیب می‌ریزد و ساعت را ریست می‌کند.
    قبل از هر تغییرِ ضریب (غذا، بچه، ازدواج، جن…) صدا زده می‌شود تا ضریب فقط «از اینجا به بعد» اثر کند."""
    for _ in range(3):
        u = await get_user(uid)
        if u is None:
            return 0
        now = utcnow()
        amount = await calc_pending(u, now)
        r = await col().update_one({"tg_id": uid, "income_since": u.income_since},
                                   {"$set": {"income_since": now}, "$inc": {"cash": amount}})
        if r.matched_count == 1:
            return amount
    return 0


async def settle_many(*uids: Optional[int]) -> None:
    for uid in uids:
        if uid:
            await settle(uid)


def net_worth(u: User) -> int:
    return u.cash + u.vault + u.invested
