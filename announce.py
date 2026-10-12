"""Day Zero — اعلان آپدیت به بازیکن‌ها (فایل مستقل).

هر بار نسخه‌ی ربات عوض بشه، یک‌بار به همه‌ی بازیکن‌ها پیام «ربات آپدیت شد؛ /start بزن» می‌ره.
ری‌استارت‌های معمولی (کرش، دیپلوی بدون تغییر) پیام تکراری نمی‌فرسته.

فعال‌سازی: کنار main.py بذار و بالای main.py بنویس   import announce
(اگه gamelog.py رو هم داری، با همون `import gamelog` خودکار فعال می‌شه.)

نسخه از این‌ها (به ترتیب) حساب می‌شه:  BOT_VERSION  →  RAILWAY_GIT_COMMIT_SHA  →  هش فایل‌های .py
متغیرهای اختیاری:
  UPDATE_NOTE       توضیح کوتاه تغییرات که ته پیام اضافه می‌شه
  UPDATE_BROADCAST  اگه 0 بذاری اعلان خاموش می‌شه
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import os
import sys
from typing import Optional

import structlog
from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

log = structlog.get_logger("announce")

ENABLED = os.getenv("UPDATE_BROADCAST", "1") not in ("0", "false", "False", "")
NOTE = os.getenv("UPDATE_NOTE", "").strip()


def _version() -> str:
    v = os.getenv("BOT_VERSION") or (os.getenv("RAILWAY_GIT_COMMIT_SHA") or "")[:12]
    if v:
        return v
    h = hashlib.sha1()
    for f in sorted(glob.glob("*.py")):
        try:
            h.update(open(f, "rb").read())
        except OSError:
            pass
    return h.hexdigest()[:12]


def _text() -> str:
    t = ("🔄 <b>Day Zero آپدیت شد!</b>\n\n"
         "یه نسخه‌ی جدید از ربات اومده. برای اینکه همه‌چیز درست کار کنه، یه‌بار /start بزن "
         "(یا دکمه‌ی پایین رو بزن).")
    return t + (f"\n\n📝 <b>تغییرات:</b>\n{NOTE}" if NOTE else "")


async def broadcast(bot) -> None:
    """اگه نسخه عوض شده باشه به همه‌ی بازیکن‌ها پیام می‌ده."""
    if not ENABLED:
        return
    from db import User            # دیر ایمپورت می‌کنیم تا دیتابیس آماده باشه
    users = User.get_motor_collection()
    meta = users.database["meta"]
    version = _version()
    try:
        prev = await meta.find_one_and_update(
            {"_id": "bot_version", "v": {"$ne": version}}, {"$set": {"v": version, "state": "sending"}},
            upsert=True, return_document=ReturnDocument.BEFORE)
    except DuplicateKeyError:
        return                                    # همین نسخه قبلاً اعلان شده (یا یه نمونه‌ی دیگه داره می‌فرسته)
    log.info("announce_start", version=version, previous=(prev or {}).get("v"))

    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 آپدیت شد — شروع", callback_data="menu")]])
    text = _text()
    sent = failed = 0
    async for doc in users.find({}, {"tg_id": 1}):
        uid = doc["tg_id"]
        for _ in range(3):
            try:
                await bot.send_message(uid, text, reply_markup=markup, disable_notification=False)
                sent += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (TelegramForbiddenError, TelegramBadRequest):
                failed += 1                       # ربات رو بلاک کرده یا هیچ‌وقت پیوی استارت نکرده
                break
            except Exception:  # noqa: BLE001
                failed += 1
                break
        await asyncio.sleep(0.06)                  # ≈ ۱۶ پیام در ثانیه؛ زیر محدودیت تلگرام
    await meta.update_one({"_id": "bot_version"}, {"$set": {"state": "done", "sent": sent, "failed": failed}})
    log.info("announce_done", sent=sent, failed=failed)
    gl = sys.modules.get("gamelog")
    if gl is not None:
        gl.emit(f"📢 <b>اعلان آپدیت</b> (نسخه {version}): ارسال‌شده {sent} — ناموفق {failed}")


_orig_start_polling = Dispatcher.start_polling
_task: Optional[asyncio.Task] = None


async def _start_polling(self, *bots, **kw):
    global _task
    if bots and ENABLED and _task is None:
        async def _run():
            await asyncio.sleep(5)                 # بذار polling بالا بیاد تا /start فوراً جواب بده
            try:
                await broadcast(bots[0])
            except Exception:  # noqa: BLE001
                log.exception("announce_error")
        _task = asyncio.create_task(_run())
    return await _orig_start_polling(self, *bots, **kw)


Dispatcher.start_polling = _start_polling
