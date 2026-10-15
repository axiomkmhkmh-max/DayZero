"""👀 شرط تماشاچی‌ها: روی پیامِ بازیِ یکی دیگه ریپلای کن و بنویس «برد 50k» یا «باخت 50k».
شرط‌بندی توده‌ایه (پول بازنده‌ها بین برنده‌ها تقسیم می‌شه؛ ۵٪ کارمزد می‌ره خزانه‌ی سرقت)."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import structlog
from aiogram import F, Router
from aiogram.types import Message

import eco
import evt
from db import User
from redis_service import redis_service
from ui import replying_to_bot
from util import esc, fa, mention, norm, parse_amount

router = Router()
log = structlog.get_logger("spectate")

MIN_BET = 1_000
MAX_BET = 5_000_000
RAKE = 0.05
KEY = "bt:{chat}:{msg}"
DUE = "bt:due"

SLOT_SIDES = {"win": ("برد", "ببره", "میبره", "win"), "lose": ("باخت", "ببازه", "میبازه", "lose")}
SLOT_LABELS = {"win": "برد", "lose": "باخت"}
DUEL_SIDES = {"p1": ("اول", "1", "یک", "p1"), "p2": ("دوم", "2", "دو", "p2")}


def _key(chat: int, msg: int) -> str:
    return KEY.format(chat=chat, msg=msg)


async def open_book(chat: int, msg: int, sides: dict[str, tuple], labels: dict[str, str], subjects: list[int], ttl: int) -> None:
    """دفترِ شرط روی پیام. اگه از قبل بازه شرط‌ها دست‌نخورده می‌مونن و فقط TTL تمدید می‌شه."""
    k = _key(chat, msg)
    async with redis_service.lock(f"bt:lk:{chat}:{msg}", ttl=10, wait=5):
        bk = await evt.jload(k) or {"bets": {}}
        bk.update({"sides": {s: list(a) for s, a in sides.items()}, "labels": labels, "subjects": subjects})
        await evt.jsave(k, bk, ttl + 600)
    await redis_service.r.zadd(DUE, {f"{chat}:{msg}": time.time() + ttl})


async def close(chat: int, msg: int) -> None:
    """شرط‌بندی بسته می‌شه (بازی شروع شد)؛ تسویه بعداً با settle."""
    try:
        async with redis_service.lock(f"bt:lk:{chat}:{msg}", ttl=10, wait=5):
            bk = await evt.jload(_key(chat, msg))
            if bk:
                bk["closed"] = True
                await evt.jsave(_key(chat, msg), bk, 3600)
    except Exception:  # noqa: BLE001
        pass


async def settle(bot, chat: int, msg: int, outcome: Optional[str]) -> None:
    """outcome = کلید طرف برنده؛ None = مساوی/لغو (همه پولشون برمی‌گرده). هیچ‌وقت کار اصلی رو خراب نمی‌کنه."""
    try:
        async with redis_service.lock(f"bt:lk:{chat}:{msg}", ttl=20, wait=10):
            k = _key(chat, msg)
            bk = await evt.jload(k)
            if not bk:
                return
            await redis_service.r.delete(k)
            await redis_service.r.zrem(DUE, f"{chat}:{msg}")
            bets: dict = bk["bets"]
            if not bets:
                return
            total = sum(v["amt"] for v in bets.values())
            win_sum = sum(v["amt"] for v in bets.values() if v["side"] == outcome)
            if outcome is None or win_sum == 0 or win_sum == total:
                for uid, v in bets.items():
                    await eco.gain(int(uid), v["amt"])
                text = "👀 <b>شرط‌های تماشاچی‌ها</b>: " + ("مساوی/لغو شد" if outcome is None else "طرف مقابل شرطی نداشت") + " — پول همه برگشت."
            else:
                rake = int(total * RAKE)
                pool = total - rake
                await evt.pot_add(rake)
                lines = []
                for uid, v in bets.items():
                    if v["side"] != outcome:
                        continue
                    pay = pool * v["amt"] // win_sum
                    await eco.gain(int(uid), pay)
                    lines.append(f"✅ {mention(int(uid), v['name'])}: {fa(v['amt'])} → <b>{fa(pay)}</b>")
                text = (f"👀 <b>نتیجه‌ی شرط تماشاچی‌ها</b> — برنده: <b>{bk['labels'].get(outcome, outcome)}</b>\n"
                        f"💰 کل شرط‌ها: {fa(total)} (کارمزد {fa(rake)})\n" + "\n".join(lines[:15]))
            await bot.send_message(chat, text, reply_to_message_id=msg, allow_sending_without_reply=True)
    except Exception:  # noqa: BLE001
        log.exception("spectate_settle_error", chat=chat, msg=msg)


# ───────────────────────── ثبت شرط (ریپلای) ─────────────────────────
def _parse(text: str, bk: dict) -> Optional[tuple[str, str]]:
    tok = text.strip().split()
    if len(tok) != 2:
        return None
    a = norm(tok[0])
    for side, aliases in bk["sides"].items():
        if a in {norm(x) for x in aliases}:
            return side, tok[1]
    return None


async def _is_bet(m: Message) -> bool:
    if not m.text or m.chat.type == "private" or not m.reply_to_message or not replying_to_bot(m):
        return False
    bk = await evt.jload(_key(m.chat.id, m.reply_to_message.message_id))
    return bool(bk) and not bk.get("closed") and _parse(m.text, bk) is not None


@router.message(_is_bet)
async def place(m: Message) -> None:
    async def go(msg: Message, actor: User) -> None:
        chat, mid = msg.chat.id, msg.reply_to_message.message_id
        async with redis_service.lock(f"bt:lk:{chat}:{mid}", ttl=10, wait=6):
            bk = await evt.jload(_key(chat, mid))
            if not bk or bk.get("closed"):
                return await msg.reply("⌛ شرط‌بندی روی این بازی بسته شده.")
            side, tok = _parse(msg.text, bk)
            if actor.tg_id in bk["subjects"]:
                return await msg.reply("🙅 خودت بازیکنی! نمی‌تونی روی بازی خودت شرط ببندی.")
            amount = parse_amount(tok, min(actor.cash, MAX_BET))
            if not amount or amount < MIN_BET:
                return await msg.reply(f"💵 مبلغ شرط حداقل {fa(MIN_BET)} (مثلاً <code>برد 50k</code>).")
            old = bk["bets"].get(str(actor.tg_id))
            if old and old["side"] != side:
                return await msg.reply("🔒 قبلاً روی طرف دیگه شرط بستی؛ نمی‌تونی عوضش کنی.")
            if (old["amt"] if old else 0) + amount > MAX_BET:
                return await msg.reply(f"🚧 سقف مجموع شرط هر نفر روی یه بازی {fa(MAX_BET)}ـه.")
            if not await eco.spend(actor.tg_id, amount):
                return await msg.reply("💸 پول کافی تو جیبت نیست!")
            bk["bets"][str(actor.tg_id)] = {"side": side, "amt": (old["amt"] if old else 0) + amount, "name": actor.name}
            await evt.jsave(_key(chat, mid), bk, 3600)
            tot = {s: sum(v["amt"] for v in bk["bets"].values() if v["side"] == s) for s in bk["sides"]}
            pools = " · ".join(f"{bk['labels'][s]}: {fa(tot[s])}" for s in bk["sides"])
        await msg.reply(f"👀 شرطت روی <b>{bk['labels'][side]}</b> ثبت شد: <b>{fa(amount)}</b>\n📊 {pools}")
    await evt.as_actor(m, go)


async def sweeper(bot) -> None:
    """شرط‌هایی که بازی‌شون هیچ‌وقت تموم نشد (انقضا/لغو/ری‌استارت) پولشون برمی‌گرده."""
    r = redis_service.r
    while True:
        try:
            await asyncio.sleep(15)
            for member in await r.zrangebyscore(DUE, 0, time.time()):
                chat, msg = (int(x) for x in member.split(":"))
                await settle(bot, chat, msg, None)
                await r.zrem(DUE, member)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("spectate_sweeper_error")
