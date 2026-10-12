"""مرکز مبارزه (پیوی): قدرت‌ها، حمله‌ی تصادفی، حمله به یوزر، تاریخچه."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import combat
import config as C
import eco
import rules
from db import FightLog, User
from h_target import ask_target
from ui import answer_owned, b, back, edit, kb
from util import esc, fa, utcnow, left, dur

router = Router()


async def render_fight(u: User):
    atk, ws = rules.attack_power(u.gear, u.forces)
    dfn, sh, se = rules.defense_power(u.gear, u.forces)
    st = u.stats
    wait = left(u.attack_ready_at)
    text = (f"⚔️ <b>مبارزه</b>\n\n⚔️ قدرت حمله: <b>{fa(atk)}</b>"
            + (f"  ({' + '.join(w['name'] for w in ws)})" if ws else "  (دست خالی)")
            + f"\n🛡 قدرت دفاع: <b>{fa(dfn)}</b>"
            + (f"  ({sh['name']})" if sh else "") + "\n\n"
            f"🏆 حمله‌ی موفق: {fa(st.get('wins', 0))} · ❌ دفع‌شده: {fa(st.get('losses', 0))}\n"
            f"💰 غارت کل: {fa(st.get('loot', 0))} · 😭 از دست‌رفته: {fa(st.get('lost', 0))}\n"
            f"🛡 حمله‌ی دفع‌کرده: {fa(st.get('repelled', 0))} · 🆚 بردِ دوئل: {fa(st.get('duel_w', 0))}\n\n"
            "<b>توی گروه</b>: روی پیام طرف ریپلای کن و بنویس:\n"
            "<code>هکش کن</code> · <code>دزدی از خانه</code>\n"
            "<code>حمله کلاشینکف</code> یا <code>حمله کلاشینکف و شمشیر</code> (ترکیبی +۱۵٪)\n"
            "طرف لازم نیست کاری کنه؛ سپرش خودکار دفاع می‌کنه!")
    if wait > 0:
        text += f"\n\n😮‍💨 حمله‌ی بعدی: {dur(wait)} دیگه"
    return text, kb([b("🎲 حمله به هدف تصادفی", "fight:random")], [b("🎯 حمله به @یوزر", "fight:target")],
                    [b("💻 هک", "fight:hack"), b("🥷 دزدی از خانه", "fight:steal")],
                    [b("📜 آخرین نبردها", "fight:log"), b("🛒 اسلحه و سپر", "mk:shop")], back("menu", "◀️ منو"))


@router.callback_query(F.data == "fight")
async def cb_fight(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    text, markup = await render_fight(user)
    await edit(cb, text, markup)


@router.callback_query(F.data == "fight:target")
async def cb_target(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await ask_target(cb, state, "attack", "می‌تونی اسلحه رو هم بنویسی: <code>@یوزر کلاشینکف و شمشیر</code>")


@router.callback_query(F.data == "fight:hack")
async def cb_hack(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await ask_target(cb, state, "hack", "💻 هکرهات ‹در برابر› هکرهای طرف؛ برنده بشی ۵ تا ۵۰٪ پولش مال تو می‌شه.")


@router.callback_query(F.data == "fight:steal")
async def cb_steal(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await ask_target(cb, state, "htheft", "🥷 ابزار دزدی تو ‹در برابر› امنیت خونه و نگهبان‌های طرف؛ برنده بشی یه وسیله از کمدش مال تو می‌شه.")


@router.callback_query(F.data == "fight:random")
async def cb_random(cb: CallbackQuery, user: User) -> None:
    now = utcnow()
    from datetime import timedelta
    flt = {"tg_id": {"$ne": user.tg_id}, "cash": {"$gte": C.MIN_VICTIM_CASH},
           "last_seen": {"$gte": now - timedelta(days=7)}}
    if user.alliance_id:
        flt["alliance_id"] = {"$ne": user.alliance_id}
    got = await eco.col().aggregate([{"$match": flt}, {"$sample": {"size": 1}}]).to_list(1)
    if not got:
        return await cb.answer("🤷 الان هدف مناسبی پیدا نشد (همه یا خالی‌ان یا غیرفعال).", show_alert=True)
    target = await eco.get_user(got[0]["tg_id"])
    await cb.answer("⚔️")
    text = await combat.do_attack(cb.bot, await eco.get_user(user.tg_id), target)
    await answer_owned(cb, text, kb([b("⚔️ مبارزه", "fight"), b("◀️ منو", "menu")]))


@router.callback_query(F.data == "fight:log")
async def cb_log(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    logs = await FightLog.find({"$or": [{"attacker_id": user.tg_id}, {"defender_id": user.tg_id}]}).sort("-created_at").limit(8).to_list()
    lines = []
    for l in logs:
        if l.attacker_id == user.tg_id:
            lines.append(f"⚔️ به {esc(l.d_name)}: " + (f"✅ +{fa(l.loot)}" if l.win else f"❌ دفع شد ({fa(-l.loot)} خسارت)"))
        else:
            lines.append(f"🛡 {esc(l.a_name)} بهت زد: " + (f"💥 -{fa(l.loot)}" if l.win else "✅ دفعش کردی"))
    await edit(cb, "📜 <b>آخرین نبردها</b>\n\n" + ("\n".join(lines) if lines else "هنوز نبردی نداشتی."), kb(back("fight", "◀️ مبارزه")))
