"""اتحاد: ساخت، پیوستن، بانک مشترک، ارتقا (ظرفیت + بونوس درآمد)، اخراج، خروج."""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pymongo.errors import DuplicateKeyError

import config as C
import eco
from db import Alliance, User
from states import Ask
from ui import b, back, edit, kb
from util import esc, fa, norm

router = Router()


async def members_of(aid: int) -> list[User]:
    return await User.find(User.alliance_id == aid).sort("-invested").to_list()


async def _show(cb: CallbackQuery, uid: int, note: str = "") -> None:
    u = await eco.get_user(uid)
    text, markup = await render_al(u)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


async def render_al(u: User):
    al = await eco.alliance_of(u)
    if al is None:
        text = (f"🤝 <b>اتحاد</b>\n\nتو عضو هیچ اتحادی نیستی.\n\n"
                f"با دوستات اتحاد بساز: بانک مشترک، بونوس درآمد کاسبی (تا +۱۰٪) و امنیت از حمله‌ی هم!\n"
                f"💵 هزینه‌ی ساخت: <b>{fa(C.ALLIANCE_CREATE)}</b>")
        return text, kb([b("➕ ساخت اتحاد", "al:create"), b("📜 لیست اتحادها", "al:list")], back("menu", "◀️ منو"))
    cap, bonus, _ = C.ALLIANCE_LEVELS[al.level]
    n = await User.find(User.alliance_id == al.aid).count()
    leader = u.tg_id == al.leader_id
    text = (f"🤝 <b>{esc(al.name)}</b> — سطح {fa(al.level)}\n\n👥 اعضا: {fa(n)}/{fa(cap)}\n"
            f"📈 بونوس درآمد کاسبی: <b>+{int(bonus * 100)}٪</b> برای همه‌ی اعضا\n🏦 بانک اتحاد: <b>{fa(al.bank)}</b>\n\n"
            "هم‌اتحادی‌ها نمی‌تونن به هم حمله کنن یا جن و طلسم بفرستن.")
    rows = [[b("🏦 واریز به بانک", "al:dep"), b("👥 اعضا", "al:members")]]
    if leader and al.level < max(C.ALLIANCE_LEVELS):
        cost = C.ALLIANCE_LEVELS[al.level + 1][2]
        rows.append([b(f"⬆️ ارتقا به سطح {fa(al.level + 1)} — {fa(cost)} (از بانک)", "al:up")])
    rows.append([b("🚪 خروج از اتحاد", "al:leave")])
    rows.append(back("menu", "◀️ منو"))
    return text, kb(*rows)


@router.callback_query(F.data == "al")
async def cb_al(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await _show(cb, user.tg_id)


# ───────────────────────── ساخت ─────────────────────────
@router.callback_query(F.data == "al:create")
async def cb_create(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    if user.alliance_id:
        return await cb.answer("اول از اتحاد فعلیت خارج شو.", show_alert=True)
    if user.cash < C.ALLIANCE_CREATE:
        return await cb.answer(f"💸 {fa(C.ALLIANCE_CREATE)} دلار نقد لازمه!", show_alert=True)
    await state.set_state(Ask.alname)
    await cb.answer()
    await edit(cb, "🤝 <b>ساخت اتحاد</b>\n\nاسم اتحاد رو بفرست (۳ تا ۲۰ حرف):", kb([b("❌ لغو", "al")]))


@router.message(StateFilter(Ask.alname), F.chat.type == "private", F.text)
async def got_name(m: Message, user: User, state: FSMContext) -> None:
    name = re.sub(r"\s+", " ", m.text.strip())
    if not 3 <= len(name) <= 20 or name.startswith("/"):
        return await m.answer("⚠️ اسم باید ۳ تا ۲۰ حرف باشه. دوباره بفرست:")
    if user.cash < C.ALLIANCE_CREATE or user.alliance_id:
        await state.clear()
        return await m.answer("💸 پول کافی نداری یا عضو اتحادی هستی.")
    aid = await eco.next_seq("alliance")
    if not await eco.spend(user.tg_id, C.ALLIANCE_CREATE, flt={"alliance_id": None}):
        await state.clear()
        return await m.answer("💸 پول کافی نداری.")
    await eco.settle(user.tg_id)
    try:
        await Alliance(aid=aid, name=name, name_key=norm(name), leader_id=user.tg_id).insert()
    except DuplicateKeyError:
        await eco.gain(user.tg_id, C.ALLIANCE_CREATE)
        return await m.answer("😕 این اسم قبلاً گرفته شده؛ یکی دیگه بفرست:")
    await eco.update_user(user.tg_id, set_={"alliance_id": aid})
    await state.clear()
    await m.answer(f"🎉 اتحاد <b>{esc(name)}</b> ساخته شد! دوستات رو دعوت کن.", reply_markup=kb([b("🤝 اتحاد", "al")]))


# ───────────────────────── لیست و پیوستن ─────────────────────────
@router.callback_query(F.data == "al:list")
async def cb_list(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    rows, lines = [], ["📜 <b>اتحادها</b>\n"]
    pipe = [{"$group": {"_id": "$alliance_id", "n": {"$sum": 1}, "w": {"$sum": "$invested"}}},
            {"$match": {"_id": {"$ne": None}}}, {"$sort": {"n": -1, "w": -1}}, {"$limit": 8}]
    for g in await eco.col().aggregate(pipe).to_list(8):
        al = await Alliance.find_one(Alliance.aid == g["_id"])
        if not al:
            continue
        cap = C.ALLIANCE_LEVELS[al.level][0]
        lines.append(f"🤝 {esc(al.name)} — 👥 {fa(g['n'])}/{fa(cap)} — سطح {fa(al.level)}")
        if not user.alliance_id and g["n"] < cap:
            rows.append([b(f"➕ پیوستن به {al.name}", f"al:join:{al.aid}")])
    if len(lines) == 1:
        lines.append("هنوز اتحادی ساخته نشده!")
    rows.append(back("al", "◀️ اتحاد"))
    await edit(cb, "\n".join(lines), kb(*rows))


@router.callback_query(F.data.startswith("al:join:"))
async def cb_join(cb: CallbackQuery, user: User) -> None:
    if user.alliance_id:
        return await cb.answer("تو عضو یه اتحادی!", show_alert=True)
    aid = int(cb.data.split(":")[2])
    al = await Alliance.find_one(Alliance.aid == aid)
    if not al:
        return await cb.answer("اتحاد پیدا نشد.", show_alert=True)
    if await User.find(User.alliance_id == aid).count() >= C.ALLIANCE_LEVELS[al.level][0]:
        return await cb.answer("ظرفیت اتحاد پره!", show_alert=True)
    await eco.settle(user.tg_id)
    await eco.update_user(user.tg_id, set_={"alliance_id": aid})
    await cb.answer()
    await _show(cb, user.tg_id, f"✅ به اتحاد <b>{esc(al.name)}</b> پیوستی!")


# ───────────────────────── بانک / ارتقا ─────────────────────────
@router.callback_query(F.data == "al:dep")
async def cb_dep_menu(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await edit(cb, f"🏦 <b>واریز به بانک اتحاد</b>\n\n💵 جیب: {fa(user.cash)}\nبانک برای ارتقای اتحاد خرج می‌شه (فقط رهبر ارتقا می‌ده).",
               kb([b("۱۰٪", "al:dp:10"), b("۲۵٪", "al:dp:25"), b("۵۰٪", "al:dp:50"), b("همه", "al:dp:100")], back("al", "◀️ اتحاد")))


@router.callback_query(F.data.startswith("al:dp:"))
async def cb_dep(cb: CallbackQuery, user: User) -> None:
    if not user.alliance_id:
        return await cb.answer("عضو اتحادی نیستی!", show_alert=True)
    amount = user.cash * int(cb.data.split(":")[2]) // 100
    if amount <= 0 or not await eco.spend(user.tg_id, amount):
        return await cb.answer("پولی برای واریز نیست.", show_alert=True)
    await Alliance.get_motor_collection().update_one({"aid": user.alliance_id}, {"$inc": {"bank": amount}})
    await cb.answer()
    await _show(cb, user.tg_id, f"🏦 {fa(amount)} دلار به بانک اتحاد واریز شد.")


@router.callback_query(F.data == "al:up")
async def cb_up(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("فقط رهبر می‌تونه!", show_alert=True)
    if al.level >= max(C.ALLIANCE_LEVELS):
        return await cb.answer("به سقف رسیدی!", show_alert=True)
    cost = C.ALLIANCE_LEVELS[al.level + 1][2]
    r = await Alliance.get_motor_collection().update_one({"aid": al.aid, "level": al.level, "bank": {"$gte": cost}},
                                                         {"$inc": {"bank": -cost, "level": 1}})
    if r.matched_count != 1:
        return await cb.answer(f"🏦 بانک باید {fa(cost)} دلار داشته باشه!", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, f"⬆️ اتحاد شد سطح {fa(al.level + 1)}!")


# ───────────────────────── اعضا / اخراج / خروج ─────────────────────────
@router.callback_query(F.data == "al:members")
async def cb_members(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al:
        return await cb.answer("عضو اتحادی نیستی!", show_alert=True)
    await cb.answer()
    mem = await members_of(al.aid)
    lines = [f"👥 <b>اعضای {esc(al.name)}</b>\n"]
    rows = []
    for m in mem:
        crown = "👑 " if m.tg_id == al.leader_id else ""
        lines.append(f"{crown}{esc(m.name)} — {fa(eco.net_worth(m))}")
        if user.tg_id == al.leader_id and m.tg_id != user.tg_id:
            rows.append([b(f"❌ اخراج {m.name}", f"al:kick:{m.tg_id}")])
    rows.append(back("al", "◀️ اتحاد"))
    await edit(cb, "\n".join(lines), kb(*rows))


@router.callback_query(F.data.startswith("al:kick:"))
async def cb_kick(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al or al.leader_id != user.tg_id:
        return await cb.answer("فقط رهبر!", show_alert=True)
    target = int(cb.data.split(":")[2])
    t = await eco.get_user(target)
    if not t or t.alliance_id != al.aid or target == user.tg_id:
        return await cb.answer("عضو نیست.", show_alert=True)
    await eco.settle(target)
    await eco.update_user(target, set_={"alliance_id": None})
    await cb.answer("اخراج شد.")
    await cb_members(cb, user)


@router.callback_query(F.data == "al:leave")
async def cb_leave(cb: CallbackQuery, user: User) -> None:
    al = await eco.alliance_of(user)
    if not al:
        return await cb.answer()
    await eco.settle(user.tg_id)
    await eco.update_user(user.tg_id, set_={"alliance_id": None})
    rest = [m for m in await members_of(al.aid) if m.tg_id != user.tg_id]
    if not rest:
        await al.delete()
    elif al.leader_id == user.tg_id:
        await Alliance.get_motor_collection().update_one({"aid": al.aid}, {"$set": {"leader_id": rest[0].tg_id}})
    await cb.answer()
    await _show(cb, user.tg_id, "🚪 از اتحاد خارج شدی.")
