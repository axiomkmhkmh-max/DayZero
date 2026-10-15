"""قمارخونه: اسلات (انیمیشن واقعی تلگرام)، گردونه، شیر یا خط، تاس — همه با خونه."""
from __future__ import annotations

import asyncio

import json

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

import config as C
import eco
import rules
from db import User
import h_spectate
from redis_service import redis_service
from ui import b, back, claim, edit, in_group, kb
from util import fa, norm, parse_amount

router = Router()

GAMES = {
    "slot": ("🎰", "اسلات"), "wheel": ("🎡", "گردونه"), "coin": ("🪙", "شیر یا خط"), "dice": ("🎲", "تاس"),
}
INFO = {
    "slot": "سه تا مثل هم = جایزه (🍫×۳ · 🍇×۴ · 🍋×۵ · 7️⃣×۱۲). دو تا مثل هم = پولت برمی‌گرده.",
    "wheel": "💀 باخت · ×۰٫۵ · ×۱ · ×۱٫۵ · ×۲ · ×۳ · ×۵ · ×۱۰ ← شانس با توئه!",
    "coin": f"شیر یا خط رو انتخاب کن؛ درست بزنی {C.COIN_PAYOUT} برابر می‌گیری.",
    "dice": f"تو یه تاس می‌ندازی، خونه هم یکی؛ بیشتر بیاری {C.DICE_PAYOUT} برابر می‌گیری (مساوی = پولت برمی‌گرده).",
}


ALIAS_HINT = {"slot": "اسلات", "wheel": "گردونه", "coin": "شیرخط", "dice": "تاس"}


def clamp_bet(bet: int, cash: int | None = None) -> int:
    bet = max(C.MIN_BET, min(C.MAX_BET, bet))
    return bet


def step(bet: int, d: int) -> int:
    steps = C.BET_STEPS
    if d > 0:
        return next((s for s in steps if s > bet), steps[-1])
    return next((s for s in reversed(steps) if s < bet), steps[0])


async def render_hub(u: User):
    text = ("🎰 <b>قمارخونه</b>\n\n💵 جیب: <b>" + fa(u.cash) + "</b>\n\n"
            "🆚 <b>شرط‌بندی با آدم‌ها</b> (برنده ۹۵٪ کل پول رو می‌بره):\n"
            "توی گروه روی پیام طرف ریپلای کن و بنویس:\n"
            "<code>دوز 50000</code> ❌⭕\n<code>تاس 1m</code> 🎲\n"
            "پیوی: <code>دوز @یوزر 50000</code>\n\n"
            "⌨️ <b>دستور سریع با خونه</b> (بدون ریپلای):\n"
            "<code>اسلات 5000</code> 🎰 (خودت 🎰 رو ریپلای می‌کنی) · <code>گردونه 5000</code> 🎡\n"
            "<code>شیرخط 5000 شیر</code> 🪙 · <code>تاس 5000</code> 🎲\n\n"
            "👇 یا از دکمه‌ها بازی کن:")
    return text, kb([b("🎰 اسلات", "g:slot"), b("🎡 گردونه", "g:wheel")],
                    [b("🪙 شیر یا خط", "g:coin"), b("🎲 تاس", "g:dice")], back("menu", "◀️ منو"))


@router.callback_query(F.data == "games")
async def cb_games(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    text, markup = await render_hub(user)
    await edit(cb, text, markup)


def game_kb(game: str, bet: int):
    ctrl = [[b("➖", f"g:{game}:dn"), b(f"💵 {fa(bet)}", "noop"), b("➕", f"g:{game}:up")],
            [b("½", f"g:{game}:half"), b("×۲", f"g:{game}:dbl"), b("🔝 حداکثر", f"g:{game}:max")]]
    if game == "coin":
        play = [[b("🪙 شیر", f"g:{game}:play:h"), b("🪙 خط", f"g:{game}:play:t")]]
    else:
        play = [[b("🎰 شرط ببند" if game == "slot" else "▶️ بچرخون" if game != "dice" else "🎲 بنداز", f"g:{game}:play")]]
    return kb(*play, *ctrl, back("games", "◀️ بازی‌ها"))


def game_text(game: str, u: User, bet: int, result: str = "") -> str:
    icon, name = GAMES[game]
    return (f"{icon} <b>{name}</b>\n<i>{INFO[game]}</i>\n\n💵 جیب: <b>{fa(u.cash)}</b>\n"
            + (f"\n{result}\n" if result else ""))


# ───────────────────────── اسلات: خود بازیکن 🎰 می‌فرسته ─────────────────────────
SLOT_TTL = 180


def _slot_key(chat_id: int, msg_id: int) -> str:
    return f"slot:{chat_id}:{msg_id}"


def slot_prompt(bet: int, cash: int, group: bool = False) -> str:
    spec = ("\n\n👀 <b>تماشاچی‌ها</b>: روی همین پیام ریپلای کنید و بنویسید <code>برد 50k</code> یا <code>باخت 50k</code>"
            if group else "")
    return (f"🎰 <b>اسلات</b>\n<i>{INFO['slot']}</i>\n\n💵 شرط: <b>{fa(bet)}</b> · جیب: <b>{fa(cash)}</b>\n\n"
            "↩️ <b>روی همین پیام ریپلای کن و یه 🎰 بفرست</b> (ایموجی 🎰 رو بفرست، گردونه رو خودت می‌چرخونی).\n"
            f"⏳ {fa(SLOT_TTL // 60)} دقیقه وقت داری؛ تا نفرستی پولی کم نمی‌شه." + spec)


async def arm_slot(prompt: Message, uid: int, bet: int) -> None:
    """شرط روی پیام prompt ثبت می‌شه؛ با اولین 🎰ِ ریپلایِ خودِ بازیکن، پول کم و نتیجه حساب می‌شه."""
    await redis_service.r.set(_slot_key(prompt.chat.id, prompt.message_id), json.dumps({"uid": uid, "bet": bet}), ex=SLOT_TTL)
    await claim(prompt, uid)
    if prompt.chat.type != "private":                       # تماشاچی‌ها می‌تونن روی نتیجه شرط ببندن
        await h_spectate.open_book(prompt.chat.id, prompt.message_id, h_spectate.SLOT_SIDES, h_spectate.SLOT_LABELS, [uid], SLOT_TTL)


@router.message(F.dice.emoji == "🎰", F.reply_to_message)
async def on_user_slot(m: Message, user: User = None) -> None:
    if m.from_user is None or m.from_user.is_bot or m.dice is None:
        return
    if getattr(m, "forward_origin", None) is not None or getattr(m, "forward_date", None) is not None:      # 🎰 فورواردی = تقلب
        return
    rep = m.reply_to_message
    key = _slot_key(m.chat.id, rep.message_id)
    raw = await redis_service.r.get(key)
    if raw is None:
        return
    pend = json.loads(raw)
    if pend["uid"] != m.from_user.id:
        return await m.reply("🚫 این شرط مال یکی دیگه‌ست؛ خودت «اسلات 5000» بنویس.")
    if not await redis_service.r.delete(key):                           # یک‌بارمصرف (جلوی چند 🎰 پشت‌سرهم)
        return
    await h_spectate.close(m.chat.id, rep.message_id)                    # از همین لحظه دیگه شرط قبول نمی‌شه
    if user is None:                                                     # گروه: میان‌افزار کاربر رو نساخته
        async with redis_service.lock(f"u:{m.from_user.id}", ttl=20, wait=6):
            actor, _ = await eco.ensure_user(m.from_user)
            return await _settle_user_slot(m, actor, pend["bet"], rep)
    return await _settle_user_slot(m, user, pend["bet"], rep)


async def _settle_user_slot(m: Message, user: User, bet: int, prompt: Message) -> None:
    if not await eco.spend(user.tg_id, bet):
        await h_spectate.settle(m.bot, prompt.chat.id, prompt.message_id, None)
        return await m.reply("💸 پول کافی تو جیبت نیست!")
    await asyncio.sleep(2.6)                                             # صبر تا انیمیشن 🎰 تموم شه
    reels = rules.slot_decode(m.dice.value)
    mult = rules.slot_mult(reels)
    payout = int(bet * mult)
    net = await settle_round(user.tg_id, bet, payout)
    await h_spectate.settle(m.bot, prompt.chat.id, prompt.message_id, "win" if mult > 1 else "lose" if mult < 1 else None)
    shown = " | ".join(rules.SLOT_SYMS[i] for i in reels)
    why = ("🎉 <b>جکپات سه‌تایی!</b>" if mult > 1 else "🤏 دو تا مثل هم؛ پولت برگشت." if mult == 1 else "💀 نشد!")
    sign = f"+{fa(net)}" if net > 0 else (fa(net) if net < 0 else "۰")
    u = await eco.get_user(user.tg_id)
    result = f"{shown}\n{why}\n💵 شرط: {fa(bet)} → نتیجه: <b>{sign}</b>"
    try:
        await prompt.edit_text(game_text("slot", u, bet, result), reply_markup=game_kb("slot", bet))
        await claim(prompt, user.tg_id)
    except Exception:  # noqa: BLE001
        await m.reply(f"🎰\n{result}\n💰 جیب: <b>{fa(u.cash)}</b>")


@router.callback_query(F.data.regexp(r"^g:(slot|wheel|coin|dice)(:.*)?$"))
async def cb_game(cb: CallbackQuery, user: User) -> None:
    parts = cb.data.split(":")
    game = parts[1]
    op = parts[2] if len(parts) > 2 else ""
    bet = clamp_bet(user.bet)

    if op in ("dn", "up", "half", "dbl", "max"):
        bet = {"dn": step(bet, -1), "up": step(bet, 1), "half": bet // 2, "dbl": bet * 2,
               "max": min(C.MAX_BET, max(C.MIN_BET, user.cash))}[op]
        bet = clamp_bet(bet)
        await eco.update_user(user.tg_id, set_={"bet": bet})
        await cb.answer()
        if game == "slot":                       # شرط عوض شد → پیام همون شرط جدید رو می‌گیره
            await edit(cb, slot_prompt(bet, user.cash, in_group(cb)), game_kb(game, bet))
            return await arm_slot(cb.message, user.tg_id, bet)
        return await edit(cb, game_text(game, user, bet), game_kb(game, bet))
    if op != "play":
        await cb.answer()
        return await edit(cb, game_text(game, user, bet), game_kb(game, bet))

    # ── اسلات: ربات خودش نمی‌چرخونه؛ بازیکن 🎰 می‌فرسته ──
    if game == "slot":
        if user.cash < bet:
            return await cb.answer("💸 پول کافی تو جیبت نیست!", show_alert=True)
        await cb.answer()
        await edit(cb, slot_prompt(bet, user.cash, in_group(cb)), game_kb(game, bet))
        return await arm_slot(cb.message, user.tg_id, bet)

    # ── بازی ──
    if user.cash < bet:
        return await cb.answer("💸 پول کافی تو جیبت نیست!", show_alert=True)
    if not await eco.spend(user.tg_id, bet):
        return await cb.answer("💸 پول کافی تو جیبت نیست!", show_alert=True)
    await cb.answer()

    async def anim(text: str) -> None:
        await edit(cb, game_text(game, user, bet, text), None)

    lines, payout = await play_round(game, bet, cb.message.answer_dice, anim, parts[3] if len(parts) > 3 else None)
    net = await settle_round(user.tg_id, bet, payout)
    sign = f"+{fa(net)}" if net > 0 else (fa(net) if net < 0 else "۰")
    u = await eco.get_user(user.tg_id)
    result = f"{lines}\n💵 شرط: {fa(bet)} → نتیجه: <b>{sign}</b>"
    await edit(cb, game_text(game, u, bet, result), game_kb(game, bet))


async def play_round(game: str, bet: int, answer_dice, anim, pick: str | None = None) -> tuple[str, int]:
    """یک دست بازی با خونه (پولِ شرط قبلاً کم شده). خروجی: (متن نتیجه, پرداخت). مشترک بین دکمه‌ها و دستور متنی."""
    payout, lines = 0, ""
    if game == "slot":
        dice_msg = await answer_dice(emoji="🎰")
        v = dice_msg.dice.value
        await asyncio.sleep(2.6)
        reels = rules.slot_decode(v)
        mult = rules.slot_mult(reels)
        payout = int(bet * mult)
        shown = " | ".join(rules.SLOT_SYMS[i] for i in reels)
        why = ("🎉 <b>جکپات سه‌تایی!</b>" if mult > 1 else "🤏 دو تا مثل هم؛ پولت برگشت." if mult == 1 else "💀 نشد!")
        lines = f"{shown}\n{why}"
        try:
            await dice_msg.delete()
        except Exception:  # noqa: BLE001
            pass
    elif game == "wheel":
        await anim("🎡 در حال چرخش...")
        await asyncio.sleep(0.9)
        mult = rules.wheel_spin()
        payout = int(bet * mult)
        lines = ("💀 <b>باختی!</b>" if mult == 0 else f"🎡 ضریب <b>×{mult:g}</b>")
    elif game == "coin":
        pick = pick if pick in ("h", "t") else rules.RNG.choice(["h", "t"])
        await anim("🪙 سکه توی هواست...")
        await asyncio.sleep(0.8)
        side = rules.RNG.choice(["h", "t"])
        name = {"h": "شیر", "t": "خط"}
        win = side == pick
        payout = int(bet * C.COIN_PAYOUT) if win else 0
        lines = f"🪙 تو <b>{name[pick]}</b> گفتی؛ اومد <b>{name[side]}</b> — " + ("✅ بردی!" if win else "❌ باختی!")
    else:  # dice
        m1 = await answer_dice(emoji="🎲")
        m2 = await answer_dice(emoji="🎲")
        await asyncio.sleep(3.2)
        me, house = m1.dice.value, m2.dice.value
        payout = bet if me == house else (int(bet * C.DICE_PAYOUT) if me > house else 0)
        lines = f"🎲 تو: <b>{me}</b>  |  خونه: <b>{house}</b>\n" + (
            "🤝 مساوی؛ پولت برگشت." if me == house else "✅ بردی!" if me > house else "❌ باختی!")
        for mm in (m1, m2):
            try:
                await mm.delete()
            except Exception:  # noqa: BLE001
                pass
    return lines, payout


async def settle_round(uid: int, bet: int, payout: int) -> int:
    if payout:
        await eco.gain(uid, payout)
    net = payout - bet
    await eco.update_user(uid, inc={"stats.bets": 1, "stats.casino_net": net})
    return net


async def text_play(m, user: User, game: str, rest: list[str], in_group: bool) -> None:
    """دستور متنی: «اسلات 50000» · «گردونه 1m» · «تاس 5000» · «شیرخط 2000 شیر» (مبلغ: عدد، 5k، 2m، همه)."""
    reply = m.reply if in_group else m.answer
    amount, pick = None, None
    for tok in rest:
        if tok.startswith(("@", "#")):
            continue
        n = norm(tok)
        if n in ("شیر", "h", "heads"):
            pick = "h"
        elif n in ("خط", "t", "tails"):
            pick = "t"
        elif amount is None:
            amount = parse_amount(tok, user.cash)
    icon, name = GAMES[game]
    if not amount:
        extra = " شیر" if game == "coin" else ""
        return await reply(f"{icon} <b>{name}</b>\nمبلغ شرط رو بنویس: <code>{ALIAS_HINT[game]} 5000{extra}</code> (یا <code>5k</code> · <code>2m</code> · <code>همه</code>)\n"
                           f"🎰 همه‌ی بازی‌ها: <code>بازی</code>")
    if not C.MIN_BET <= amount <= C.MAX_BET:
        return await reply(f"💵 شرط باید بین {fa(C.MIN_BET)} و {fa(C.MAX_BET)} دلار باشه.")
    if game == "slot":
        if user.cash < amount:
            return await reply("💸 پول کافی تو جیبت نیست!")
        prompt = await reply(slot_prompt(amount, user.cash, m.chat.type != "private"), reply_markup=game_kb("slot", amount))
        return await arm_slot(prompt, user.tg_id, amount)
    if not await eco.spend(user.tg_id, amount):
        return await reply("💸 پول کافی تو جیبت نیست!")
    status = await reply(f"{icon} <b>{name}</b> — شرط {fa(amount)} ...")

    async def anim(text: str) -> None:
        try:
            await status.edit_text(f"{icon} <b>{name}</b> — شرط {fa(amount)}\n{text}")
        except Exception:  # noqa: BLE001
            pass

    lines, payout = await play_round(game, amount, m.answer_dice if not in_group else (lambda emoji: m.reply_dice(emoji=emoji)), anim, pick)
    net = await settle_round(user.tg_id, amount, payout)
    sign = f"+{fa(net)}" if net > 0 else (fa(net) if net < 0 else "۰")
    u = await eco.get_user(user.tg_id)
    try:
        await status.edit_text(f"{icon} <b>{name}</b>\n\n{lines}\n💵 شرط: {fa(amount)} → نتیجه: <b>{sign}</b>\n💰 جیب: <b>{fa(u.cash)}</b>")
    except Exception:  # noqa: BLE001
        await reply(f"{lines}\n💵 نتیجه: <b>{sign}</b>")
