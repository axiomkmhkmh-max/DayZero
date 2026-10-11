"""Redis: کول‌داون، قفل توزیع‌شده (ضد race-condition) و کمک‌تابع‌های سشن."""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

from redis.asyncio import Redis

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""


class LockTimeout(Exception):
    pass


class RedisService:
    def __init__(self) -> None:
        self._r: Redis | None = None

    @property
    def r(self) -> Redis:
        assert self._r is not None, "Redis متصل نیست"
        return self._r

    async def connect(self, url: str) -> None:
        self._r = Redis.from_url(url, decode_responses=True)
        await self._r.ping()

    async def close(self) -> None:
        if self._r is not None:
            await self._r.aclose()

    async def cooldown(self, key: str, seconds: float) -> int:
        """کول‌داون را شروع می‌کند. اگر مجاز بود 0، وگرنه ثانیه‌ی باقی‌مانده."""
        ok = await self.r.set(f"cd:{key}", "1", nx=True, px=int(seconds * 1000))
        if ok:
            return 0
        ttl = await self.r.pttl(f"cd:{key}")
        return max(1, int(round(ttl / 1000))) if ttl > 0 else 1

    async def clear_cooldown(self, key: str) -> None:
        await self.r.delete(f"cd:{key}")

    @asynccontextmanager
    async def lock(self, name: str, ttl: float = 10.0, wait: float = 5.0):
        """قفل توزیع‌شده با توکن (آزادسازی امن با Lua)."""
        key, token = f"lock:{name}", uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while not await self.r.set(key, token, nx=True, px=int(ttl * 1000)):
            if loop.time() > deadline:
                raise LockTimeout(name)
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            try:
                await self.r.eval(_RELEASE, 1, key, token)
            except Exception:  # noqa: BLE001
                pass


redis_service = RedisService()
