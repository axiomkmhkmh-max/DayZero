"""قمارخونه: اسلات (انیمیشن واقعی تلگرام)، گردونه، شیر یا خط، تاس — همه با خونه."""
from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.types import CallbackQuery

import config as C
import eco
import rules
from db import User
from ui import b, back, edit, kb
from util import fa

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
            "👇 یا تنهایی با خونه بازی کن:")
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
        play = [[b("▶️ بچرخون" if game != "dice" else "🎲 بنداز", f"g:{game}:play")]]
    return kb(*play, *ctrl, back("games", "◀️ بازی‌ها"))


def game_text(game: str, u: User, bet: int, result: str = "") -> str:
    icon, name = GAMES[game]
    return (f"{icon} <b>{name}</b>\n<i>{INFO[game]}</i>\n\n💵 جیب: <b>{fa(u.cash)}</b>\n"
            + (f"\n{result}\n" if result else ""))


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
        return await edit(cb, game_text(game, user, bet), game_kb(game, bet))
    if op != "play":
        await cb.answer()
        return await edit(cb, game_text(game, user, bet), game_kb(game, bet))

    # ── بازی ──
    if user.cash < bet:
        return await cb.answer("💸 پول کافی تو جیبت نیست!", show_alert=True)
    if not await eco.spend(user.tg_id, bet):
        return await cb.answer("💸 پول کافی تو جیبت نیست!", show_alert=True)
    await cb.answer()
    payout, lines = 0, ""

    if game == "slot":
        dice_msg = await cb.message.answer_dice(emoji="🎰")
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
        await edit(cb, game_text(game, user, bet, "🎡 در حال چرخش..."), None)
        await asyncio.sleep(0.9)
        mult = rules.wheel_spin()
        payout = int(bet * mult)
        lines = ("💀 <b>باختی!</b>" if mult == 0 else f"🎡 ضریب <b>×{mult:g}</b>")
    elif game == "coin":
        pick = parts[3]
        await edit(cb, game_text(game, user, bet, "🪙 سکه توی هواست..."), None)
        await asyncio.sleep(0.8)
        side = rules.RNG.choice(["h", "t"])
        name = {"h": "شیر", "t": "خط"}
        win = side == pick
        payout = int(bet * C.COIN_PAYOUT) if win else 0
        lines = f"🪙 اومد <b>{name[side]}</b> — " + ("✅ بردی!" if win else "❌ باختی!")
    else:  # dice
        m1 = await cb.message.answer_dice(emoji="🎲")
        m2 = await cb.message.answer_dice(emoji="🎲")
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

    if payout:
        await eco.gain(user.tg_id, payout)
    net = payout - bet
    sign = f"+{fa(net)}" if net > 0 else (fa(net) if net < 0 else "۰")
    await eco.update_user(user.tg_id, inc={"stats.bets": 1, "stats.casino_net": net})
    u = await eco.get_user(user.tg_id)
    result = f"{lines}\n💵 شرط: {fa(bet)} → نتیجه: <b>{sign}</b>"
    await edit(cb, game_text(game, u, bet, result), game_kb(game, bet))
