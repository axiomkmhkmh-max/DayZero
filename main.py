"""Day Zero — نقطه‌ی شروع ربات."""
from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand

import config
import duel
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
import h_market
import h_menu
import h_social
import h_target

log = structlog.get_logger("main")

COMMANDS = [
    BotCommand(command="menu", description="🌅 منوی اصلی"),
    BotCommand(command="me", description="💵 موجودی و قدرت"),
    BotCommand(command="attack", description="⚔️ حمله (ریپلای + اسلحه)"),
    BotCommand(command="ttt", description="❌⭕ دوز با شرط (ریپلای + مبلغ)"),
    BotCommand(command="dice", description="🎲 تاس با شرط (ریپلای + مبلغ)"),
    BotCommand(command="marry", description="💍 ثبت ازدواج (ریپلای)"),
    BotCommand(command="divorce", description="💔 درخواست طلاق"),
    BotCommand(command="baby", description="👶 بچه‌دار شدن"),
]


async def main() -> None:
    config.validate()
    setup_logging(config.LOG_LEVEL)
    await redis_service.connect(config.REDIS_URL)
    mongo = await init_db()

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=RedisStorage(redis_service.r))

    mw = GameMiddleware()
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)

    # ترتیب مهمه: حالت‌های FSM (گرفتن هدف/اسم) قبل از دستورهای متنی
    for r in (h_target.router, h_alliance.router, h_menu.router, h_home.router, h_biz.router, h_market.router,
              h_casino.router, h_fight.router, h_social.router, duel.router, h_group.router):
        dp.include_router(r)

    duel.set_bot(bot)
    sweeper = asyncio.create_task(duel.sweeper(bot))
    try:
        await bot.set_my_commands(COMMANDS)
        me = await bot.get_me()
        log.info("started", username=me.username)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        sweeper.cancel()
        await bot.session.close()
        await redis_service.close()
        mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
