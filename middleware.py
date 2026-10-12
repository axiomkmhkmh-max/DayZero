"""میان‌افزار: ضداسپم، ساخت کاربر، قفل شخصی (جلوی دوبار-کلیک و race)."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

import config
import eco
from redis_service import redis_service

# دکمه‌هایی که صاحب‌شون خودشون چک می‌کنن (پیشنهاد ازدواج/طلاق، دوئل) یا کاری نمی‌کنن
_OPEN_PREFIXES = ("mr:", "dv:", "mt:", "noop")


class GameMiddleware(BaseMiddleware):
    """برای callback و پیام‌های پیوی: کاربر را می‌سازد و کل پردازش را زیر قفل همان کاربر اجرا می‌کند.
    پیام‌های گروه دست‌نخورده رد می‌شوند (دستورهای گروه خودشان قفل می‌گیرند).
    callbackهای گروه فقط برای صاحب منو (own:chat:msg) مجازند."""

    async def __call__(self, handler: Callable[[Any, dict], Awaitable[Any]], event: Any, data: dict) -> Any:
        tg = data.get("event_from_user")
        if tg is None or tg.is_bot:
            return await handler(event, data)
        is_cb = isinstance(event, CallbackQuery)
        if is_cb and event.message is not None and event.message.chat.type != "private" \
                and not (event.data or "").startswith(_OPEN_PREFIXES):
            # توی گروه هر منو فقط برای کسی که بازش کرده کار می‌کنه
            owner = await redis_service.r.get(f"own:{event.message.chat.id}:{event.message.message_id}")
            if owner is None:
                await event.answer("⌛ این منو دیگه منقضی شده. بنویس «منو» تا منوی خودت باز شه.", show_alert=True)
                return None
            if int(owner) != tg.id:
                await event.answer("🚫 این منوی تو نیست! بنویس «منو» تا منوی خودت باز شه.", show_alert=True)
                return None
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
