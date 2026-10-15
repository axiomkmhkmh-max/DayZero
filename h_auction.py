"""🔨 مزایده‌ی زنده: یه آیتم نادر دارک‌وب، پیشنهاد با دکمه یا ریپلای، ضدآخرِ‌وقت‌زنی (اسنایپ).
ادمین گروه با «مزایده فعال» هر چند ساعت یه مزایده‌ی خودکار می‌گیره."""
from __future__ import annotations

import asyncio
import time

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

import eco
import evt
import rules
from db import User
from redis_service import redis_service
from ui import b, kb, replying_to_bot
from util import esc, fa, mention, norm, parse_amount

router = Router()
log = structlog.get_logger("auction")

DURATION = 120                  # ثانیه
SNIPE_WINDOW = 20               # پیشنهاد تو ۲۰ ثانیه‌ی آخر، ۲۰ ثانیه تمدید می‌کنه
SNIPE_MAX = 5
START_FRAC = 0.35               # قیمت پایه = ۳۵٪ قیمت آیتم
MIN_INC = 100_000
INC_FRAC = 0.05                 # حداقل افزایش = ۵٪ بالاترین پیشنهاد
POT_SHARE = 0.30                # ۳۰٪ پول مزایده می‌ره تو خزانه‌ی سرقت؛ بقیه می‌سوزه
MANUAL_CD = 1800                # مزایده‌ی دستی هر گروه هر ۳۰ دقیقه
AUTO_EVERY = 4 * 3600
KEY = "au:{chat}:{msg}"
ACTIVE = "au:active:{chat}"
DUE = "au:due"
AUTO_SET = "au:auto"
TTL = 3600

ALIASES = {norm(a): "start" for a in ("مزایده", "مزایده بزن", "auction")}
ALIASES.update({norm("مزایده فعال"): "on", norm("مزایده غیرفعال"): "off"})


def _key(chat: int, msg: int) -> str:
    return KEY.format(chat=chat, msg=msg)


def _min_next(a: dict) -> int:
    if not a["top_uid"]:
        return a["start"]
    return a["top_bid"] + max(MIN_INC, int(a["top_bid"] * INC_FRAC))


def _text(a: dict, final: str = "") -> str:
    left = max(0, int(a["end"] - time.time()))
    top = (f"👑 بالاترین: {mention(a['top_uid'], a['top_name'])} با <b>{fa(a['top_bid'])}</b>"
           if a["top_uid"] else "🕳 هنوز پیشنهادی نیست.")
    t = (f"🔨 <b>مزایده‌ی زنده</b>\n\n{a['emoji']} <b>{esc(a['name'])}</b>\n<i>{esc(a['desc'])}</i>\n\n"
         f"💵 قیمت پایه: <b>{fa(a['start'])}</b>\n{top}\n")
    if final:
        return t + f"\n{final}"
    return (t + f"\n⏳ <b>{fa(left)}</b> ثانیه مونده · پیشنهاد بعدی حداقل <b>{fa(_min_next(a))}</b>\n"
            "↩️ یا روی همین پیام ریپلای کن و مبلغ بنویس (مثلاً <code>12m</code>)")


def _kb(a: dict):
    n = _min_next(a)
    return kb([b(f"➕ {fa(n)}", "au:b:min"), b("➕ ۱۰٪", "au:b:10"), b("➕ ۵۰٪", "au:b:50")])


async def _edit(bot, chat: int, msg: int, text: str, markup=None) -> None:
    try:
        await bot.edit_message_text(text, chat_id=chat, message_id=msg, reply_markup=markup)
    except Exception:  # noqa: BLE001
        pass


# ───────────────────────── شروع ─────────────────────────
async def launch(bot, chat: int, manual: bool = True) -> str:
    """مزایده می‌سازه. خروجی: متن خطا (یا خالی اگه شروع شد)."""
    r = redis_service.r
    if not await r.set(ACTIVE.format(chat=chat), "1", nx=True, ex=TTL):
        return "🔨 یه مزایده همین الان بازه."
    if manual and not await r.set(f"au:cd:{chat}", "1", nx=True, ex=MANUAL_CD):
        await r.delete(ACTIVE.format(chat=chat))
        ttl = await r.ttl(f"au:cd:{chat}")
        return f"🕒 مزایده‌ی بعدی این گروه {fa(max(1, ttl) // 60 + 1)} دقیقه‌ی دیگه‌ست."
    it = rules.RNG.choice(list(rules.DARK_INDEX.values()))
    a = {"item": it["key"], "emoji": it["emoji"], "name": it["name"], "desc": it["desc"],
         "start": max(MIN_INC, int(it["price"] * START_FRAC)), "top_uid": 0, "top_name": "", "top_bid": 0,
         "end": time.time() + DURATION, "ext": 0}
    sent = await bot.send_message(chat, _text(a), reply_markup=_kb(a))
    await evt.jsave(_key(chat, sent.message_id), a, TTL)
    await redis_service.r.set(ACTIVE.format(chat=chat), str(sent.message_id), ex=TTL)
    await r.zadd(DUE, {f"{chat}:{sent.message_id}": a["end"]})
    await r.set(f"au:last:{chat}", str(int(time.time())), ex=7 * 86400)
    return ""


def _is_cmd(m: Message) -> bool:
    return bool(m.text) and norm(m.text) in ALIASES


@router.message(F.func(_is_cmd))
async def cmd(m: Message) -> None:
    if m.chat.type == "private":
        return await m.answer("🔨 مزایده‌ی گروهیه؛ ربات رو ببر توی گروه.")
    what = ALIASES[norm(m.text)]
    if what in ("on", "off"):
        if not await evt.is_chat_admin(m.bot, m.chat.id, m.from_user.id):
            return await m.reply("🚫 فقط ادمین‌های گروه می‌تونن این رو تنظیم کنن.")
        if what == "on":
            await redis_service.r.sadd(AUTO_SET, m.chat.id)
            return await m.reply(f"✅ مزایده‌ی خودکار روشن شد؛ هر حدود {fa(AUTO_EVERY // 3600)} ساعت یه آیتم نادر میاد.")
        await redis_service.r.srem(AUTO_SET, m.chat.id)
        return await m.reply("🔕 مزایده‌ی خودکار خاموش شد.")
    err = await launch(m.bot, m.chat.id, manual=True)
    if err:
        await m.reply(err)


# ───────────────────────── پیشنهاد ─────────────────────────
async def _bid(bot, chat: int, msg: int, user: User, amount: int) -> str:
    """پیشنهاد می‌ده؛ خروجی: متن خطا یا ''."""
    async with redis_service.lock(f"au:lk:{chat}:{msg}", ttl=15, wait=8):
        a = await evt.jload(_key(chat, msg))
        if not a or time.time() >= a["end"]:
            return "⌛ این مزایده تموم شده."
        if a["top_uid"] == user.tg_id:
            return "👑 تو همین الان بالاترین پیشنهادی!"
        if amount < _min_next(a):
            return f"💵 پیشنهاد باید حداقل {fa(_min_next(a))} باشه."
        if not await eco.spend(user.tg_id, amount):
            return "💸 پول کافی تو جیبت نیست!"
        if a["top_uid"]:
            await eco.gain(a["top_uid"], a["top_bid"])           # پول نفر قبلی برمی‌گرده
            try:
                await bot.send_message(chat, f"⚠️ {mention(a['top_uid'], a['top_name'])}، {esc(user.name)} پیشنهادت رو رد کرد!",
                                       reply_to_message_id=msg, allow_sending_without_reply=True)
            except Exception:  # noqa: BLE001
                pass
        a["top_uid"], a["top_name"], a["top_bid"] = user.tg_id, user.name, amount
        if a["end"] - time.time() < SNIPE_WINDOW and a["ext"] < SNIPE_MAX:
            a["end"] += SNIPE_WINDOW
            a["ext"] += 1
            await redis_service.r.zadd(DUE, {f"{chat}:{msg}": a["end"]})
        await evt.jsave(_key(chat, msg), a, TTL)
        await _edit(bot, chat, msg, _text(a), _kb(a))
        return ""


@router.callback_query(F.data.startswith("au:b:"))
async def cb_bid(cb: CallbackQuery, user: User) -> None:
    chat, msg = cb.message.chat.id, cb.message.message_id
    a = await evt.jload(_key(chat, msg))
    if not a:
        return await cb.answer("⌛ این مزایده تموم شده.", show_alert=True)
    mode = cb.data.split(":")[2]
    base = a["top_bid"] or a["start"]
    amount = _min_next(a) if mode == "min" else max(_min_next(a), int(base * (1 + int(mode) / 100)))
    err = await _bid(cb.bot, chat, msg, user, amount)
    await cb.answer(err or f"✅ پیشنهادت {fa(amount)} ثبت شد!", show_alert=bool(err))


async def _is_bid_reply(m: Message) -> bool:
    if not m.text or not m.reply_to_message or not replying_to_bot(m) or m.chat.type == "private":
        return False
    tok = m.text.strip().split()
    if len(tok) != 1 or parse_amount(tok[0], 0) is None:
        return False
    return bool(await redis_service.r.exists(_key(m.chat.id, m.reply_to_message.message_id)))


@router.message(_is_bid_reply)
async def reply_bid(m: Message) -> None:
    async def go(msg: Message, actor: User) -> None:
        amount = parse_amount(msg.text.strip().split()[0], actor.cash)
        err = await _bid(msg.bot, msg.chat.id, msg.reply_to_message.message_id, actor, amount or 0)
        await msg.reply(err or f"✅ پیشنهادت <b>{fa(amount)}</b> ثبت شد!")
    await evt.as_actor(m, go)


# ───────────────────────── پایان و زمان‌بندی ─────────────────────────
async def _finish(bot, chat: int, msg: int) -> None:
    async with redis_service.lock(f"au:lk:{chat}:{msg}", ttl=20, wait=10):
        a = await evt.jload(_key(chat, msg))
        if not a:
            return
        if time.time() < a["end"]:                              # تمدید شده (اسنایپ)؛ دوباره صف
            return await redis_service.r.zadd(DUE, {f"{chat}:{msg}": a["end"]})
        await redis_service.r.delete(_key(chat, msg), ACTIVE.format(chat=chat))
        if a["top_uid"]:
            await eco.update_user(a["top_uid"], inc={f"items.{a['item']}": 1})
            await evt.pot_add(int(a["top_bid"] * POT_SHARE))
            final = (f"🏆 <b>فروخته شد!</b> {mention(a['top_uid'], a['top_name'])} با <b>{fa(a['top_bid'])}</b> "
                     f"برنده شد و {a['emoji']} {esc(a['name'])} رفت تو کیفش.")
        else:
            final = "🕳 کسی پیشنهاد نداد؛ آیتم بی‌خریدار موند."
        await _edit(bot, chat, msg, _text(a, final))


async def sweeper(bot) -> None:
    r = redis_service.r
    tick = 0
    while True:
        try:
            await asyncio.sleep(2)
            for member in await r.zrangebyscore(DUE, 0, time.time()):
                if not await r.zrem(DUE, member):
                    continue
                chat, msg = (int(x) for x in member.split(":"))
                try:
                    await _finish(bot, chat, msg)
                except Exception:  # noqa: BLE001
                    log.exception("auction_finish_error", member=member)
                    await r.zadd(DUE, {member: time.time() + 15})
            tick += 1
            if tick % 30 == 0:                                   # هر ~۶۰ ثانیه: مزایده‌های خودکار
                for chat in await r.smembers(AUTO_SET):
                    chat = int(chat)
                    last = int(await r.get(f"au:last:{chat}") or 0)
                    if time.time() - last < AUTO_EVERY or await r.exists(ACTIVE.format(chat=chat)):
                        continue
                    try:
                        await launch(bot, chat, manual=False)
                    except Exception as e:  # noqa: BLE001
                        log.warning("auction_auto_failed", chat=chat, error=repr(e))
                        await r.set(f"au:last:{chat}", str(int(time.time())), ex=7 * 86400)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("auction_sweeper_error")
