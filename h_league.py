"""🏆 لیگ هفتگی گروه‌ها: رشد ثروت اعضا (از شنبه ۰۰:۰۰ تهران) → رتبه‌ی گروه + جایزه‌ی نفرهای اول."""
from __future__ import annotations

import asyncio
import json

import structlog
from aiogram import BaseMiddleware, F, Router
from aiogram.types import Message

import eco
import evt
from redis_service import redis_service
from util import dur, esc, fa, mention, norm

router = Router()
log = structlog.get_logger("league")

MIN_MEMBERS = 5                 # گروهی که کمتر از ۵ نفر فعال باشه تو رتبه‌بندی/جایزه حساب نمی‌شه
TOP_N = 5                       # امتیاز گروه = مجموع رشد ۵ نفر اول
PLAYER_PRIZES = (2_000_000, 1_000_000, 500_000)         # ۳ نفر اول هر گروه
GROUP_PRIZES = (1_000_000, 500_000, 250_000)            # گروه‌های اول تا سوم: به هر کدوم از ۵ نفر اول
KEEP = 21 * 86400
ALIASES = {norm(a) for a in ("لیگ", "لیگ هفتگی", "league")}


def _kb(wk: str, chat: int) -> str:
    return f"lg:m:{wk}:{chat}"


# ───────────────────────── ردیابی (میان‌افزار) ─────────────────────────
class Tracker(BaseMiddleware):
    """هر کس تو گروه پیام بده تو لیگ این هفته‌ی اون گروه ثبت می‌شه (حداکثر هر ۱۰ دقیقه یک بار پردازش)."""

    async def __call__(self, handler, event, data):
        try:
            if isinstance(event, Message) and event.chat.type in ("group", "supergroup") \
                    and event.from_user and not event.from_user.is_bot:
                await self._track(event)
        except Exception:  # noqa: BLE001
            pass
        return await handler(event, data)

    @staticmethod
    async def _track(m: Message) -> None:
        r = redis_service.r
        uid, chat = m.from_user.id, m.chat.id
        if not await r.set(f"lg:w:{chat}:{uid}", "1", nx=True, ex=600):
            return
        wk = evt.week_id()
        await r.sadd(_kb(wk, chat), uid)
        await r.expire(_kb(wk, chat), KEEP)
        await r.hset("lg:chats", str(chat), m.chat.title or "گروه")
        if not await r.hexists(f"lg:b:{wk}", str(uid)):
            u = await eco.get_user(uid)
            if u is not None:
                await r.hset(f"lg:b:{wk}", str(uid), eco.net_worth(u))
                await r.expire(f"lg:b:{wk}", KEEP)


# ───────────────────────── محاسبه ─────────────────────────
async def _scores(wk: str) -> dict[int, dict]:
    """chat → {title, members:[(uid, name, growth)...] مرتب نزولی, score}"""
    r = redis_service.r
    chats = await r.hgetall("lg:chats")
    bases = {int(k): int(v) for k, v in (await r.hgetall(f"lg:b:{wk}")).items()}
    members = {int(c): [int(x) for x in await r.smembers(_kb(wk, int(c)))] for c in chats}
    ids = {u for lst in members.values() for u in lst if u in bases}
    users = {}
    if ids:
        async for d in eco.col().find({"tg_id": {"$in": list(ids)}}, projection={"tg_id": 1, "name": 1, "cash": 1, "vault": 1, "invested": 1, "bank": 1}):
            users[d["tg_id"]] = d
    out = {}
    for c, lst in members.items():
        rows = []
        for uid in lst:
            d = users.get(uid)
            if d is None:
                continue
            nw = int(d.get("cash", 0)) + int(d.get("vault", 0)) + int(d.get("invested", 0)) + int(d.get("bank", 0))
            rows.append((uid, d.get("name", "Player"), nw - bases[uid]))
        rows.sort(key=lambda x: x[2], reverse=True)
        out[c] = {"title": chats.get(str(c), "گروه"), "members": rows, "n": len(rows),
                  "score": sum(max(0, g) for _, _, g in rows[:TOP_N])}
    return out


def _ranked(scores: dict[int, dict]) -> list[tuple[int, dict]]:
    ok = [(c, s) for c, s in scores.items() if s["n"] >= MIN_MEMBERS and s["score"] > 0]
    return sorted(ok, key=lambda x: x[1]["score"], reverse=True)


# ───────────────────────── دستور «لیگ» ─────────────────────────
def _is_cmd(m: Message) -> bool:
    return bool(m.text) and norm(m.text) in ALIASES


@router.message(F.func(_is_cmd))
async def cmd(m: Message) -> None:
    wk = evt.week_id()
    scores = await _scores(wk)
    ranked = _ranked(scores)
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [f"{medals[i]} {esc(s['title'])} — <b>{fa(s['score'])}</b>" for i, (_, s) in enumerate(ranked[:5])] \
        or ["<i>هنوز گروهی با ≥۵ نفر فعال و رشد مثبت نداریم.</i>"]
    text = (f"🏆 <b>لیگ هفتگی گروه‌ها</b>\n⏳ پایان هفته: {dur(evt.week_left_sec())} دیگه\n\n" + "\n".join(lines))
    if m.chat.type != "private":
        mine = scores.get(m.chat.id)
        if mine:
            pos = next((i + 1 for i, (c, _) in enumerate(ranked) if c == m.chat.id), None)
            text += (f"\n\n📍 <b>گروه شما</b>: " + (f"رتبه‌ی {fa(pos)}" if pos else f"هنوز رتبه نداره (فعال: {fa(mine['n'])} از {fa(MIN_MEMBERS)}، یا رشد مثبت لازمه)")
                     + f" · امتیاز {fa(mine['score'])}\n")
            for i, (uid, name, g) in enumerate(mine["members"][:3]):
                text += f"{medals[i]} {mention(uid, name)}: <b>{'+' if g >= 0 else ''}{fa(g)}</b>\n"
    text += (f"\n🎁 <b>جایزه‌ها</b>: ۳ نفر اول هر گروه {fa(PLAYER_PRIZES[0])} / {fa(PLAYER_PRIZES[1])} / {fa(PLAYER_PRIZES[2])}"
             f" · ۵ نفر اول گروه‌های ۱ تا ۳: {fa(GROUP_PRIZES[0])} / {fa(GROUP_PRIZES[1])} / {fa(GROUP_PRIZES[2])} هر کدوم\n"
             f"<i>امتیاز = رشد ثروت (جیب+بانک+گاوصندوق+دارایی) از شنبه. گروه باید ≥{fa(MIN_MEMBERS)} نفر فعال داشته باشه.</i>")
    await m.reply(text) if m.chat.type != "private" else await m.answer(text)


# ───────────────────────── پایان هفته ─────────────────────────
async def finalize(bot, wk: str) -> None:
    r = redis_service.r
    if not await r.set(f"lg:done:{wk}", "1", nx=True, ex=KEEP):
        return
    scores = await _scores(wk)
    ranked = _ranked(scores)
    paid: dict[int, dict[int, int]] = {}                       # chat → {uid: prize}
    for gi, (chat, s) in enumerate(ranked):
        pay = paid.setdefault(chat, {})
        for i, (uid, _, g) in enumerate(s["members"][:3]):
            if g > 0:
                pay[uid] = pay.get(uid, 0) + PLAYER_PRIZES[i]
        if gi < 3:
            for uid, _, g in s["members"][:TOP_N]:
                if g > 0:
                    pay[uid] = pay.get(uid, 0) + GROUP_PRIZES[gi]
    for chat, pay in paid.items():
        for uid, prize in pay.items():
            await eco.gain(uid, prize)
    medals = ["🥇", "🥈", "🥉"]
    for gi, (chat, s) in enumerate(ranked):
        pay = paid.get(chat, {})
        lines = [f"{medals[i]} {mention(uid, name)}: +{fa(g)} → 🎁 <b>{fa(pay.get(uid, 0))}</b>"
                 for i, (uid, name, g) in enumerate(s["members"][:3]) if g > 0]
        text = (f"🏆 <b>پایان لیگ هفتگی!</b>\nگروه شما رتبه‌ی <b>{fa(gi + 1)}</b> شد (امتیاز {fa(s['score'])})\n\n" + "\n".join(lines)
                + "\n\n💰 جایزه‌ها به جیب برنده‌ها واریز شد. هفته‌ی جدید شروع شد! 🚀")
        try:
            await bot.send_message(chat, text)
        except Exception:  # noqa: BLE001
            pass
    log.info("league_finalized", week=wk, groups=len(ranked))


async def sweeper(bot) -> None:
    r = redis_service.r
    while True:
        try:
            await asyncio.sleep(60)
            cur = evt.week_id()
            old = await r.get("lg:cur")
            if old is None:
                await r.set("lg:cur", cur)
            elif old != cur:
                await r.set("lg:cur", cur)
                await finalize(bot, old)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("league_sweeper_error")
