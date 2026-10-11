"""میان‌افزار: ضداسپم، ساخت کاربر، قفل شخصی (جلوی دوبار-کلیک و race)."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

import config
import eco
from redis_service import redis_service


class GameMiddleware(BaseMiddleware):
    """برای callback و پیام‌های پیوی: کاربر را می‌سازد و کل پردازش را زیر قفل همان کاربر اجرا می‌کند.
    پیام‌های گروه دست‌نخورده رد می‌شوند (دستورهای گروه خودشان قفل می‌گیرند)."""

    async def __call__(self, handler: Callable[[Any, dict], Awaitable[Any]], event: Any, data: dict) -> Any:
        tg = data.get("event_from_user")
        if tg is None or tg.is_bot:
            return await handler(event, data)
        is_cb = isinstance(event, CallbackQuery)
        is_pm = isinstance(event, Message) and event.chat.type == "private"
        if not (is_cb or is_pm):
            return await handler(event, data)
        if not await redis_service.r.set(f"thr:{tg.id}", "1", nx=True, px=config.THROTTLE_MS):
            if is_cb:
                await event.answer("⏳")
            return None
        async with redis_service.lock(f"u:{tg.id}", ttl=20, wait=6):
            user, is_new = await eco.ensure_user(tg)
            data["user"], data["is_new"] = user, is_new
            return await handler(event, data)
