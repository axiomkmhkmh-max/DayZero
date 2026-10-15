"""🎯 سرجایزه (Bounty): روی سر هر کسی پول بذار؛ اولین نفری که موفق بهش حمله/هک/دزدی کنه، کل جایزه رو برمی‌داره.
دستورها: «سرجایزه 5m» (ریپلای یا @یوزر) · «سرجایزه» (لیست تحت‌تعقیب‌ها) · «باج» (طرفِ سرجایزه با پرداخت نصفش خودش رو نجات می‌ده)."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import structlog
from aiogram import F, Router
from aiogram.types import Message

import actions
import eco
import evt
from db import User
from redis_service import redis_service
from util import dur, esc, fa, mention, norm, parse_amount

router = Router()
log = structlog.get_logger("bounty")

MIN_BOUNTY = 100_000
FEE = 0.10                      # ۱۰٪ مبلغی که می‌ذاری می‌ره تو خزانه‌ی سرقت؛ ۹۰٪ می‌شه جایزه
RANSOM = 0.50                   # باج = ۵۰٪ جایزه (بین سرجایزه‌گذارها تقسیم می‌شه)
LIFE = 7 * 86400                # جایزه‌ای که کسی برنداره بعد ۷ روز به صاحب‌هاش برمی‌گرده
KEY = "bn:{uid}"
BOARD = "bn:board"
DUE = "bn:due"

_CMD = re.compile(r"^(سر\s?جایزه|bounty)(?:\s+(.*))?$", re.I | re.S)
_BOARD_WORDS = {norm(x) for x in ("سرجایزه ها", "سرجایزهها", "تحت تعقیب", "تحت‌تعقیب", "تحت تعقیب ها", "wanted")}
_RANSOM_WORDS = {norm(x) for x in ("باج", "باج بده", "پرداخت باج", "ransom")}
# اگه متن نتیجه‌ی اکشن این نشونه رو داشت یعنی موفق بوده
_WIN_MARK = {"attack": "حمله موفق", "hack": "هک موفق", "vthief": "زد به گاوصندوق", "htheft": "دزدی موفق"}


def _key(uid: int) -> str:
    return KEY.format(uid=uid)


async def _save(bn: dict) -> None:
    await evt.jsave(_key(bn["target"]), bn, LIFE + 86400)
    await redis_service.r.zadd(BOARD, {str(bn["target"]): bn["total"]})
    await redis_service.r.zadd(DUE, {str(bn["target"]): bn["exp"]})


async def _drop(target: int) -> None:
    await redis_service.r.delete(_key(target))
    await redis_service.r.zrem(BOARD, str(target))
    await redis_service.r.zrem(DUE, str(target))


# ───────────────────────── دستورها ─────────────────────────
def _is_cmd(m: Message) -> bool:
    if not m.text:
        return False
    t = m.text.strip()
    return bool(_CMD.match(t)) or norm(t) in _BOARD_WORDS or norm(t) in _RANSOM_WORDS


@router.message(F.func(_is_cmd))
async def cmd(m: Message, user: User = None) -> None:
    if user is None:                                    # گروه: میان‌افزار کاربر نساخته
        return await evt.as_actor(m, _run)
    return await _run(m, user)


async def _run(m: Message, actor: User) -> None:
    t = m.text.strip()
    if norm(t) in _RANSOM_WORDS:
        return await _ransom(m, actor)
    mt = _CMD.match(t)
    rest = (mt.group(2) or "").strip() if mt else ""
    if norm(t) in _BOARD_WORDS or not rest:
        return await _board(m, actor)
    return await _place(m, actor, rest)


async def _target_of(m: Message, tokens: list[str]) -> Optional[User]:
    r = m.reply_to_message
    if r and r.from_user and not r.from_user.is_bot:
        return await eco.user_from_seen(r.from_user.id) or (await eco.ensure_user(r.from_user))[0]
    for tk in tokens:
        if tk.startswith("@"):
            return await eco.resolve_username(tk)
    return None


async def _place(m: Message, actor: User, rest: str) -> None:
    tokens = rest.split()
    target = await _target_of(m, tokens)
    if target is None:
        if not any(parse_amount(tk, 0) for tk in tokens):      # یه جمله‌ی عادی که با «سرجایزه» شروع شده؛ ساکت می‌مونیم
            return
        return await m.reply("🎯 روی پیام طرف ریپلای کن و بنویس <code>سرجایزه 5m</code>\n(یا: <code>سرجایزه @یوزر 5m</code>)")
    if target.tg_id == actor.tg_id:
        return await m.reply("😅 روی سر خودت که جایزه نمی‌ذارن!")
    amount = next((a for a in (parse_amount(tk, actor.cash) for tk in tokens if not tk.startswith("@")) if a), None)
    if not amount or amount < MIN_BOUNTY:
        return await m.reply(f"💵 حداقل سرجایزه {fa(MIN_BOUNTY)}ـه. مثال: <code>سرجایزه 5m</code>")
    if not await eco.spend(actor.tg_id, amount):
        return await m.reply("💸 پول کافی تو جیبت نیست!")
    fee = int(amount * FEE)
    prize = amount - fee
    await evt.pot_add(fee)
    async with redis_service.lock(f"bn:lk:{target.tg_id}", ttl=10, wait=6):
        bn = await evt.jload(_key(target.tg_id)) or {"target": target.tg_id, "t_name": target.name, "total": 0, "funders": {}, "names": {}}
        bn["t_name"] = target.name
        bn["total"] += prize
        bn["funders"][str(actor.tg_id)] = bn["funders"].get(str(actor.tg_id), 0) + prize
        bn["names"][str(actor.tg_id)] = actor.name
        bn["exp"] = time.time() + LIFE
        await _save(bn)
    text = (f"🎯 <b>سرجایزه!</b>\n{mention(actor.tg_id, actor.name)} روی سر {mention(target.tg_id, target.name)} "
            f"<b>{fa(prize)}</b> گذاشت.\n💰 جایزه‌ی کل: <b>{fa(bn['total'])}</b>\n\n"
            "هر کس اولین حمله/هک/دزدیِ موفق رو بزنه، کل جایزه مال اونه! 🔫")
    await m.reply(text)
    try:
        await m.bot.send_message(target.tg_id, f"🎯 {esc(actor.name)} روی سرت <b>{fa(prize)}</b> جایزه گذاشت! مجموع: <b>{fa(bn['total'])}</b>\n"
                                               f"می‌تونی با «باج» نصفش رو بدی و خودت رو نجات بدی ({fa(int(bn['total'] * RANSOM))}).")
    except Exception:  # noqa: BLE001
        pass


async def _board(m: Message, actor: User) -> None:
    rows = await redis_service.r.zrevrange(BOARD, 0, 9, withscores=True)
    lines = []
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
    for i, (uid, score) in enumerate(rows):
        bn = await evt.jload(_key(int(uid)))
        if not bn:
            continue
        left = dur(max(1, bn["exp"] - time.time()))
        lines.append(f"{medals[i]} {mention(int(uid), bn['t_name'])} — <b>{fa(bn['total'])}</b> · ⏳ {left}")
    mine = await evt.jload(_key(actor.tg_id))
    text = "🎯 <b>تحت‌تعقیب‌ترین‌ها</b>\n\n" + ("\n".join(lines) if lines else "<i>الان کسی سرجایزه نداره. اولیش رو تو بذار!</i>")
    if mine:
        text += f"\n\n⚠️ <b>روی سر خودت</b> {fa(mine['total'])} جایزه‌ست! با «باج» می‌تونی نصفش رو بدی و نجات پیدا کنی."
    text += "\n\n<i>ثبت: روی پیام طرف ریپلای کن و بنویس «سرجایزه 5m» (کارمزد ۱۰٪)</i>"
    await m.reply(text) if m.chat.type != "private" else await m.answer(text)


async def _ransom(m: Message, actor: User) -> None:
    async with redis_service.lock(f"bn:lk:{actor.tg_id}", ttl=10, wait=6):
        bn = await evt.jload(_key(actor.tg_id))
        if not bn:
            return await m.reply("😌 روی سرت جایزه‌ای نیست.")
        pay = int(bn["total"] * RANSOM)
        if not await eco.spend(actor.tg_id, pay):
            return await m.reply(f"💸 برای باج باید {fa(pay)} تو جیبت باشه.")
        total = bn["total"]
        for uid, amt in bn["funders"].items():                  # باج بین سرجایزه‌گذارها به نسبت سهمشون تقسیم می‌شه
            await eco.gain(int(uid), pay * amt // total)
        await _drop(actor.tg_id)
    await m.reply(f"🕊 {mention(actor.tg_id, actor.name)} با <b>{fa(pay)}</b> باج، سرجایزه‌ش (<b>{fa(total)}</b>) رو خرید و نجات پیدا کرد!")


# ───────────────────────── برداشتن جایزه ─────────────────────────
async def claim(bot, hunter_id: int, hunter_name: str, target_id: int) -> str:
    async with redis_service.lock(f"bn:lk:{target_id}", ttl=10, wait=6):
        bn = await evt.jload(_key(target_id))
        if not bn:
            return ""
        if str(hunter_id) in bn["funders"]:
            return "🎯 روی سر این آدم سرجایزه‌ی خودت هم هست؛ خودت نمی‌تونی برداریش!"
        await _drop(target_id)
    await eco.gain(hunter_id, bn["total"])
    await eco.add_xp(hunter_id, 50)
    await eco.update_user(hunter_id, inc={"stats.bounty_w": 1, "stats.bounty_sum": bn["total"]})
    return (f"🎯💰 <b>سرجایزه برداشته شد!</b> {mention(hunter_id, hunter_name)} جایزه‌ی سر "
            f"{mention(target_id, bn['t_name'])} یعنی <b>{fa(bn['total'])}</b> دلار رو برد! 🏆")


_orig_run = actions.run


async def _run_with_bounty(bot, action, actor, target, chat_id, in_group=False, extra=""):
    text = await _orig_run(bot, action, actor, target, chat_id, in_group, extra)
    try:
        mark = _WIN_MARK.get(action)
        if mark and isinstance(text, str) and mark in text:
            more = await claim(bot, actor.tg_id, actor.name, target.tg_id)
            if more:
                text += "\n\n" + more
    except Exception:  # noqa: BLE001
        log.exception("bounty_claim_error")
    return text


if not getattr(actions.run, "_bounty", False):
    _run_with_bounty._bounty = True                    # type: ignore[attr-defined]
    actions.run = _run_with_bounty                     # همه‌ی مسیرها (گروه/پیوی/دکمه) از actions.run رد می‌شن


# ───────────────────────── انقضا ─────────────────────────
async def sweeper(bot) -> None:
    r = redis_service.r
    while True:
        try:
            await asyncio.sleep(300)
            for uid in await r.zrangebyscore(DUE, 0, time.time()):
                async with redis_service.lock(f"bn:lk:{uid}", ttl=20, wait=10):
                    bn = await evt.jload(_key(int(uid)))
                    if not bn or bn["exp"] > time.time():
                        if not bn:
                            await r.zrem(DUE, uid)
                        continue
                    await _drop(int(uid))
                for fid, amt in bn["funders"].items():
                    await eco.gain(int(fid), amt)
                    try:
                        await bot.send_message(int(fid), f"⌛ سرجایزه‌ی روی {esc(bn['t_name'])} کسی برنداشت؛ <b>{fa(amt)}</b> برگشت به جیبت.")
                    except Exception:  # noqa: BLE001
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("bounty_sweeper_error")
