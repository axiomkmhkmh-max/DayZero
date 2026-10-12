"""دستورهای متنی (گروه و پیوی): حمله، دوز، تاس، ثبت ازدواج، درخواست طلاق، بچه‌دار شدن، جن، دزد، طلسم‌ها …
روی پیام طرف ریپلای کن یا @یوزرنیمش رو بنویس."""
from __future__ import annotations

import re
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

import actions
import combat
import config as C
import duel
import eco
import h_menu
import rules
from db import User
from redis_service import redis_service
from ui import kb, url_b
from util import esc, fa, norm, parse_amount

router = Router()

# alias (نرمال‌شده) → (دستور, فقط-وقتی-تنهاست)
_RAW = {
    "attack": (["حمله", "attack"], False),
    "ttt": (["دوز", "ttt"], False),
    "dice": (["تاس", "تاسبازی", "dice"], False),
    "marry": (["ثبت ازدواج", "ازدواج", "marry"], False),
    "divorce": (["درخواست طلاق", "طلاق", "divorce"], True),
    "baby": (["بچه دار شدن", "بچه دار شو", "بچه دار", "بچه", "baby"], True),
    "jinn": (["جن", "jinn"], False),
    "vthief": (["دزد گاوصندوق", "دزد", "thief"], False),
    "love": (["طلسم عاشقی", "love"], False),
    "split": (["طلسم طلاق", "split"], False),
    "wallet": (["جیب", "موجودی", "کیف", "me", "wallet"], True),
    "menu": (["منو", "منوی بازی"], True),
}
ALIASES = {norm(a): (cmd, solo) for cmd, (als, solo) in _RAW.items() for a in als}
_BOT_RE = re.compile(rf"(?i)@{re.escape(C.BOT_USERNAME)}\b")

USAGE = {
    "attack": "⚔️ روی پیام طرف ریپلای کن و بنویس:\n<code>حمله</code> · <code>حمله کلاشینکف</code> · <code>حمله کلاشینکف و شمشیر</code>",
    "ttt": "❌⭕ روی پیام طرف ریپلای کن و بنویس: <code>دوز 50000</code>",
    "dice": "🎲 روی پیام طرف ریپلای کن و بنویس: <code>تاس 50000</code>",
    "marry": "💍 روی پیام طرف ریپلای کن و بنویس: <code>ثبت ازدواج</code>",
    "jinn": "👹 روی پیام طرف ریپلای کن و بنویس: <code>جن</code>",
    "vthief": "🥷 روی پیام طرف ریپلای کن و بنویس: <code>دزد</code>",
    "love": "💘 روی پیام طرف ریپلای کن و بنویس: <code>طلسم عاشقی</code>",
    "split": "💔 روی پیام یکی از زوج‌ها ریپلای کن و بنویس: <code>طلسم طلاق</code>",
}


def match_command(text: str) -> Optional[tuple[str, bool, list[str]]]:
    """(دستور, با-اسلش؟, توکن‌های باقی‌مانده) یا None."""
    text = _BOT_RE.sub("", text).strip()
    toks = text.split()
    if not toks:
        return None
    slash = toks[0].startswith("/")
    if slash:
        toks[0] = re.sub(r"@\w+$", "", toks[0])
    for n in (3, 2, 1):
        if len(toks) < n:
            continue
        hit = ALIASES.get(norm("".join(toks[:n])))
        if hit:
            cmd, solo = hit
            if solo and len(toks) != n:
                continue
            return cmd, slash, toks[n:]
    return None


async def find_target(m: Message) -> Optional[User]:
    r = m.reply_to_message
    if r and r.from_user and not r.from_user.is_bot and r.from_user.id != m.from_user.id:
        return (await eco.ensure_user(r.from_user))[0]
    for ent in m.entities or []:
        if ent.type == "text_mention" and ent.user and not ent.user.is_bot and ent.user.id != m.from_user.id:
            return (await eco.ensure_user(ent.user))[0]
        if ent.type == "mention":
            name = m.text[ent.offset + 1: ent.offset + ent.length]
            if name.lower() == C.BOT_USERNAME.lower() or name.lower() == (m.from_user.username or "").lower():
                continue
            t = await eco.find_by_username(name)
            if t:
                return t
    for tok in (m.text or "").split():
        if tok.startswith("#") and tok[1:].isdigit():
            t = await eco.get_user(int(tok[1:]))
            if t:
                return t
    return None


async def process(m: Message, cmd: str, slash: bool, rest: list[str], actor: User, in_group: bool) -> None:
    reply = m.reply if in_group else m.answer

    if cmd == "menu":
        return await h_menu.open_menu(m, actor)
    if cmd == "wallet":
        atk, _ = rules.attack_power(actor.gear, actor.forces)
        dfn, _, _ = rules.defense_power(actor.gear, actor.forces)
        return await reply(f"👤 <b>{esc(actor.name)}</b> جان، اینم وضعیتت:\n"
                           f"💵 جیب: <b>{fa(actor.cash)}</b>   🔐 گاوصندوق: <b>{fa(actor.vault)}</b>\n"
                           f"👶 بچه: {fa(actor.kids)}   ⚔️ حمله: {fa(atk)}   🛡 دفاع: {fa(dfn)}")
    if cmd == "baby":
        ok, text = await actions.baby(actor.tg_id)
        return await reply(text)
    if cmd == "divorce":
        res = await actions.divorce_request(m.bot, actor, m.chat.id, in_group)
        if res:
            await reply(res)
        return

    target = await find_target(m)
    if target is None:
        if slash or not in_group:           # توی گروه برای پیام‌های معمولی ساکت می‌مونیم
            await reply(USAGE.get(cmd, "🎯 روی پیام طرف ریپلای کن یا @یوزرنیمش رو بنویس."))
        return

    if cmd in ("ttt", "dice"):
        amount = None
        for tok in rest:
            if tok.startswith(("@", "#")):
                continue
            amount = parse_amount(tok, actor.cash)
            if amount:
                break
        if not amount:
            return await reply(USAGE[cmd] + f"\n(مبلغ بین {fa(C.MIN_BET)} و {fa(C.MAX_BET)})")
        err = await duel.create(m.bot, cmd, actor, await eco.get_user(target.tg_id), amount, m.chat.id, in_group)
        if err:
            await reply(err)
        return

    extra = " ".join(t for t in rest if not t.startswith(("@", "#")))
    action = {"attack": "attack", "marry": "marry", "jinn": "jinn", "vthief": "vthief", "love": "love", "split": "split"}[cmd]
    text = await actions.run(m.bot, action, actor, target, m.chat.id, in_group, extra)
    if text:
        await reply(text)


@router.message(F.text, StateFilter(None))
async def on_text(m: Message, user: User = None) -> None:
    if m.from_user is None or m.from_user.is_bot or not m.text:
        return
    hit = match_command(m.text)
    if hit is None:
        return
    cmd, slash, rest = hit
    if m.chat.type == "private":                       # پیوی: میان‌افزار قبلاً قفل و کاربر رو آماده کرده
        return await process(m, cmd, slash, rest, user, False)
    if not await redis_service.r.set(f"gthr:{m.from_user.id}", "1", nx=True, px=900):
        return
    async with redis_service.lock(f"u:{m.from_user.id}", ttl=20, wait=6):
        actor, _ = await eco.ensure_user(m.from_user)
        await process(m, cmd, slash, rest, actor, True)
