"""شرط‌بندی دو نفره: دوز و تاس. پول هر دو نفر بعد از قبول در escrow می‌ره و برنده ۹۵٪ کل رو می‌گیره."""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

import config as C
import eco
import rules
from db import Match, User
from ui import b, kb
from util import esc, fa, mention, utcnow

router = Router()

KINDS = {"ttt": ("❌⭕", "دوز"), "dice": ("🎲", "تاس")}
FACES = ["", "⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
SYM = {"X": "❌", "O": "⭕", "": "▫️"}


def prize(stake: int) -> tuple[int, int]:
    pot = stake * 2
    fee = int(pot * C.PVP_FEE)
    return pot - fee, fee


def _col():
    return Match.get_motor_collection()


def render(m: Match) -> tuple[str, Optional[InlineKeyboardMarkup]]:
    icon, name = KINDS[m.kind]
    p1, p2 = mention(m.p1, m.n1), mention(m.p2, m.n2)
    win, _ = prize(m.stake)
    head = f"{icon} <b>{name}</b>\n{p1} 🆚 {p2}\n💵 شرط هر نفر: <b>{fa(m.stake)}</b> — 🏆 جایزه‌ی برنده: <b>{fa(win)}</b>"
    if m.status == "pending":
        return (head + f"\n\n{p2}، قبول می‌کنی؟ ({C.PVP_PENDING_MIN} دقیقه وقت داری)",
                kb([b("✅ قبول", f"mt:{m.mid}:acc"), b("❌ رد", f"mt:{m.mid}:rej")],
                   [b("🚫 لغو (چالش‌دهنده)", f"mt:{m.mid}:can")]))
    if m.kind == "ttt":
        turn_name = mention(m.turn, m.n1 if m.turn == m.p1 else m.n2) if m.turn else ""
        board = [[b(SYM[m.board[r * 3 + c]], f"mt:{m.mid}:m{r * 3 + c}" if m.status == "active" else "noop")
                  for c in range(3)] for r in range(3)]
        if m.status == "active":
            sym = "❌" if m.turn == m.p1 else "⭕"
            return head + f"\n\n❌ {p1}   ⭕ {p2}\n🎯 نوبت: {turn_name} ({sym})", kb(*board)
        return head, kb(*board)
    return head, None


async def push(bot, m: Match, text: Optional[str] = None, markup="auto") -> None:
    t, mk = render(m)
    if text is not None:
        t = text if markup == "auto" else text
    if markup != "auto":
        mk = markup
    for ref in m.msgs:
        try:
            await bot.edit_message_text(t, chat_id=ref["chat_id"], message_id=ref["message_id"], reply_markup=mk)
        except Exception:  # noqa: BLE001
            pass


async def create(bot, kind: str, p1: User, p2: User, stake: int, chat_id: int, in_group: bool) -> Optional[str]:
    """چالش می‌سازه؛ در صورت خطا متنش رو برمی‌گردونه."""
    if p1.tg_id == p2.tg_id:
        return "😅 با خودت که نمی‌شه شرط بست!"
    if stake < C.MIN_BET or stake > C.MAX_BET:
        return f"💵 شرط باید بین {fa(C.MIN_BET)} و {fa(C.MAX_BET)} دلار باشه."
    if p1.cash < stake:
        return "💸 پول کافی تو جیبت نداری!"
    if p2.cash < stake:
        return f"💸 {esc(p2.name)} پول کافی تو جیبش نداره!"
    open_n = await Match.find({"p1": p1.tg_id, "status": {"$in": ["pending", "active", "starting"]}}).count()
    if open_n >= C.PVP_MAX_OPEN:
        return "⏳ چند تا چالش باز داری؛ اول اونا تموم بشن."
    m = Match(mid=uuid.uuid4().hex[:10], kind=kind, p1=p1.tg_id, p2=p2.tg_id, n1=p1.name, n2=p2.name, stake=stake)
    text, markup = render(m)
    sent = await bot.send_message(chat_id, text, reply_markup=markup)
    m.msgs = [{"chat_id": chat_id, "message_id": sent.message_id}]
    if not in_group:                                   # پیوی: به پیوی طرف هم می‌فرستیم
        try:
            s2 = await bot.send_message(p2.tg_id, text, reply_markup=markup)
            m.msgs.append({"chat_id": p2.tg_id, "message_id": s2.message_id})
        except Exception:  # noqa: BLE001
            await bot.delete_message(chat_id, sent.message_id)
            return "😕 نتونستم به طرف پیام بدم (باید ربات رو استارت کرده باشه). توی گروه با ریپلای امتحان کن."
    await m.insert()
    return None


async def finish(bot, m: Match, winner: Optional[int], note: str = "", from_status: str = "active") -> bool:
    """پایان بازی: اتمیک active→done و پرداخت."""
    r = await _col().update_one({"mid": m.mid, "status": from_status}, {"$set": {"status": "done", "updated_at": utcnow()}})
    if r.matched_count != 1:
        return False
    m = await Match.find_one(Match.mid == m.mid)
    p1, p2 = mention(m.p1, m.n1), mention(m.p2, m.n2)
    if winner is None:
        await eco.gain(m.p1, m.stake)
        await eco.gain(m.p2, m.stake)
        result = "🤝 <b>مساوی!</b> پول هر دو نفر برگشت."
    else:
        win, fee = prize(m.stake)
        loser = m.p2 if winner == m.p1 else m.p1
        await eco.gain(winner, win)
        await eco.update_user(winner, inc={"stats.duel_w": 1, "stats.duel_profit": win - m.stake})
        await eco.update_user(loser, inc={"stats.duel_l": 1})
        wn = p1 if winner == m.p1 else p2
        result = f"🏆 <b>{wn} برنده شد</b> و <b>{fa(win)}</b> دلار به جیب زد! 💰"
    text, markup = render(m)
    await push(await _bot(m), m, text + (f"\n{note}" if note else "") + f"\n\n{result}", markup if m.kind == "ttt" else None)
    return True


_BOT = None


async def _bot(m) -> object:
    return _BOT


def set_bot(bot) -> None:
    global _BOT
    _BOT = bot


# ───────────────────────── دکمه‌ها ─────────────────────────
@router.callback_query(F.data.startswith("mt:"))
async def cb_match(cb: CallbackQuery, user: User) -> None:
    _, mid, op = cb.data.split(":")
    m = await Match.find_one(Match.mid == mid)
    if m is None or m.status in ("done", "cancelled"):
        return await cb.answer("این بازی تموم شده.", show_alert=True)
    uid = cb.from_user.id
    bot = cb.bot

    if op == "can":
        if uid != m.p1:
            return await cb.answer("فقط چالش‌دهنده می‌تونه لغو کنه.", show_alert=True)
        return await _cancel(bot, m, cb, "🚫 چالش لغو شد.")
    if op == "rej":
        if uid != m.p2:
            return await cb.answer("این چالش برای تو نیست!", show_alert=True)
        return await _cancel(bot, m, cb, f"❌ {esc(m.n2)} چالش رو رد کرد.")

    if op == "acc":
        if uid != m.p2:
            return await cb.answer("این چالش برای تو نیست!", show_alert=True)
        if (utcnow() - m.created_at).total_seconds() > C.PVP_PENDING_MIN * 60:
            return await _cancel(bot, m, cb, "⌛ چالش منقضی شد.")
        r = await _col().update_one({"mid": mid, "status": "pending"}, {"$set": {"status": "starting"}})
        if r.matched_count != 1:
            return await cb.answer("این چالش دیگه فعال نیست.", show_alert=True)
        if not await eco.spend(m.p1, m.stake):
            await _col().update_one({"mid": mid}, {"$set": {"status": "cancelled"}})
            await cb.answer()
            return await push(bot, m, f"💸 {esc(m.n1)} دیگه پول کافی نداره؛ چالش لغو شد.", None)
        if not await eco.spend(m.p2, m.stake):
            await eco.gain(m.p1, m.stake)
            await _col().update_one({"mid": mid}, {"$set": {"status": "cancelled"}})
            return await cb.answer("💸 پول کافی نداری!", show_alert=True)
        await cb.answer("🎮 شروع شد!")
        if m.kind == "ttt":
            first = rules.RNG.choice([m.p1, m.p2])
            await _col().update_one({"mid": mid}, {"$set": {"status": "active", "turn": first, "updated_at": utcnow()}})
            return await push(bot, await Match.find_one(Match.mid == mid))
        await _col().update_one({"mid": mid}, {"$set": {"status": "active", "updated_at": utcnow()}})
        return await _play_dice(bot, await Match.find_one(Match.mid == mid))

    if op.startswith("m") and m.kind == "ttt":
        if m.status != "active":
            return await cb.answer("بازی شروع نشده.", show_alert=True)
        if uid not in (m.p1, m.p2):
            return await cb.answer("تو بازیکن این بازی نیستی!", show_alert=True)
        idx = int(op[1:])
        sym = "X" if uid == m.p1 else "O"
        nxt = m.p2 if uid == m.p1 else m.p1
        r = await _col().update_one({"mid": mid, "status": "active", "turn": uid, f"board.{idx}": ""},
                                    {"$set": {f"board.{idx}": sym, "turn": nxt, "updated_at": utcnow()}})
        if r.matched_count != 1:
            return await cb.answer("نوبتت نیست یا خونه پره!", show_alert=True)
        await cb.answer()
        m = await Match.find_one(Match.mid == mid)
        res = rules.ttt_result(m.board)
        if res is None:
            return await push(bot, m)
        await finish(bot, m, None if res == "draw" else (m.p1 if res == "X" else m.p2))
        return
    await cb.answer()


async def _cancel(bot, m: Match, cb: CallbackQuery, text: str) -> None:
    r = await _col().update_one({"mid": m.mid, "status": "pending"}, {"$set": {"status": "cancelled"}})
    if r.matched_count != 1:
        return await cb.answer("این چالش دیگه فعال نیست.", show_alert=True)
    await cb.answer()
    await push(bot, m, text, None)


async def _play_dice(bot, m: Match) -> None:
    await push(bot, m, render(m)[0] + "\n\n🎲 تاس‌ها توی هوان...", None)
    await asyncio.sleep(1.3)
    for _ in range(10):
        r1 = [rules.RNG.randint(1, 6), rules.RNG.randint(1, 6)]
        r2 = [rules.RNG.randint(1, 6), rules.RNG.randint(1, 6)]
        if sum(r1) != sum(r2):
            break
    else:
        r1 = r2 = [0, 0]
    note = (f"\n{mention(m.p1, m.n1)}: {FACES[r1[0]]} {FACES[r1[1]]} = <b>{sum(r1)}</b>"
            f"\n{mention(m.p2, m.n2)}: {FACES[r2[0]]} {FACES[r2[1]]} = <b>{sum(r2)}</b>")
    winner = None if sum(r1) == sum(r2) else (m.p1 if sum(r1) > sum(r2) else m.p2)
    await finish(bot, m, winner, note)


# ───────────────────────── نظافت‌چی (انقضا و باخت بی‌حرکتی) ─────────────────────────
async def sweeper(bot) -> None:
    import structlog
    log = structlog.get_logger("duel")
    while True:
        try:
            await asyncio.sleep(60)
            now = utcnow()
            async for m in Match.find({"status": "pending", "created_at": {"$lt": now - timedelta(minutes=C.PVP_PENDING_MIN)}}):
                r = await _col().update_one({"mid": m.mid, "status": "pending"}, {"$set": {"status": "cancelled"}})
                if r.matched_count == 1:
                    await push(bot, m, "⌛ چالش منقضی شد.", None)
            async for m in Match.find({"status": "active", "updated_at": {"$lt": now - timedelta(minutes=C.PVP_IDLE_MIN)}}):
                loser = m.turn
                winner = m.p2 if loser == m.p1 else m.p1
                await finish(bot, m, winner, "⌛ حریف نوبتش رو نیومد (باخت با انصراف).")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("sweeper_error")
