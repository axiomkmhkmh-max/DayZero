"""Day Zero — نقطه‌ی شروع ربات."""
from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, InlineKeyboardMarkup

import config
import duel
import gamelog                      # لاگ چنل تلگرام
from db import init_db
from logs import setup_logging
from middleware import GameMiddleware
from redis_service import redis_service

import h_alliance
import h_biz
import h_casino
import h_fight
import h_group
import h_home
import h_life
import h_market
import h_menu
import h_social
import h_target

try:                                 # اعلان آپدیت به بازیکن‌ها (اگه announce.py کنار main.py باشه)
    import announce  # noqa: F401
except ImportError:
    pass

log = structlog.get_logger("main")

# ───────────────────────── اعداد انگلیسی ─────────────────────────
# همه‌ی متن‌هایی که ربات می‌فرسته (پیام، کپشن، دکمه، هشدار) قبل از ارسال به ارقام انگلیسی تبدیل می‌شن
_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬٪٫", "01234567890123456789,%.")


def _en(s: str) -> str:
    return s.translate(_EN)


def _btn(s: str) -> str:
    """متن دکمه: ارقام انگلیسی + «◀️ منو» → «◀️ پنل» (چون منو شد پنل)."""
    return _en(s).replace("◀️ منو", "◀️ پنل")


def _en_markup(mk):
    if isinstance(mk, InlineKeyboardMarkup):
        rows = [[btn.model_copy(update={"text": _btn(btn.text)}) for btn in row] for row in mk.inline_keyboard]
        return mk.model_copy(update={"inline_keyboard": rows})
    return mk


async def _english_digits(make_request, bot, method):
    for field in ("text", "caption"):
        v = getattr(method, field, None)
        if isinstance(v, str):
            setattr(method, field, _en(v))
    mk = getattr(method, "reply_markup", None)
    if mk is not None:
        setattr(method, "reply_markup", _en_markup(mk))
    return await make_request(bot, method)

COMMANDS = [
    BotCommand(command="market", description="🛒 بازار"),
    BotCommand(command="home", description="🏠 خونه"),
    BotCommand(command="biz", description="🏪 کاسبی"),
    BotCommand(command="menu", description="🌅 پنل اصلی"),
    BotCommand(command="me", description="💵 موجودی و قدرت"),
    BotCommand(command="attack", description="⚔️ حمله (ریپلای + اسلحه)"),
    BotCommand(command="ttt", description="❌⭕ دوز با شرط (ریپلای + مبلغ)"),
    BotCommand(command="dice", description="🎲 تاس با شرط (ریپلای + مبلغ)"),
    BotCommand(command="marry", description="💍 ثبت ازدواج (ریپلای)"),
    BotCommand(command="divorce", description="💔 درخواست طلاق"),
    BotCommand(command="baby", description="👶 بچه‌دار شدن"),
    BotCommand(command="pocket", description="💸 پول تو جیبی (ساعتی یک بار)"),
    BotCommand(command="steal", description="🥷 دزدی از خانه (ریپلای)"),
    BotCommand(command="hack", description="💻 هکش کن (ریپلای)"),
    BotCommand(command="transfer", description="💸 انتقال پول (ریپلای + مبلغ)"),
]


async def main() -> None:
    config.validate()
    setup_logging(config.LOG_LEVEL)
    await redis_service.connect(config.REDIS_URL)
    mongo = await init_db()

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(_english_digits)
    dp = Dispatcher(storage=RedisStorage(redis_service.r))

    mw = GameMiddleware()
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)

    # ترتیب مهمه: حالت‌های FSM (گرفتن هدف/اسم) قبل از دستورهای متنی
    for r in (h_target.router, h_alliance.router, h_menu.router, h_life.router, h_home.router, h_biz.router, h_market.router,
              h_casino.router, h_fight.router, h_social.router, duel.router, h_group.router):
        dp.include_router(r)

    duel.set_bot(bot)
    gamelog.setup(bot)
    sweeper = asyncio.create_task(duel.sweeper(bot))
    logger_task = asyncio.create_task(gamelog.worker())
    try:
        await bot.set_my_commands(COMMANDS)
        me = await bot.get_me()
        log.info("started", username=me.username)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        sweeper.cancel()
        logger_task.cancel()
        await bot.session.close()
        await redis_service.close()
        mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
