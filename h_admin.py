"""Day Zero — پنل ادمین.

باز کردن پنل (فقط پیوی، فقط ادمین‌ها):  /admin

دستورهای سریع (پیوی یا گروه؛ آیدی/@یوزر یا ریپلای روی پیام طرف):
  /info  <کاربر>              اطلاعات بازیکن
  /ban   <کاربر> [دلیل]       بن
  /unban <کاربر>              آنبن
  /give  <کاربر> <مبلغ>       دادن پول  (مبلغ: 5000 · 5k · 2m · 1b)
  /take  <کاربر> <مبلغ>       گرفتن پول
  /reset <کاربر>              ریست کامل پیشرفت بازیکن
  /cd    <کاربر>              ریست کول‌داون‌ها
  /newgift <مبلغ> <تعداد> [کد]  ساخت کد هدیه

ادمین‌ها در config.py (ADMIN_IDS) یا متغیر محیطی ADMIN_IDS تعریف می‌شن.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import string
from datetime import timedelta
from typing import Any, Awaitable, Callable, Optional

import structlog
from aiogram import BaseMiddleware, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config as C
import eco
from db import Alliance, GiftCode, User
from redis_service import redis_service
from states import AdminAsk
from ui import DANGER, PRIMARY, SUCCESS, b, edit, kb
from util import esc, fa, left, mention, parse_amount, utcnow

log = structlog.get_logger("admin")
router = Router()

BANNED_KEY = "dz:banned"                    # set از آیدی بازیکن‌های بن‌شده (چک سریع)
MAX_AMOUNT = 10_000_000_000_000             # سقف منطقی برای یک بار دادن/گرفتن پول


def is_admin(uid: Optional[int]) -> bool:
    return uid is not None and uid in C.ADMIN_IDS


def _is_admin_msg(m: Message) -> bool:
    return m.from_user is not None and is_admin(m.from_user.id)


def _is_admin_pm(m: Message) -> bool:
    return _is_admin_msg(m) and m.chat.type == "private"


# ═════════════════════════ بن: میان‌افزار + همگام‌سازی ═════════════════════════
async def sync_bans() -> int:
    """موقع استارت ربات: لیست بن‌شده‌ها از Mongo می‌ره تو Redis."""
    ids = [d["tg_id"] async for d in eco.col().find({"banned": True}, {"tg_id": 1})]
    pipe = redis_service.r.pipeline()
    pipe.delete(BANNED_KEY)
    if ids:
        pipe.sadd(BANNED_KEY, *ids)
    await pipe.execute()
    return len(ids)


class BanMiddleware(BaseMiddleware):
    """بازیکن بن‌شده نه دستور می‌تونه بزنه نه دکمه. باید قبل از GameMiddleware ثبت بشه."""

    async def __call__(self, handler: Callable[[Any, dict], Awaitable[Any]], event: Any, data: dict) -> Any:
        tg = data.get("event_from_user")
        if tg is None or is_admin(tg.id) or not await redis_service.r.sismember(BANNED_KEY, tg.id):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("🚫 از ربات بن شدی.", show_alert=True)
        elif isinstance(event, Message) and event.chat.type == "private":
            if await redis_service.r.set(f"bannote:{tg.id}", "1", nx=True, ex=60):       # حداکثر یه پیام در دقیقه
                u = await eco.get_user(tg.id)
                why = f"\nدلیل: {esc(u.ban_reason)}" if u and u.ban_reason else ""
                await event.answer(f"🚫 تو از ربات بن شدی.{why}")
        return None


# ═════════════════════════ عملیات اصلی ═════════════════════════
async def _notify(bot, uid: int, text: str) -> None:
    try:
        await bot.send_message(uid, text)
    except Exception:  # noqa: BLE001
        pass


async def _audit(bot, admin_id: int, text: str) -> None:
    """به بقیه‌ی ادمین‌ها خبر می‌ده که یه ادمین چه کاری کرد."""
    log.info("admin_action", admin=admin_id, action=text)
    for aid in C.ADMIN_IDS:
        if aid != admin_id:
            await _notify(bot, aid, f"🛡 <b>ادمین</b> <code>{admin_id}</code>: {text}")


def _who(u: User) -> str:
    return f"{mention(u.tg_id, u.name)} (<code>{u.tg_id}</code>)"


async def do_ban(bot, admin_id: int, u: User, reason: str = "") -> str:
    if is_admin(u.tg_id):
        return "⛔ ادمین رو نمی‌شه بن کرد."
    await eco.col().update_one({"tg_id": u.tg_id}, {"$set": {"banned": True, "ban_reason": reason[:200], "banned_at": utcnow()}})
    await redis_service.r.sadd(BANNED_KEY, u.tg_id)
    await _notify(bot, u.tg_id, "🚫 تو از ربات بن شدی." + (f"\nدلیل: {esc(reason)}" if reason else ""))
    await _audit(bot, admin_id, f"بن {_who(u)}" + (f" — {esc(reason)}" if reason else ""))
    return f"🚫 {_who(u)} بن شد."


async def do_unban(bot, admin_id: int, u: User) -> str:
    await eco.col().update_one({"tg_id": u.tg_id}, {"$set": {"banned": False, "ban_reason": "", "banned_at": None}})
    await redis_service.r.srem(BANNED_KEY, u.tg_id)
    await _notify(bot, u.tg_id, "✅ بنت برداشته شد؛ دوباره می‌تونی بازی کنی.")
    await _audit(bot, admin_id, f"آنبن {_who(u)}")
    return f"✅ {_who(u)} آنبن شد."


async def do_cash(bot, admin_id: int, u: User, amount: int, give: bool) -> str:
    amount = min(amount, MAX_AMOUNT)
    if give:
        await eco.gain(u.tg_id, amount)
        await _notify(bot, u.tg_id, f"🎁 ادمین <b>{fa(amount)}</b> دلار بهت داد!")
        await _audit(bot, admin_id, f"{fa(amount)} دلار به {_who(u)} داد")
        return f"💰 <b>{fa(amount)}</b> دلار به {_who(u)} داده شد."
    fresh = await eco.get_user(u.tg_id)
    amt = min(amount, max(0, fresh.cash if fresh else 0))          # بیشتر از موجودی جیب نمی‌شه گرفت
    if amt <= 0 or not await eco.update_user(u.tg_id, cash=-amt):
        return f"💸 {_who(u)} پولی تو جیبش نداره (گاوصندوق دست‌نخورده می‌مونه)."
    await _notify(bot, u.tg_id, f"💸 ادمین <b>{fa(amt)}</b> دلار از جیبت کم کرد.")
    await _audit(bot, admin_id, f"{fa(amt)} دلار از {_who(u)} گرفت")
    note = f" (درخواستی {fa(amount)} بود؛ بیشتر از جیبش نبود)" if amt < amount else ""
    return f"💸 <b>{fa(amt)}</b> دلار از جیب {_who(u)} کم شد.{note}"


async def do_clear_cd(bot, admin_id: int, u: User) -> str:
    await eco.col().update_one({"tg_id": u.tg_id}, {"$set": {
        "job_ready_at": None, "walk_ready_at": None, "pocket_ready_at": None,
        "child_ready_at": None, "attack_ready_at": None}})
    keys = [f"hack:{u.tg_id}", f"htheft:{u.tg_id}"]
    if u.spouse_id:
        keys.append(f"intim:{min(u.tg_id, u.spouse_id)}:{max(u.tg_id, u.spouse_id)}")
    await redis_service.r.delete(*keys)
    await _audit(bot, admin_id, f"کول‌داون‌های {_who(u)} رو ریست کرد")
    return f"⏱ کول‌داون‌های {_who(u)} ریست شد (کار، چرخ، جیبی، بچه، حمله، هک، دزدی، جایزه‌ی زوجی)."


async def do_reset(bot, admin_id: int, u: User) -> str:
    """پیشرفت بازیکن رو به حالت تازه‌وارد برمی‌گردونه (اسم، یوزرنیم، تاریخ ثبت‌نام و وضعیت بن می‌مونن)."""
    uid, col = u.tg_id, eco.col()
    if u.spouse_id:                                    # همسر هم مجرد می‌شه
        await col.update_one({"tg_id": u.spouse_id, "spouse_id": uid}, {"$set": {
            "spouse_id": None, "mahr": 0, "mahr_to": None, "rand_marriage": False, "love_lock_until": None}})
    if u.alliance_id:                                  # رهبر اتحاد بود: اتحاد منحل می‌شه
        al = await Alliance.find_one(Alliance.aid == u.alliance_id)
        if al and al.leader_id == uid:
            await col.update_many({"alliance_id": u.alliance_id}, {"$set": {"alliance_id": None}})
            await al.delete()
    fresh = User(tg_id=uid).model_dump(exclude={
        "id", "revision_id", "tg_id", "name", "username", "created_at", "last_seen",
        "banned", "ban_reason", "banned_at"})
    now = utcnow()
    fresh["income_since"] = now
    fresh["shield_until"] = now + timedelta(hours=C.NEWBIE_SHIELD_HOURS)
    await col.update_one({"tg_id": uid}, {"$set": fresh})
    keys = [f"hack:{uid}", f"htheft:{uid}"]
    if u.spouse_id:
        keys.append(f"intim:{min(uid, u.spouse_id)}:{max(uid, u.spouse_id)}")
    await redis_service.r.delete(*keys)
    await _notify(bot, uid, "🔄 پیشرفتت توسط ادمین ریست شد.")
    await _audit(bot, admin_id, f"ریست کامل {_who(u)}")
    return f"🔄 {_who(u)} کامل ریست شد (پول اولیه {fa(C.START_CASH)} دلار)."


async def do_gift(admin_id: int, bot, amount: int, uses: int, code: str) -> str:
    code = code.upper()
    if await GiftCode.get_motor_collection().find_one({"code": code}):
        return f"🎁 کد <code>{code}</code> از قبل وجود داره."
    await GiftCode.get_motor_collection().insert_one(
        {"code": code, "amount": amount, "max_uses": uses, "used_by": [], "created_at": utcnow()})
    await _audit(bot, admin_id, f"کد هدیه <code>{code}</code> ساخت ({fa(amount)} دلار، {fa(uses)} بار)")
    return (f"🎁 کد هدیه ساخته شد:\n<code>{code}</code>\n"
            f"💰 {fa(amount)} دلار · 👥 {fa(uses)} بار مصرف\nبازیکن‌ها تو پیوی ربات: <code>کد هدیه {code}</code>")


async def _broadcast(bot, admin_id: int, text: str) -> None:
    sent = failed = 0
    async for d in eco.col().find({"banned": {"$ne": True}}, {"tg_id": 1}):
        for attempt in range(2):
            try:
                await bot.send_message(d["tg_id"], text)
                sent += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:  # noqa: BLE001
                failed += 1
                break
        await asyncio.sleep(0.05)                      # زیر سقف ۳۰ پیام در ثانیه‌ی تلگرام
    await _notify(bot, admin_id, f"📢 پیام همگانی تموم شد.\n✅ ارسال‌شده: {fa(sent)}\n❌ ناموفق: {fa(failed)}")
    await _audit(bot, admin_id, f"پیام همگانی فرستاد ({fa(sent)} نفر)")


# ═════════════════════════ نمایش ═════════════════════════
def _fmt_dt(d) -> str:
    return d.strftime("%Y-%m-%d %H:%M") + " UTC" if d else "—"


def info_text(u: User) -> str:
    atk_h = f"@{u.username}" if u.username else "—"
    spouse = f"<code>{u.spouse_id}</code>" if u.spouse_id else "—"
    ban = f"🚫 <b>بن‌شده</b>" + (f" ({esc(u.ban_reason)})" if u.ban_reason else "") if u.banned else "✅ آزاد"
    shield = f"🛡 {int(left(u.shield_until) // 60)} دقیقه" if left(u.shield_until) > 0 else "—"
    return (f"👤 <b>{esc(u.name)}</b>  <code>{u.tg_id}</code>\n"
            f"یوزرنیم: {atk_h}   وضعیت: {ban}\n\n"
            f"💵 جیب: <b>{fa(u.cash)}</b>\n🔐 گاوصندوق: <b>{fa(u.vault)}</b>\n"
            f"📈 ثروت کل: <b>{fa(eco.net_worth(u))}</b>\n\n"
            f"🏠 خونه: {fa(u.house)}   👶 بچه: {fa(u.kids)}   💍 همسر: {spouse}\n"
            f"🏪 کسب‌وکار: {fa(len(u.businesses))}   🤝 اتحاد: {fa(u.alliance_id or 0) if u.alliance_id else '—'}\n"
            f"🛡 محافظ: {shield}   ⚔️ شیفت‌ها: {fa(u.shifts)}\n"
            f"🕒 ثبت‌نام: {_fmt_dt(u.created_at)}\n🕒 آخرین فعالیت: {_fmt_dt(u.last_seen)}")


def panel_kb() -> InlineKeyboardMarkup:
    return kb(
        [b("📊 آمار", "adm:stats", PRIMARY), b("🔎 اطلاعات بازیکن", "adm:ask:info", PRIMARY)],
        [b("🚫 بن", "adm:ask:ban", DANGER), b("✅ آنبن", "adm:ask:unban", SUCCESS)],
        [b("💰 دادن پول", "adm:ask:give", SUCCESS), b("💸 گرفتن پول", "adm:ask:take", DANGER)],
        [b("🔄 ریست بازیکن", "adm:ask:reset", DANGER), b("⏱ ریست کول‌داون", "adm:ask:cd", PRIMARY)],
        [b("🎁 ساخت کد هدیه", "adm:gift", SUCCESS), b("📢 پیام همگانی", "adm:bc", PRIMARY)],
        [b("📋 لیست بن‌شده‌ها", "adm:banlist", PRIMARY)],
    )


PANEL_TEXT = "🛡 <b>پنل ادمین Day Zero</b>\n\nیکی از گزینه‌ها رو انتخاب کن:"
CANCEL_KB = kb([b("❌ لغو", "adm:cancel")])


def user_kb(u: User) -> InlineKeyboardMarkup:
    ban_btn = b("✅ آنبن", f"adm:u:unban:{u.tg_id}", SUCCESS) if u.banned else b("🚫 بن", f"adm:u:ban:{u.tg_id}", DANGER)
    return kb(
        [ban_btn],
        [b("💰 دادن پول", f"adm:u:give:{u.tg_id}", SUCCESS), b("💸 گرفتن پول", f"adm:u:take:{u.tg_id}", DANGER)],
        [b("🔄 ریست", f"adm:u:reset:{u.tg_id}", DANGER), b("⏱ کول‌داون", f"adm:u:cd:{u.tg_id}", PRIMARY)],
        [b("◀️ پنل", "adm:home")],
    )


ASK_TITLES = {"info": "🔎 اطلاعات بازیکن", "ban": "🚫 بن", "unban": "✅ آنبن", "give": "💰 دادن پول",
              "take": "💸 گرفتن پول", "reset": "🔄 ریست بازیکن", "cd": "⏱ ریست کول‌داون"}


async def _resolve(text: str) -> Optional[User]:
    t = text.strip().lstrip("#")
    if t.isdigit():
        return await eco.get_user(int(t))
    m = re.match(r"^@?([A-Za-z0-9_]{4,32})$", t)
    return await eco.find_by_username(m.group(1)) if m else None


async def stats_text() -> str:
    col = eco.col()
    total = await col.count_documents({})
    banned = await col.count_documents({"banned": True})
    day = await col.count_documents({"last_seen": {"$gte": utcnow() - timedelta(days=1)}})
    agg = await col.aggregate([{"$group": {"_id": None, "cash": {"$sum": "$cash"}, "vault": {"$sum": "$vault"}}}]).to_list(1)
    cash, vault = (agg[0]["cash"], agg[0]["vault"]) if agg else (0, 0)
    rich = await col.find({}, {"tg_id": 1, "name": 1, "cash": 1, "vault": 1}).sort([("cash", -1)]).limit(5).to_list(5)
    top = "\n".join(f"{i}. {esc(d.get('name', '?'))} (<code>{d['tg_id']}</code>) — {fa(d.get('cash', 0) + d.get('vault', 0))}"
                    for i, d in enumerate(rich, 1))
    return (f"📊 <b>آمار ربات</b>\n\n👥 کل بازیکن‌ها: <b>{fa(total)}</b>\n🟢 فعال ۲۴ ساعت اخیر: <b>{fa(day)}</b>\n"
            f"🚫 بن‌شده: <b>{fa(banned)}</b>\n\n💵 مجموع جیب‌ها: <b>{fa(cash)}</b>\n🔐 مجموع گاوصندوق‌ها: <b>{fa(vault)}</b>\n\n"
            f"🏆 پولدارترین‌ها (بر اساس جیب):\n{top or '—'}")


async def banlist_text() -> str:
    rows = await eco.col().find({"banned": True}, {"tg_id": 1, "name": 1, "ban_reason": 1}).limit(40).to_list(40)
    if not rows:
        return "📋 هیچ بازیکنی بن نیست."
    lines = [f"• {esc(d.get('name', '?'))} — <code>{d['tg_id']}</code>" + (f" — {esc(d['ban_reason'])}" if d.get("ban_reason") else "")
             for d in rows]
    return "📋 <b>بن‌شده‌ها</b>\n\n" + "\n".join(lines)


# ═════════════════════════ مسیردهی یک عمل روی یک بازیکن ═════════════════════════
async def act_on(bot, state: FSMContext, admin_id: int, act: str, u: User) -> tuple[str, InlineKeyboardMarkup]:
    """یه کار رو روی بازیکن انجام می‌ده (یا از ادمین ورودی بعدی می‌خواد). خروجی: (متن, دکمه‌ها)"""
    uid = u.tg_id
    if act == "info":
        return info_text(u), user_kb(u)
    if act == "ban":
        if is_admin(uid):
            return "⛔ ادمین رو نمی‌شه بن کرد.", kb([b("◀️ پنل", "adm:home")])
        if u.banned:
            return f"{_who(u)} از قبل بن شده.", user_kb(u)
        await state.set_state(AdminAsk.reason)
        await state.update_data(uid=uid)
        return f"🚫 دلیل بن {_who(u)} رو بنویس (یا <code>-</code> برای بدون دلیل):", CANCEL_KB
    if act == "unban":
        text = await do_unban(bot, admin_id, u)
    elif act in ("give", "take"):
        await state.set_state(AdminAsk.amount)
        await state.update_data(uid=uid, mode=act)
        verb = "بدم به" if act == "give" else "بگیرم از"
        return (f"{'💰' if act == 'give' else '💸'} چقدر {verb} {_who(u)}؟\n"
                f"مبلغ رو بنویس: <code>5000</code> · <code>5k</code> · <code>2m</code> · <code>1b</code>"), CANCEL_KB
    elif act == "reset":
        return (f"⚠️ <b>ریست کامل {_who(u)}؟</b>\nپول، خونه، بچه، کسب‌وکار، نیروها، اتحاد و همه‌چیزش پاک می‌شه و برمی‌گرده به حالت تازه‌وارد. "
                f"این کار برگشت نداره!"), kb([b("✅ بله، ریست کن", f"adm:rs:{uid}", DANGER)], [b("❌ لغو", "adm:cancel")])
    elif act == "cd":
        text = await do_clear_cd(bot, admin_id, u)
    else:
        return "؟", kb([b("◀️ پنل", "adm:home")])
    fresh = await eco.get_user(uid) or u
    return text + "\n\n" + info_text(fresh), user_kb(fresh)


# ═════════════════════════ پنل: دکمه‌ها ═════════════════════════
@router.message(Command("admin"), F.func(_is_admin_msg))
async def cmd_admin(m: Message, state: FSMContext) -> None:
    if m.chat.type != "private":
        return await m.reply("🛡 پنل ادمین رو تو پیوی ربات باز کن: /admin")
    await state.clear()
    await m.answer(PANEL_TEXT, reply_markup=panel_kb())


@router.callback_query(F.data.startswith("adm:"))
async def cb_admin(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id):
        return await cb.answer("🚫 دسترسی نداری.", show_alert=True)
    if cb.message is None or cb.message.chat.type != "private":
        return await cb.answer("پنل ادمین فقط تو پیوی کار می‌کنه.", show_alert=True)
    parts = cb.data.split(":")
    op, bot, aid = parts[1], cb.bot, cb.from_user.id
    await cb.answer()

    if op in ("home", "cancel"):
        await state.clear()
        return await edit(cb, PANEL_TEXT, panel_kb())
    if op == "stats":
        return await edit(cb, await stats_text(), kb([b("🔄 تازه‌سازی", "adm:stats", PRIMARY)], [b("◀️ پنل", "adm:home")]))
    if op == "banlist":
        return await edit(cb, await banlist_text(), kb([b("◀️ پنل", "adm:home")]))
    if op == "ask":                                    # از منوی اصلی: اول آیدی/یوزرنیم می‌خوایم
        act = parts[2]
        await state.set_state(AdminAsk.target)
        await state.update_data(act=act)
        return await edit(cb, f"<b>{ASK_TITLES.get(act, act)}</b>\n\n🎯 <b>آیدی عددی</b> یا <b>@یوزرنیم</b> بازیکن رو بفرست:", CANCEL_KB)
    if op == "u":                                      # از کارت بازیکن: آیدی معلومه
        act, uid = parts[2], int(parts[3])
        u = await eco.get_user(uid)
        if u is None:
            return await edit(cb, "بازیکن پیدا نشد.", kb([b("◀️ پنل", "adm:home")]))
        await state.clear()
        text, markup = await act_on(bot, state, aid, act, u)
        return await edit(cb, text, markup)
    if op == "rs":                                     # تأیید ریست
        u = await eco.get_user(int(parts[2]))
        await state.clear()
        if u is None:
            return await edit(cb, "بازیکن پیدا نشد.", kb([b("◀️ پنل", "adm:home")]))
        text = await do_reset(bot, aid, u)
        fresh = await eco.get_user(u.tg_id) or u
        return await edit(cb, text + "\n\n" + info_text(fresh), user_kb(fresh))
    if op == "gift":
        await state.set_state(AdminAsk.gift)
        return await edit(cb, "🎁 <b>ساخت کد هدیه</b>\n\nاین‌جوری بفرست:\n<code>مبلغ تعداد-استفاده [کد]</code>\n"
                              "مثال: <code>1m 10</code> (کد رندوم) یا <code>5m 3 EID1405</code>", CANCEL_KB)
    if op == "bc":
        await state.set_state(AdminAsk.broadcast)
        return await edit(cb, "📢 <b>پیام همگانی</b>\n\nمتنی که می‌خوای برای همه‌ی بازیکن‌ها بره رو بفرست (HTML ساده مجازه):", CANCEL_KB)
    if op == "bcgo":
        data = await state.get_data()
        text = data.get("bc_text")
        await state.clear()
        if not text:
            return await edit(cb, "متنی پیدا نشد؛ دوباره امتحان کن.", kb([b("◀️ پنل", "adm:home")]))
        asyncio.create_task(_broadcast(bot, aid, text))
        return await edit(cb, "📢 ارسال شروع شد؛ وقتی تموم بشه گزارش می‌گیری.", kb([b("◀️ پنل", "adm:home")]))


# ═════════════════════════ پنل: پیام‌های ورودی (FSM) ═════════════════════════
@router.message(StateFilter(AdminAsk.target), F.func(_is_admin_pm), F.text)
async def got_target(m: Message, state: FSMContext) -> None:
    act = (await state.get_data()).get("act", "info")
    u = await _resolve(m.text)
    if u is None:
        return await m.answer("❌ بازیکنی با این مشخصات پیدا نشد (باید یه بار ربات رو زده باشه). دوباره بفرست یا لغو کن:", reply_markup=CANCEL_KB)
    await state.clear()
    text, markup = await act_on(m.bot, state, m.from_user.id, act, u)
    await m.answer(text, reply_markup=markup)


@router.message(StateFilter(AdminAsk.reason), F.func(_is_admin_pm), F.text)
async def got_reason(m: Message, state: FSMContext) -> None:
    uid = (await state.get_data()).get("uid")
    await state.clear()
    u = await eco.get_user(uid) if uid else None
    if u is None:
        return await m.answer("بازیکن پیدا نشد.", reply_markup=kb([b("◀️ پنل", "adm:home")]))
    reason = "" if m.text.strip() == "-" else m.text.strip()
    text = await do_ban(m.bot, m.from_user.id, u, reason)
    fresh = await eco.get_user(uid) or u
    await m.answer(text + "\n\n" + info_text(fresh), reply_markup=user_kb(fresh))


@router.message(StateFilter(AdminAsk.amount), F.func(_is_admin_pm), F.text)
async def got_amount(m: Message, state: FSMContext) -> None:
    data = await state.get_data()
    amount = parse_amount(m.text.split()[0]) if m.text.split() else None
    if not amount:
        return await m.answer("❌ مبلغ معتبر نیست. مثل <code>5000</code> یا <code>2m</code> بفرست:", reply_markup=CANCEL_KB)
    if amount > MAX_AMOUNT:
        return await m.answer(f"❌ سقف مبلغ {fa(MAX_AMOUNT)} دلاره.", reply_markup=CANCEL_KB)
    await state.clear()
    u = await eco.get_user(data.get("uid", 0))
    if u is None:
        return await m.answer("بازیکن پیدا نشد.", reply_markup=kb([b("◀️ پنل", "adm:home")]))
    text = await do_cash(m.bot, m.from_user.id, u, amount, give=data.get("mode") == "give")
    fresh = await eco.get_user(u.tg_id) or u
    await m.answer(text + "\n\n" + info_text(fresh), reply_markup=user_kb(fresh))


def _parse_gift(tokens: list[str]) -> Optional[tuple[int, int, str]]:
    if len(tokens) < 2:
        return None
    amount, uses = parse_amount(tokens[0]), parse_amount(tokens[1])
    if not amount or not uses or amount > MAX_AMOUNT or uses > 100_000:
        return None
    code = tokens[2].upper() if len(tokens) > 2 else "DZ" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    return (amount, uses, code) if re.fullmatch(r"[A-Z0-9_]{3,32}", code) else None


@router.message(StateFilter(AdminAsk.gift), F.func(_is_admin_pm), F.text)
async def got_gift(m: Message, state: FSMContext) -> None:
    parsed = _parse_gift(m.text.split())
    if parsed is None:
        return await m.answer("❌ فرمت درست نیست. مثل <code>1m 10</code> یا <code>5m 3 EID1405</code> بفرست (کد فقط حروف انگلیسی/عدد):",
                              reply_markup=CANCEL_KB)
    await state.clear()
    await m.answer(await do_gift(m.from_user.id, m.bot, *parsed), reply_markup=kb([b("◀️ پنل", "adm:home")]))


@router.message(StateFilter(AdminAsk.broadcast), F.func(_is_admin_pm), F.text)
async def got_broadcast(m: Message, state: FSMContext) -> None:
    await state.update_data(bc_text=m.html_text)
    total = await eco.col().count_documents({"banned": {"$ne": True}})
    await m.answer(f"📢 پیش‌نمایش:\n\n{m.html_text}\n\n— — —\nبه <b>{fa(total)}</b> بازیکن فرستاده می‌شه. تأیید؟",
                   reply_markup=kb([b("✅ ارسال", "adm:bcgo", SUCCESS)], [b("❌ لغو", "adm:cancel")]))


# ═════════════════════════ دستورهای سریع (پیوی یا گروه) ═════════════════════════
async def _cmd_target(m: Message, cmd: CommandObject) -> tuple[Optional[User], list[str]]:
    """کاربر هدف از ریپلای یا اولین آرگومان؛ بقیه‌ی آرگومان‌ها هم برمی‌گرده."""
    args = (cmd.args or "").split()
    rep = m.reply_to_message
    if rep is not None and rep.from_user is not None and not rep.from_user.is_bot:
        return await eco.get_user(rep.from_user.id), args
    if not args:
        return None, args
    return await _resolve(args[0]), args[1:]


async def _quick(m: Message, cmd: CommandObject, act: str) -> None:
    u, rest = await _cmd_target(m, cmd)
    if u is None:
        return await m.reply(f"❌ بازیکن پیدا نشد.\nمثال: <code>/{act} 123456789</code> یا ریپلای روی پیامش.")
    aid, bot = m.from_user.id, m.bot
    if act == "info":
        return await m.reply(info_text(u))
    if act == "ban":
        return await m.reply(await do_ban(bot, aid, u, " ".join(rest)))
    if act == "unban":
        return await m.reply(await do_unban(bot, aid, u))
    if act in ("give", "take"):
        amount = parse_amount(rest[0]) if rest else None
        if not amount or amount > MAX_AMOUNT:
            return await m.reply(f"❌ مبلغ معتبر نیست.\nمثال: <code>/{act} 123456789 5m</code>")
        return await m.reply(await do_cash(bot, aid, u, amount, give=act == "give"))
    if act == "reset":
        return await m.reply(await do_reset(bot, aid, u))
    if act == "cd":
        return await m.reply(await do_clear_cd(bot, aid, u))


def _register_quick() -> None:
    for act in ("info", "ban", "unban", "give", "take", "reset", "cd"):
        async def handler(m: Message, command: CommandObject, _act: str = act) -> None:
            await _quick(m, command, _act)
        router.message.register(handler, Command(act), F.func(_is_admin_msg))


_register_quick()


@router.message(Command("newgift"), F.func(_is_admin_pm))
async def cmd_gift(m: Message, command: CommandObject) -> None:
    parsed = _parse_gift((command.args or "").split())
    if parsed is None:
        return await m.answer("❌ مثال: <code>/newgift 1m 10</code> یا <code>/newgift 5m 3 EID1405</code>")
    await m.answer(await do_gift(m.from_user.id, m.bot, *parsed))
