"""ابزارهای مشترک ایونت‌های گروهی: خزانه‌ی سرقت، هفته‌ی لیگ، کاربرِ گروه."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from aiogram.types import Message

import eco
from redis_service import redis_service
from util import utcnow

# ───────────────────────── خزانه‌ی مشترک (گاوصندوق سرقت) ─────────────────────────
POT_KEY = "heist:pot"
SEED_POT = 3_000_000            # شروع خزانه (اولین بار)


async def pot() -> int:
    r = redis_service.r
    await r.set(POT_KEY, SEED_POT, nx=True)
    return max(0, int(await r.get(POT_KEY) or 0))


async def pot_add(n: int) -> None:
    """کارمزدها، ورودی‌ها، جریمه‌ها و مزایده‌ها می‌ریزن تو خزانه. هیچ‌وقت کار اصلی رو خراب نمی‌کنه."""
    try:
        if n > 0:
            await pot()
            await redis_service.r.incrby(POT_KEY, int(n))
    except Exception:  # noqa: BLE001
        pass


async def pot_take(n: int) -> int:
    """حداکثر n رو از خزانه برمی‌داره و همون مقدار رو برمی‌گردونه."""
    cur = await pot()
    n = max(0, min(int(n), cur))
    if n:
        await redis_service.r.decrby(POT_KEY, n)
    return n


# ───────────────────────── هفته‌ی لیگ (شنبه ۰۰:۰۰ به وقت تهران) ─────────────────────────
TEHRAN = timedelta(hours=3, minutes=30)


def _local() -> datetime:
    return utcnow() + TEHRAN


def week_id() -> str:
    t = _local()
    sat = t.date() - timedelta(days=(t.weekday() - 5) % 7)
    return sat.isoformat()


def week_left_sec() -> int:
    t = _local()
    nxt = datetime.combine(t.date() - timedelta(days=(t.weekday() - 5) % 7) + timedelta(days=7), datetime.min.time())
    return max(1, int((nxt - t).total_seconds()))


# ───────────────────────── کاربرِ گروه ─────────────────────────
async def as_actor(m: Message, fn: Callable[[Message, Any], Awaitable[Any]]) -> Any:
    """میان‌افزار برای پیام‌های گروه کاربر نمی‌سازه؛ اینجا زیر قفلِ خودش می‌سازیم."""
    if m.from_user is None or m.from_user.is_bot:
        return None
    async with redis_service.lock(f"u:{m.from_user.id}", ttl=20, wait=6):
        actor, _ = await eco.ensure_user(m.from_user)
        return await fn(m, actor)


async def jload(key: str) -> Optional[dict]:
    raw = await redis_service.r.get(key)
    return json.loads(raw) if raw else None


async def jsave(key: str, data: dict, ttl: int) -> None:
    await redis_service.r.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)


async def is_chat_admin(bot, chat_id: int, uid: int) -> bool:
    try:
        mem = await bot.get_chat_member(chat_id, uid)
        return mem.status in ("creator", "administrator")
    except Exception:  # noqa: BLE001
        return False
