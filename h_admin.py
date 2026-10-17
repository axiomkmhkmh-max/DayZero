"""Day Zero — پنل و دستورهای ادمین.

باز کردن پنل (فقط پیوی، فقط ادمین‌ها):  /admin      ·      راهنمای کامل دستورها: /ahelp
آیدی خودت رو با /myid بگیر (برای همه کار می‌کنه) و تو متغیر محیطی ADMIN_IDS یا config.py بذار.

دستورها (پیوی یا گروه؛ <کاربر> = آیدی عددی / @یوزر / ریپلای روی پیام طرف):
  /info /ban /unban /give /take /vgive /vtake /setcash /setvault /kids /house /reset /cd
  /giveitem /takeitem /itemlist /fmarry /fdivorce /dm /find /top /stats /banlist
  /newgift /gifts /delgift /giveall /broadcast /admins /addadmin /deladmin
مبلغ‌ها: 5000 · 5k · 2m · 1b
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
from aiogram.types import BotCommand, BotCommandScopeChat, CallbackQuery, InlineKeyboardMarkup, Message

import actions
import config as C
import eco
import rules
from db import Alliance, GiftCode, User
from redis_service import redis_service
from states import AdminAsk
from ui import DANGER, PRIMARY, SUCCESS, b, edit, kb
from util import en_digits, esc, fa, left, mention, norm, parse_amount, utcnow

log = structlog.get_logger("admin")
router = Router()

BANNED_KEY = "dz:banned"                    # set از آیدی بازیکن‌های بن‌شده (چک سریع)
MAX_AMOUNT = 10_000_000_000_000             # سقف منطقی برای یک بار دادن/گرفتن پول
MAX_KIDS = 1_000
MAX_ITEMS = 1_000

# ═════════════════════════ ادمین‌ها ═════════════════════════
_DYN: set[int] = set()                      # ادمین‌هایی که با /addadmin اضافه شدن (تو Mongo ذخیره‌ان)


def all_admins() -> set[int]:
    return set(C.ADMIN_IDS) | _DYN


def is_owner(uid: Optional[int]) -> bool:
    return uid is not None and uid in C.ADMIN_IDS


def is_admin(uid: Optional[int]) -> bool:
    return uid is not None and (uid in C.ADMIN_IDS or uid in _DYN)


def _is_admin_msg(m: Message) -> bool:
    return m.from_user is not None and is_admin(m.from_user.id)


def _is_admin_pm(m: Message) -> bool:
    return _is_admin_msg(m) and m.chat.type == "private"


async def load_admins() -> int:
    """موقع استارت: ادمین‌های اضافه‌شده از Mongo خونده می‌شن."""
    ids = [d["tg_id"] async for d in eco.col().find({"is_admin": True}, {"tg_id": 1})]
    _DYN.clear()
    _DYN.update(ids)
    return len(all_admins())


ADMIN_COMMANDS = [
    BotCommand(command="admin", description="🛡 پنل ادمین"),
    BotCommand(command="ahelp", description="📖 راهنمای دستورهای ادمین"),
    BotCommand(command="info", description="🔎 اطلاعات بازیکن"),
    BotCommand(command="give", description="💰 دادن پول (جیب)"),
    BotCommand(command="take", description="💸 گرفتن پول (جیب)"),
    BotCommand(command="vgive", description="🔐 دادن پول به گاوصندوق"),
    BotCommand(command="vtake", description="🔐 گرفتن از گاوصندوق"),
    BotCommand(command="setcash", description="🖊 تنظیم دقیق جیب"),
    BotCommand(command="kids", description="👶 تنظیم تعداد بچه"),
    BotCommand(command="house", description="🏠 تنظیم سطح خونه"),
    BotCommand(command="giveitem", description="🎁 دادن وسیله/نیرو/آیتم"),
    BotCommand(command="takeitem", description="🗑 گرفتن وسیله/نیرو/آیتم"),
    BotCommand(command="ban", description="🚫 بن"),
    BotCommand(command="unban", description="✅ آنبن"),
    BotCommand(command="cd", description="⏱ ریست کول‌داون (یا all)"),
    BotCommand(command="reset", description="🔄 ریست بازیکن"),
    BotCommand(command="find", description="🔍 جستجوی بازیکن"),
    BotCommand(command="top", description="🏆 پولدارترین‌ها"),
    BotCommand(command="stats", description="📊 آمار ربات"),
    BotCommand(command="giveall", description="🎁 پول به همه"),
    BotCommand(command="broadcast", description="📢 پیام همگانی"),
    BotCommand(command="newgift", description="🎁 ساخت کد هدیه"),
    BotCommand(command="alzero", description="🤝 صفر کردن بانک اتحاد"),
    BotCommand(command="addadmin", description="👑 اضافه کردن ادمین"),
]
_base_cmds: list[BotCommand] = []


async def publish_commands(bot, base: Optional[list[BotCommand]] = None, only: Optional[int] = None) -> None:
    """منوی دستورهای ادمین فقط برای خود ادمین‌ها (تو چت خصوصی‌شون) نشون داده می‌شه."""
    global _base_cmds
    if base is not None:
        _base_cmds = list(base)
    for aid in ([only] if only else sorted(all_admins())):
        try:
            await bot.set_my_commands(_base_cmds + ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=aid))
        except Exception:  # noqa: BLE001
            pass


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


# ═════════════════════════ ابزارهای کمکی ═════════════════════════
async def _notify(bot, uid: int, text: str) -> None:
    try:
        await bot.send_message(uid, text)
    except Exception:  # noqa: BLE001
        pass


async def _audit(bot, admin_id: int, text: str) -> None:
    """به بقیه‌ی ادمین‌ها خبر می‌ده که یه ادمین چه کاری کرد."""
    log.info("admin_action", admin=admin_id, action=text)
    for aid in all_admins():
        if aid != admin_id:
            await _notify(bot, aid, f"🛡 <b>ادمین</b> <code>{admin_id}</code>: {text}")


def _who(u: User) -> str:
    return f"{mention(u.tg_id, u.name)} (<code>{u.tg_id}</code>)"


def _num(tok: str) -> Optional[int]:
    """عدد صحیح ≥ ۰ (۵k · 2m هم قبوله)؛ نامعتبر = None."""
    t = en_digits(tok.strip())
    if t == "0":
        return 0
    return parse_amount(tok)


async def _resolve(text: str) -> Optional[User]:
    t = text.strip().lstrip("#")
    if t.isdigit():
        return await eco.get_user(int(t))
    m = re.match(r"^@?([A-Za-z0-9_]{4,32})$", t)
    return await eco.find_by_username(m.group(1)) if m else None


async def _reply_long(m: Message, text: str) -> None:
    """پیام‌های بلند رو تکه‌تکه می‌فرسته (سقف تلگرام ۴۰۹۶ کاراکتره)."""
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 3800:
            await m.reply(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await m.reply(chunk)


async def _scan_delete(patterns: list[str]) -> int:
    r, n = redis_service.r, 0
    for pat in patterns:
        async for k in r.scan_iter(match=pat, count=500):
            await r.delete(k)
            n += 1
    return n


# ═════════════════════════ عملیات اصلی ═════════════════════════
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


async def do_vault(bot, admin_id: int, u: User, amount: int, give: bool) -> str:
    amount = min(amount, MAX_AMOUNT)
    col = eco.col()
    if give:
        await col.update_one({"tg_id": u.tg_id}, {"$inc": {"vault": amount}})
        await _notify(bot, u.tg_id, f"🎁 ادمین <b>{fa(amount)}</b> دلار به گاوصندوقت اضافه کرد!")
        await _audit(bot, admin_id, f"{fa(amount)} دلار به گاوصندوق {_who(u)} اضافه کرد")
        return f"🔐 <b>{fa(amount)}</b> دلار به گاوصندوق {_who(u)} اضافه شد."
    fresh = await eco.get_user(u.tg_id)
    amt = min(amount, max(0, fresh.vault if fresh else 0))
    if amt <= 0 or (await col.update_one({"tg_id": u.tg_id, "vault": {"$gte": amt}}, {"$inc": {"vault": -amt}})).matched_count != 1:
        return f"🔐 گاوصندوق {_who(u)} خالیه."
    await _notify(bot, u.tg_id, f"💸 ادمین <b>{fa(amt)}</b> دلار از گاوصندوقت کم کرد.")
    await _audit(bot, admin_id, f"{fa(amt)} دلار از گاوصندوق {_who(u)} گرفت")
    return f"🔐 <b>{fa(amt)}</b> دلار از گاوصندوق {_who(u)} کم شد."


_SET_FIELD = {"setcash": ("cash", "جیب"), "setvault": ("vault", "گاوصندوق"), "kids": ("kids", "تعداد بچه"),
              "house": ("house", "سطح خونه")}


async def do_set(bot, admin_id: int, u: User, mode: str, value: int) -> str:
    """مقدار دقیق جیب / گاوصندوق / بچه / سطح خونه رو تنظیم می‌کنه."""
    field, label = _SET_FIELD[mode]
    if field in ("cash", "vault") and value > MAX_AMOUNT:
        return f"❌ سقف مبلغ {fa(MAX_AMOUNT)} دلاره."
    if field == "kids" and value > MAX_KIDS:
        return f"❌ تعداد بچه حداکثر {fa(MAX_KIDS)} می‌تونه باشه."
    if field == "house" and not 0 <= value <= C.MAX_HOUSE:
        return f"❌ سطح خونه باید بین ۰ تا {fa(C.MAX_HOUSE)} باشه."
    fresh = await eco.get_user(u.tg_id)
    if fresh is None:
        return "بازیکن پیدا نشد."
    if field in ("cash", "vault"):
        delta = value - getattr(fresh, field)
        if delta:
            await eco.col().update_one({"tg_id": u.tg_id}, {"$inc": {field: delta}})
    else:
        await eco.settle(u.tg_id)                      # درآمد قبلی با تعداد بچه‌ی قبلی حساب بشه
        await eco.col().update_one({"tg_id": u.tg_id}, {"$set": {field: value}})
    shown = fa(value)
    await _notify(bot, u.tg_id, f"🛠 ادمین {label} تو رو روی <b>{shown}</b> تنظیم کرد.")
    await _audit(bot, admin_id, f"{label} {_who(u)} رو روی {shown} گذاشت")
    return f"🖊 {label} {_who(u)} شد <b>{shown}</b>."


async def apply_amount(bot, admin_id: int, u: User, mode: str, amount: int) -> str:
    """مسیر مشترک همه‌ی عمل‌های «عددی» (هم پنل هم دستور)."""
    if mode in ("give", "take", "vgive", "vtake") and amount <= 0:
        return "❌ مبلغ باید بیشتر از صفره."
    if mode in ("give", "take", "vgive", "vtake") and amount > MAX_AMOUNT:
        return f"❌ سقف مبلغ {fa(MAX_AMOUNT)} دلاره."
    if mode == "give":
        return await do_cash(bot, admin_id, u, amount, True)
    if mode == "take":
        return await do_cash(bot, admin_id, u, amount, False)
    if mode == "vgive":
        return await do_vault(bot, admin_id, u, amount, True)
    if mode == "vtake":
        return await do_vault(bot, admin_id, u, amount, False)
    if mode in _SET_FIELD:
        return await do_set(bot, admin_id, u, mode, amount)
    return "؟"


# ───────── وسیله / نیرو / آیتم ─────────
_KIND_FIELD = {"gear": "gear", "item": "items", "force": "forces"}
_KIND_LABEL = {"gear": "وسیله‌ی فروشگاه", "item": "آیتم دارک وب", "force": "نیرو"}


def _norm_name(s: str) -> str:
    return s.replace(" ", "").replace("\u200c", "").lower()


def _find_item(query: str) -> Optional[tuple[str, str, dict]]:
    """کلید (مثل g0 · jinn · h_adv) یا اسم فارسی → (نوع, کلید, مشخصات)."""
    q = query.strip().lower()
    if not q:
        return None
    indexes = (("gear", rules.SHOP_INDEX), ("item", rules.DARK_INDEX), ("force", rules.FORCE_INDEX))
    for kind, idx in indexes:
        if q in idx:
            return kind, q, idx[q]
    qn = _norm_name(q)
    for kind, idx in indexes:
        for k, meta in idx.items():
            if _norm_name(str(meta.get("name", ""))) == qn:
                return kind, k, meta
    return None


def itemlist_text() -> str:
    def line(k: str, m: dict) -> str:
        return f"<code>{k}</code> — {m.get('emoji', '')} {esc(str(m.get('name', k)))}"
    out = ["📦 <b>لیست کلیدها</b> (برای /giveitem و /takeitem؛ کلید یا اسم فارسی)\n", "<b>🛒 وسیله‌های فروشگاه</b>"]
    out += [line(k, m) for k, m in rules.SHOP_INDEX.items()]
    out += ["", "<b>🕶 آیتم‌های دارک وب</b>"] + [line(k, m) for k, m in rules.DARK_INDEX.items()]
    out += ["", "<b>👥 نیروها</b>"] + [line(k, m) for k, m in rules.FORCE_INDEX.items()]
    return "\n".join(out)


async def do_item(bot, admin_id: int, u: User, query: str, n: int, give: bool) -> str:
    found = _find_item(query)
    if found is None:
        return f"❌ «{esc(query)}» پیدا نشد. لیست کلیدها: /itemlist"
    kind, key, meta = found
    n = max(1, min(n, MAX_ITEMS))
    field = _KIND_FIELD[kind]
    name = f"{meta.get('emoji', '')} {esc(str(meta.get('name', key)))}".strip()
    col = eco.col()
    if give:
        if kind == "gear":
            await col.update_one({"tg_id": u.tg_id}, {"$set": {f"gear.{key}": 1}})
            n = 1
        else:
            await col.update_one({"tg_id": u.tg_id}, {"$inc": {f"{field}.{key}": n}})
        await _notify(bot, u.tg_id, f"🎁 ادمین «{name}» ×{fa(n)} بهت داد!")
        await _audit(bot, admin_id, f"«{name}» ×{fa(n)} به {_who(u)} داد")
        return f"🎁 «{name}» ×{fa(n)} ({_KIND_LABEL[kind]}) به {_who(u)} داده شد."
    fresh = await eco.get_user(u.tg_id)
    cur = int(getattr(fresh, field, {}).get(key, 0)) if fresh else 0
    if cur <= 0:
        return f"🕳 {_who(u)} «{name}» نداره."
    if kind == "gear" or n >= cur:
        await col.update_one({"tg_id": u.tg_id}, {"$unset": {f"{field}.{key}": ""}})
        n = cur
    else:
        await col.update_one({"tg_id": u.tg_id}, {"$inc": {f"{field}.{key}": -n}})
    await _notify(bot, u.tg_id, f"🗑 ادمین «{name}» ×{fa(n)} از تو گرفت.")
    await _audit(bot, admin_id, f"«{name}» ×{fa(n)} از {_who(u)} گرفت")
    return f"🗑 «{name}» ×{fa(n)} از {_who(u)} گرفته شد."


# ───────── ازدواج / طلاق اجباری ─────────
async def do_force_marry(bot, admin_id: int, a: User, b_: User) -> str:
    if a.tg_id == b_.tg_id:
        return "😅 یه نفر که نمی‌تونه با خودش ازدواج کنه."
    for x in (a, b_):                                  # اگه همسر دارن اول طلاق می‌گیرن
        fx = await eco.get_user(x.tg_id)
        if fx and fx.spouse_id:
            await actions.do_divorce(fx.tg_id, fx.spouse_id)
    if not await actions.do_marry(a.tg_id, b_.tg_id):
        return "💔 نشد؛ دوباره امتحان کن."
    for x, y in ((a, b_), (b_, a)):
        await _notify(bot, x.tg_id, f"💍 ادمین شما رو با {esc(y.name)} ازدواج داد!")
    await _audit(bot, admin_id, f"{_who(a)} و {_who(b_)} رو ازدواج داد")
    return f"💍 {_who(a)} و {_who(b_)} ازدواج کردن."


async def do_force_divorce(bot, admin_id: int, u: User) -> str:
    fresh = await eco.get_user(u.tg_id)
    if fresh is None or not fresh.spouse_id:
        return f"💍 {_who(u)} متاهل نیست."
    sp = fresh.spouse_id
    await actions.do_divorce(fresh.tg_id, sp)
    for uid in (fresh.tg_id, sp):
        await _notify(bot, uid, "💔 ادمین شما رو طلاق داد.")
    await _audit(bot, admin_id, f"{_who(u)} رو از همسرش (<code>{sp}</code>) جدا کرد")
    return f"💔 {_who(u)} از همسرش (<code>{sp}</code>) جدا شد."


# ───────── کول‌داون / ریست ─────────
_CD_FIELDS = {"job_ready_at": None, "walk_ready_at": None, "pocket_ready_at": None,
              "child_ready_at": None, "attack_ready_at": None}


async def do_clear_cd(bot, admin_id: int, u: User) -> str:
    await eco.col().update_one({"tg_id": u.tg_id}, {"$set": dict(_CD_FIELDS)})
    uid = u.tg_id
    keys = [f"hack:{uid}", f"htheft:{uid}", f"cd:rmarry:{uid}"]
    if u.spouse_id:
        keys.append(f"intim:{min(uid, u.spouse_id)}:{max(uid, u.spouse_id)}")
    await redis_service.r.delete(*keys)
    await _scan_delete([f"pcd:*:{uid}:*", f"pcd:*:*:{uid}"])             # کولداون هک/دزدی روی هر نفر
    await _audit(bot, admin_id, f"کول‌داون‌های {_who(u)} رو ریست کرد")
    return f"⏱ کول‌داون‌های {_who(u)} ریست شد (کار، چرخ، جیبی، بچه، حمله، هک، دزدی، رندوم، جایزه‌ی زوجی)."


async def do_clear_cd_all(bot, admin_id: int) -> str:
    await eco.col().update_many({}, {"$set": dict(_CD_FIELDS)})
    n = await _scan_delete(["pcd:*", "intim:*", "hack:*", "htheft:*", "cd:rmarry:*", "rmrecent:*"])
    await _audit(bot, admin_id, "کول‌داون‌های همه‌ی بازیکن‌ها رو ریست کرد")
    return f"⏱ کول‌داون‌های <b>همه‌ی بازیکن‌ها</b> ریست شد ({fa(n)} تایمر پاک شد)."


async def do_reset(bot, admin_id: int, u: User) -> str:
    """پیشرفت بازیکن رو به حالت تازه‌وارد برمی‌گردونه (اسم، یوزرنیم، تاریخ ثبت‌نام، وضعیت بن و ادمین بودن می‌مونن)."""
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
        "banned", "ban_reason", "banned_at", "is_admin"})
    now = utcnow()
    fresh["income_since"] = now
    fresh["shield_until"] = now + timedelta(hours=C.NEWBIE_SHIELD_HOURS)
    await col.update_one({"tg_id": uid}, {"$set": fresh})
    keys = [f"hack:{uid}", f"htheft:{uid}", f"cd:rmarry:{uid}"]
    if u.spouse_id:
        keys.append(f"intim:{min(uid, u.spouse_id)}:{max(uid, u.spouse_id)}")
    await redis_service.r.delete(*keys)
    await _scan_delete([f"pcd:*:{uid}:*", f"pcd:*:*:{uid}"])
    await _notify(bot, uid, "🔄 پیشرفتت توسط ادمین ریست شد.")
    await _audit(bot, admin_id, f"ریست کامل {_who(u)}")
    return f"🔄 {_who(u)} کامل ریست شد (پول اولیه {fa(C.START_CASH)} دلار)."


async def do_give_all(bot, admin_id: int, amount: int) -> str:
    amount = min(amount, MAX_AMOUNT)
    r = await eco.col().update_many({"banned": {"$ne": True}}, {"$inc": {"cash": amount}})
    await _audit(bot, admin_id, f"به همه‌ی بازیکن‌ها {fa(amount)} دلار داد ({fa(r.modified_count)} نفر)")
    return f"🎁 به <b>{fa(r.modified_count)}</b> بازیکن هر کدوم <b>{fa(amount)}</b> دلار داده شد."


# ───────── کد هدیه ─────────
async def do_gift(admin_id: int, bot, amount: int, uses: int, code: str) -> str:
    code = code.upper()
    if await GiftCode.get_motor_collection().find_one({"code": code}):
        return f"🎁 کد <code>{code}</code> از قبل وجود داره."
    await GiftCode.get_motor_collection().insert_one(
        {"code": code, "amount": amount, "max_uses": uses, "used_by": [], "created_at": utcnow()})
    await _audit(bot, admin_id, f"کد هدیه <code>{code}</code> ساخت ({fa(amount)} دلار، {fa(uses)} بار)")
    return (f"🎁 کد هدیه ساخته شد:\n<code>{code}</code>\n"
            f"💰 {fa(amount)} دلار · 👥 {fa(uses)} بار مصرف\nبازیکن‌ها تو پیوی ربات: <code>کد هدیه {code}</code>")


async def gifts_text() -> str:
    rows = await GiftCode.get_motor_collection().find({}).sort([("created_at", -1)]).limit(40).to_list(40)
    if not rows:
        return "🎁 هیچ کد هدیه‌ای نیست. بساز: /newgift 1m 10"
    lines = [f"• <code>{esc(d['code'])}</code> — {fa(d.get('amount', 0))} دلار — {fa(len(d.get('used_by', [])))}/{fa(d.get('max_uses', 1))} بار"
             for d in rows]
    return "🎁 <b>کدهای هدیه</b> (حذف: <code>/delgift کد</code>)\n\n" + "\n".join(lines)


# ───────── پیام همگانی ─────────
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


# ───────── مدیریت ادمین‌ها ─────────
async def do_add_admin(bot, admin_id: int, u: User) -> str:
    if is_admin(u.tg_id):
        return f"👑 {_who(u)} از قبل ادمینه."
    await eco.col().update_one({"tg_id": u.tg_id}, {"$set": {"is_admin": True}})
    _DYN.add(u.tg_id)
    await _notify(bot, u.tg_id, "👑 تو ادمین ربات شدی!\nپنل: /admin  ·  راهنما: /ahelp")
    await publish_commands(bot, only=u.tg_id)
    await _audit(bot, admin_id, f"{_who(u)} رو ادمین کرد")
    return f"👑 {_who(u)} ادمین شد."


async def do_del_admin(bot, admin_id: int, u: User) -> str:
    if not is_owner(admin_id):
        return "⛔ فقط ادمین‌های اصلی (ADMIN_IDS) می‌تونن ادمین حذف کنن."
    if is_owner(u.tg_id):
        return "⛔ ادمین اصلی رو نمی‌شه حذف کرد."
    if u.tg_id not in _DYN:
        return f"{_who(u)} ادمین نیست."
    await eco.col().update_one({"tg_id": u.tg_id}, {"$set": {"is_admin": False}})
    _DYN.discard(u.tg_id)
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=u.tg_id))
    except Exception:  # noqa: BLE001
        pass
    await _notify(bot, u.tg_id, "ادمین بودنت برداشته شد.")
    await _audit(bot, admin_id, f"ادمین بودن {_who(u)} رو برداشت")
    return f"✅ {_who(u)} دیگه ادمین نیست."


async def admins_text() -> str:
    lines = []
    for uid in sorted(all_admins()):
        u = await eco.get_user(uid)
        tag = "👑 اصلی" if is_owner(uid) else "🛡 اضافه‌شده"
        lines.append(f"• {tag} — {esc(u.name) if u else '—'} (<code>{uid}</code>)")
    return "👑 <b>ادمین‌ها</b>\n\n" + ("\n".join(lines) if lines else "—") + "\n\nاضافه: /addadmin · حذف: /deladmin"


# ═════════════════════════ نمایش ═════════════════════════
def _fmt_dt(d) -> str:
    return d.strftime("%Y-%m-%d %H:%M") + " UTC" if d else "—"


def _compact(d: dict, limit: int = 12) -> str:
    items = [f"{k}×{v}" for k, v in d.items() if v]
    return "، ".join(items[:limit]) + (" …" if len(items) > limit else "") if items else "—"


def info_text(u: User) -> str:
    atk_h = f"@{u.username}" if u.username else "—"
    spouse = f"<code>{u.spouse_id}</code>" if u.spouse_id else "—"
    ban = f"🚫 <b>بن‌شده</b>" + (f" ({esc(u.ban_reason)})" if u.ban_reason else "") if u.banned else "✅ آزاد"
    role = " 👑 ادمین" if is_admin(u.tg_id) else ""
    return (f"👤 <b>{esc(u.name)}</b>{role}  <code>{u.tg_id}</code>\n"
            f"یوزرنیم: {atk_h}   وضعیت: {ban}\n\n"
            f"💵 جیب: <b>{fa(u.cash)}</b>\n🔐 گاوصندوق: <b>{fa(u.vault)}</b>\n"
            f"📈 ثروت کل: <b>{fa(eco.net_worth(u))}</b>\n\n"
            f"🏠 خونه: {fa(u.house)}   👶 بچه: {fa(u.kids)}   💍 همسر: {spouse}\n"
            f"🏪 کسب‌وکار: {fa(len(u.businesses))}   🤝 اتحاد: {fa(u.alliance_id) if u.alliance_id else '—'}   ⚔️ شیفت‌ها: {fa(u.shifts)}\n"
            f"👥 نیروها: {_compact(u.forces)}\n🛒 وسیله‌ها: {_compact(u.gear)}\n🕶 آیتم‌ها: {_compact(u.items)}\n"
            f"🕒 ثبت‌نام: {_fmt_dt(u.created_at)}\n🕒 آخرین فعالیت: {_fmt_dt(u.last_seen)}")


ADMIN_HELP = """🛡 <b>راهنمای دستورهای ادمین</b>
<i>(<code>کاربر</code> = آیدی عددی · @یوزر · یا ریپلای روی پیامش؛ مبلغ: 5000 · 5k · 2m · 1b)</i>

<b>👤 بازیکن</b>
<code>/info کاربر</code> · <code>/find اسم</code> · <code>/top 10</code>
<code>/ban کاربر دلیل</code> · <code>/unban کاربر</code> · <code>/banlist</code>
<code>/reset کاربر</code> (ریست کامل) · <code>/cd کاربر</code> (کولداون) · <code>/cd all</code> (همه)
<code>/dm کاربر متن</code> (پیام خصوصی از ربات)

<b>💰 پول</b>
<code>/give کاربر 5m</code> · <code>/take کاربر 5m</code> (جیب)
<code>/vgive کاربر 5m</code> · <code>/vtake کاربر 5m</code> (گاوصندوق)
<code>/setcash کاربر 1m</code> · <code>/setvault کاربر 1m</code> (مقدار دقیق)
<code>/giveall 100k</code> (پول به همه، با تأیید)

<b>🏠 دارایی</b>
<code>/kids کاربر 8</code> · <code>/house کاربر 5</code>
<code>/giveitem کاربر کلید [تعداد]</code> · <code>/takeitem کاربر کلید [تعداد]</code>
مثال: <code>/giveitem @ali g2 5</code> · <code>/giveitem @ali jinn 3</code> · کلیدها: <code>/itemlist</code>
<code>/fmarry کاربر۱ کاربر۲</code> (ازدواج اجباری) · <code>/fdivorce کاربر</code>

<b>🎁 کد هدیه و پیام</b>
<code>/newgift 1m 10 [کد]</code> · <code>/gifts</code> · <code>/delgift کد</code>
<code>/broadcast متن</code> (پیام همگانی، با تأیید، پیوی)

<b>👑 ادمین‌ها و آمار</b>
<code>/stats</code> · <code>/admins</code> · <code>/addadmin کاربر</code> · <code>/deladmin کاربر</code> (فقط ادمین اصلی)
<b>🤝 اتحاد</b>: <code>/alzero</code> (بانک همه‌ی اتحادها صفر) · <code>/alzero اسم</code> (فقط یکی) · از پنل هم می‌شه\n<code>/admin</code> پنل دکمه‌ای (پیوی) · <code>/myid</code> آیدی خودت"""


def panel_kb() -> InlineKeyboardMarkup:
    return kb(
        [b("📊 آمار", "adm:stats", PRIMARY), b("🔎 اطلاعات بازیکن", "adm:ask:info", PRIMARY)],
        [b("🚫 بن", "adm:ask:ban", DANGER), b("✅ آنبن", "adm:ask:unban", SUCCESS)],
        [b("💰 دادن پول", "adm:ask:give", SUCCESS), b("💸 گرفتن پول", "adm:ask:take", DANGER)],
        [b("🔐 دادن به گاوصندوق", "adm:ask:vgive", SUCCESS), b("🔐 گرفتن از گاوصندوق", "adm:ask:vtake", DANGER)],
        [b("🖊 تنظیم جیب", "adm:ask:setcash", PRIMARY), b("🖊 تنظیم گاوصندوق", "adm:ask:setvault", PRIMARY)],
        [b("👶 تنظیم بچه", "adm:ask:kids", PRIMARY), b("🏠 تنظیم خونه", "adm:ask:house", PRIMARY)],
        [b("💔 طلاق اجباری", "adm:ask:fdivorce", DANGER), b("⏱ ریست کول‌داون", "adm:ask:cd", PRIMARY)],
        [b("🔄 ریست بازیکن", "adm:ask:reset", DANGER), b("🎁 پول به همه", "adm:giveall", SUCCESS)],
        [b("🎁 ساخت کد هدیه", "adm:gift", SUCCESS), b("📋 کدهای هدیه", "adm:gifts", PRIMARY)],
        [b("📢 پیام همگانی", "adm:bc", PRIMARY), b("📋 لیست بن‌شده‌ها", "adm:banlist", PRIMARY)],
        [b("🏆 پولدارترین‌ها", "adm:top", PRIMARY), b("👑 ادمین‌ها", "adm:admins", PRIMARY)],
        [b("⏱ ریست کول‌داون همه", "adm:cdall", DANGER), b("📖 راهنمای دستورها", "adm:help", PRIMARY)],
        [b("🤝 اتحادها (بانک)", "adm:al", PRIMARY)],
    )


async def alliances_view() -> tuple[str, InlineKeyboardMarkup]:
    als = await Alliance.find_all().sort("-bank").to_list()
    lines = ["🤝 <b>اتحادها</b>\n"]
    rows = []
    for al in als:
        n = await eco.col().count_documents({"alliance_id": al.aid})
        arm = sum(int(v) for v in (al.armory or {}).values())
        lines.append(f"• <b>{esc(al.name)}</b> — سطح {fa(al.level)} · {fa(n)} عضو · 🏦 {fa(al.bank)} · 🏛 {fa(arm)} نیرو")
        rows.append([b(f"🧹 بانک «{al.name}»", f"adm:alz:{al.aid}", DANGER), b("🏛 انبار", f"adm:alr:{al.aid}", DANGER)])
    if not als:
        lines.append("هیچ اتحادی نیست.")
    else:
        rows.append([b("🧹 صفر کردن بانک همه‌ی اتحادها", "adm:alzall", DANGER)])
    rows.append([b("◀️ پنل", "adm:home")])
    return "\n".join(lines), kb(*rows)


async def zero_banks(aid: Optional[int] = None) -> tuple[int, int]:
    """بانک یه اتحاد (یا همه) صفر می‌شه. خروجی: (تعداد اتحاد، مجموع پولِ پاک‌شده)."""
    flt = {"aid": aid} if aid is not None else {}
    total, cnt = 0, 0
    async for d in Alliance.get_motor_collection().find(flt, {"aid": 1, "bank": 1}):
        total += int(d.get("bank", 0))
        cnt += 1
    await Alliance.get_motor_collection().update_many(flt, {"$set": {"bank": 0}})
    return cnt, total


PANEL_TEXT = "🛡 <b>پنل ادمین Day Zero</b>\n\nیکی از گزینه‌ها رو انتخاب کن:\n<i>دستورهای کامل‌تر (وسیله، نیرو، ازدواج اجباری و…): /ahelp</i>"
CANCEL_KB = kb([b("❌ لغو", "adm:cancel")])
HOME_KB = kb([b("◀️ پنل", "adm:home")])


def user_kb(u: User) -> InlineKeyboardMarkup:
    i = u.tg_id
    ban_btn = b("✅ آنبن", f"adm:u:unban:{i}", SUCCESS) if u.banned else b("🚫 بن", f"adm:u:ban:{i}", DANGER)
    rows = [
        [ban_btn],
        [b("💰 دادن پول", f"adm:u:give:{i}", SUCCESS), b("💸 گرفتن پول", f"adm:u:take:{i}", DANGER)],
        [b("🔐 دادن گاوصندوق", f"adm:u:vgive:{i}", SUCCESS), b("🔐 گرفتن گاوصندوق", f"adm:u:vtake:{i}", DANGER)],
        [b("👶 بچه", f"adm:u:kids:{i}", PRIMARY), b("🏠 خونه", f"adm:u:house:{i}", PRIMARY)],
        [b("🔄 ریست", f"adm:u:reset:{i}", DANGER), b("⏱ کول‌داون", f"adm:u:cd:{i}", PRIMARY)],
    ]
    if u.spouse_id:
        rows.append([b("💔 طلاق اجباری", f"adm:u:fdivorce:{i}", DANGER)])
    rows.append([b("◀️ پنل", "adm:home")])
    return kb(*rows)


ASK_TITLES = {"info": "🔎 اطلاعات بازیکن", "ban": "🚫 بن", "unban": "✅ آنبن", "give": "💰 دادن پول",
              "take": "💸 گرفتن پول", "vgive": "🔐 دادن به گاوصندوق", "vtake": "🔐 گرفتن از گاوصندوق",
              "setcash": "🖊 تنظیم جیب", "setvault": "🖊 تنظیم گاوصندوق", "kids": "👶 تنظیم تعداد بچه",
              "house": "🏠 تنظیم سطح خونه", "fdivorce": "💔 طلاق اجباری", "reset": "🔄 ریست بازیکن",
              "cd": "⏱ ریست کول‌داون"}
AMOUNT_ACTS = ("give", "take", "vgive", "vtake", "setcash", "setvault", "kids", "house")
AMOUNT_PROMPTS = {
    "give": "💰 چقدر بدم به {w}؟", "take": "💸 چقدر از جیبِ {w} بگیرم؟", "vgive": "🔐 چقدر بذارم تو گاوصندوقِ {w}؟",
    "vtake": "🔐 چقدر از گاوصندوقِ {w} بگیرم؟", "setcash": "🖊 جیبِ {w} رو روی چه مبلغی بذارم؟",
    "setvault": "🖊 گاوصندوقِ {w} رو روی چه مبلغی بذارم؟", "kids": "👶 تعداد بچه‌های {w} چندتا بشه؟",
    "house": "🏠 سطح خونه‌ی {w} چند بشه؟",
}


async def stats_text() -> str:
    col = eco.col()
    total = await col.count_documents({})
    banned = await col.count_documents({"banned": True})
    married = await col.count_documents({"spouse_id": {"$ne": None}})
    day = await col.count_documents({"last_seen": {"$gte": utcnow() - timedelta(days=1)}})
    agg = await col.aggregate([{"$group": {"_id": None, "cash": {"$sum": "$cash"}, "vault": {"$sum": "$vault"},
                                           "kids": {"$sum": "$kids"}}}]).to_list(1)
    cash, vault, kids = (agg[0]["cash"], agg[0]["vault"], agg[0]["kids"]) if agg else (0, 0, 0)
    return (f"📊 <b>آمار ربات</b>\n\n👥 کل بازیکن‌ها: <b>{fa(total)}</b>\n🟢 فعال ۲۴ ساعت اخیر: <b>{fa(day)}</b>\n"
            f"🚫 بن‌شده: <b>{fa(banned)}</b>   💍 متاهل: <b>{fa(married)}</b>   👶 کل بچه‌ها: <b>{fa(kids)}</b>\n\n"
            f"💵 مجموع جیب‌ها: <b>{fa(cash)}</b>\n🔐 مجموع گاوصندوق‌ها: <b>{fa(vault)}</b>\n\n"
            f"🏆 پولدارترین‌ها: /top")


async def top_text(n: int = 10) -> str:
    n = max(1, min(n, 30))
    rows = await eco.col().aggregate([
        {"$addFields": {"_t": {"$add": ["$cash", "$vault"]}}}, {"$sort": {"_t": -1}}, {"$limit": n},
        {"$project": {"tg_id": 1, "name": 1, "cash": 1, "vault": 1, "_t": 1}}]).to_list(n)
    lines = [f"{i}. {esc(d.get('name', '?'))} (<code>{d['tg_id']}</code>) — <b>{fa(d['_t'])}</b>" for i, d in enumerate(rows, 1)]
    return f"🏆 <b>{fa(n)} نفر پولدار (جیب + گاوصندوق)</b>\n\n" + ("\n".join(lines) or "—")


async def banlist_text() -> str:
    rows = await eco.col().find({"banned": True}, {"tg_id": 1, "name": 1, "ban_reason": 1}).limit(40).to_list(40)
    if not rows:
        return "📋 هیچ بازیکنی بن نیست."
    lines = [f"• {esc(d.get('name', '?'))} — <code>{d['tg_id']}</code>" + (f" — {esc(d['ban_reason'])}" if d.get("ban_reason") else "")
             for d in rows]
    return "📋 <b>بن‌شده‌ها</b>\n\n" + "\n".join(lines)


async def find_text(query: str) -> str:
    q = query.strip().lstrip("@")
    if not q:
        return "❌ مثال: <code>/find علی</code>"
    rx = {"$regex": re.escape(q), "$options": "i"}
    rows = await eco.col().find({"$or": [{"name": rx}, {"username": rx}]}, {"tg_id": 1, "name": 1, "username": 1, "cash": 1}).limit(15).to_list(15)
    if not rows:
        return "🔍 چیزی پیدا نشد."
    lines = [f"• {esc(d.get('name', '?'))}" + (f" @{d['username']}" if d.get("username") else "") + f" — <code>{d['tg_id']}</code>"
             for d in rows]
    return f"🔍 <b>نتیجه‌ی جستجو</b>\n\n" + "\n".join(lines)


# ═════════════════════════ مسیردهی یک عمل روی یک بازیکن ═════════════════════════
async def act_on(bot, state: FSMContext, admin_id: int, act: str, u: User) -> tuple[str, InlineKeyboardMarkup]:
    """یه کار رو روی بازیکن انجام می‌ده (یا از ادمین ورودی بعدی می‌خواد). خروجی: (متن, دکمه‌ها)"""
    uid = u.tg_id
    if act == "info":
        return info_text(u), user_kb(u)
    if act == "ban":
        if is_admin(uid):
            return "⛔ ادمین رو نمی‌شه بن کرد.", HOME_KB
        if u.banned:
            return f"{_who(u)} از قبل بن شده.", user_kb(u)
        await state.set_state(AdminAsk.reason)
        await state.update_data(uid=uid)
        return f"🚫 دلیل بن {_who(u)} رو بنویس (یا <code>-</code> برای بدون دلیل):", CANCEL_KB
    if act == "unban":
        text = await do_unban(bot, admin_id, u)
    elif act in AMOUNT_ACTS:
        await state.set_state(AdminAsk.amount)
        await state.update_data(uid=uid, mode=act)
        hint = "عدد رو بنویس" if act in ("kids", "house") else "مبلغ رو بنویس: <code>5000</code> · <code>5k</code> · <code>2m</code> · <code>1b</code>"
        return f"{AMOUNT_PROMPTS[act].format(w=_who(u))}\n{hint}", CANCEL_KB
    elif act == "fdivorce":
        text = await do_force_divorce(bot, admin_id, u)
    elif act == "reset":
        return (f"⚠️ <b>ریست کامل {_who(u)}؟</b>\nپول، خونه، بچه، کسب‌وکار، نیروها، اتحاد و همه‌چیزش پاک می‌شه و برمی‌گرده به حالت تازه‌وارد. "
                f"این کار برگشت نداره!"), kb([b("✅ بله، ریست کن", f"adm:rs:{uid}", DANGER)], [b("❌ لغو", "adm:cancel")])
    elif act == "cd":
        text = await do_clear_cd(bot, admin_id, u)
    else:
        return "؟", HOME_KB
    fresh = await eco.get_user(uid) or u
    return text + "\n\n" + info_text(fresh), user_kb(fresh)


# ═════════════════════════ پنل: دکمه‌ها ═════════════════════════
@router.message(Command("myid", "id"))
async def cmd_myid(m: Message) -> None:
    """آیدی عددی تلگرام (برای گذاشتن تو ADMIN_IDS) — برای همه کار می‌کنه."""
    if m.from_user is None:
        return
    extra = f"\nآیدی این چت: <code>{m.chat.id}</code>" if m.chat.type != "private" else ""
    await m.reply(f"🆔 آیدی تو: <code>{m.from_user.id}</code>{extra}")


@router.message(Command("admin"), F.func(_is_admin_msg))
async def cmd_admin(m: Message, state: FSMContext) -> None:
    if m.chat.type != "private":
        return await m.reply("🛡 پنل ادمین رو تو پیوی ربات باز کن: /admin\n(دستورهای سریع تو گروه هم کار می‌کنن: /ahelp)")
    await state.clear()
    await m.answer(PANEL_TEXT, reply_markup=panel_kb())


@router.message(Command("alzero"), F.func(_is_admin_msg))
async def cmd_alzero(m: Message, command: CommandObject) -> None:
    """/alzero → بانک همه‌ی اتحادها صفر؛ /alzero اسم‌اتحاد → فقط همون."""
    name = (command.args or "").strip()
    if not name:
        cnt, total = await zero_banks()
        return await m.reply(f"✅ بانک {fa(cnt)} اتحاد صفر شد (جمعاً 🏦 {fa(total)} پاک شد).")
    al = await Alliance.find_one(Alliance.name_key == norm(name))
    if al is None:
        return await m.reply("اتحادی با این اسم پیدا نشد.")
    _, total = await zero_banks(al.aid)
    await m.reply(f"✅ بانک «{esc(al.name)}» صفر شد (🏦 {fa(total)} پاک شد).")


@router.message(Command("ahelp", "adminhelp"), F.func(_is_admin_msg))
async def cmd_ahelp(m: Message) -> None:
    await m.reply(ADMIN_HELP)


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
        return await edit(cb, await banlist_text(), HOME_KB)
    if op == "top":
        return await edit(cb, await top_text(15), kb([b("🔄 تازه‌سازی", "adm:top", PRIMARY)], [b("◀️ پنل", "adm:home")]))
    if op == "admins":
        return await edit(cb, await admins_text(), HOME_KB)
    if op == "gifts":
        return await edit(cb, await gifts_text(), HOME_KB)
    if op == "help":
        return await edit(cb, ADMIN_HELP, HOME_KB)
    if op == "cdall":
        return await edit(cb, "⚠️ کول‌داون <b>همه‌ی</b> بازیکن‌ها ریست بشه؟",
                          kb([b("✅ بله", "adm:cdallgo", DANGER)], [b("❌ لغو", "adm:cancel")]))
    if op == "cdallgo":
        return await edit(cb, await do_clear_cd_all(bot, aid), HOME_KB)
    if op == "al":
        text, markup = await alliances_view()
        return await edit(cb, text, markup)
    if op == "alz":
        cnt, total = await zero_banks(int(parts[2]))
        text, markup = await alliances_view()
        return await edit(cb, f"✅ بانک صفر شد (🏦 {fa(total)} پاک شد).\n\n" + text, markup)
    if op == "alr":
        await Alliance.get_motor_collection().update_one({"aid": int(parts[2])}, {"$set": {"armory": {}}})
        text, markup = await alliances_view()
        return await edit(cb, "✅ انبار اتحاد خالی شد.\n\n" + text, markup)
    if op == "alzall":
        return await edit(cb, "⚠️ بانک <b>همه‌ی</b> اتحادها صفر بشه؟",
                          kb([b("✅ بله، صفر کن", "adm:alzallgo", DANGER)], [b("❌ لغو", "adm:al")]))
    if op == "alzallgo":
        cnt, total = await zero_banks()
        text, markup = await alliances_view()
        return await edit(cb, f"✅ بانک {fa(cnt)} اتحاد صفر شد (جمعاً 🏦 {fa(total)} پاک شد).\n\n" + text, markup)
    if op == "ask":                                    # از منوی اصلی: اول آیدی/یوزرنیم می‌خوایم
        act = parts[2]
        await state.set_state(AdminAsk.target)
        await state.update_data(act=act)
        return await edit(cb, f"<b>{ASK_TITLES.get(act, act)}</b>\n\n🎯 <b>آیدی عددی</b> یا <b>@یوزرنیم</b> بازیکن رو بفرست:", CANCEL_KB)
    if op == "u":                                      # از کارت بازیکن: آیدی معلومه
        act, uid = parts[2], int(parts[3])
        u = await eco.get_user(uid)
        if u is None:
            return await edit(cb, "بازیکن پیدا نشد.", HOME_KB)
        await state.clear()
        text, markup = await act_on(bot, state, aid, act, u)
        return await edit(cb, text, markup)
    if op == "rs":                                     # تأیید ریست
        u = await eco.get_user(int(parts[2]))
        await state.clear()
        if u is None:
            return await edit(cb, "بازیکن پیدا نشد.", HOME_KB)
        text = await do_reset(bot, aid, u)
        fresh = await eco.get_user(u.tg_id) or u
        return await edit(cb, text + "\n\n" + info_text(fresh), user_kb(fresh))
    if op == "giveall":
        await state.set_state(AdminAsk.amount)
        await state.update_data(mode="giveall")
        return await edit(cb, "🎁 <b>پول به همه</b>\n\nچقدر به هر بازیکن (غیر بن‌شده) بدم؟ <code>100k</code> · <code>1m</code>", CANCEL_KB)
    if op == "gaok":                                   # تأیید پول به همه
        await state.clear()
        return await edit(cb, await do_give_all(bot, aid, int(parts[2])), HOME_KB)
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
            return await edit(cb, "متنی پیدا نشد؛ دوباره امتحان کن.", HOME_KB)
        asyncio.create_task(_broadcast(bot, aid, text))
        return await edit(cb, "📢 ارسال شروع شد؛ وقتی تموم بشه گزارش می‌گیری.", HOME_KB)


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
        return await m.answer("بازیکن پیدا نشد.", reply_markup=HOME_KB)
    reason = "" if m.text.strip() == "-" else m.text.strip()
    text = await do_ban(m.bot, m.from_user.id, u, reason)
    fresh = await eco.get_user(uid) or u
    await m.answer(text + "\n\n" + info_text(fresh), reply_markup=user_kb(fresh))


@router.message(StateFilter(AdminAsk.amount), F.func(_is_admin_pm), F.text)
async def got_amount(m: Message, state: FSMContext) -> None:
    data = await state.get_data()
    mode = data.get("mode", "give")
    amount = _num(m.text.split()[0]) if m.text.split() else None
    if amount is None:
        return await m.answer("❌ عدد معتبر نیست. مثل <code>5000</code> یا <code>2m</code> بفرست:", reply_markup=CANCEL_KB)
    if mode == "giveall":
        if amount <= 0 or amount > MAX_AMOUNT:
            return await m.answer(f"❌ مبلغ باید بین ۱ تا {fa(MAX_AMOUNT)} باشه:", reply_markup=CANCEL_KB)
        await state.clear()
        total = await eco.col().count_documents({"banned": {"$ne": True}})
        return await m.answer(f"🎁 به <b>{fa(total)}</b> بازیکن هر کدوم <b>{fa(amount)}</b> دلار داده بشه؟",
                              reply_markup=kb([b("✅ بده", f"adm:gaok:{amount}", SUCCESS)], [b("❌ لغو", "adm:cancel")]))
    await state.clear()
    u = await eco.get_user(data.get("uid", 0))
    if u is None:
        return await m.answer("بازیکن پیدا نشد.", reply_markup=HOME_KB)
    text = await apply_amount(m.bot, m.from_user.id, u, mode, amount)
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
    await m.answer(await do_gift(m.from_user.id, m.bot, *parsed), reply_markup=HOME_KB)


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


_USAGE = {
    "vgive": "/vgive 123456789 5m", "vtake": "/vtake 123456789 5m", "setcash": "/setcash 123456789 1m",
    "setvault": "/setvault 123456789 1m", "kids": "/kids 123456789 8", "house": "/house 123456789 5",
    "give": "/give 123456789 5m", "take": "/take 123456789 5m",
}


async def _quick(m: Message, cmd: CommandObject, act: str) -> None:
    if act == "cd" and (cmd.args or "").strip().lower() in ("all", "همه"):
        return await m.reply(await do_clear_cd_all(m.bot, m.from_user.id))
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
    if act in AMOUNT_ACTS:
        amount = _num(rest[0]) if rest else None
        if amount is None:
            return await m.reply(f"❌ عدد/مبلغ معتبر نیست.\nمثال: <code>{_USAGE[act]}</code>")
        return await m.reply(await apply_amount(bot, aid, u, act, amount))
    if act == "reset":
        return await m.reply(await do_reset(bot, aid, u))
    if act == "cd":
        return await m.reply(await do_clear_cd(bot, aid, u))


def _register_quick() -> None:
    for act in ("info", "ban", "unban", "give", "take", "vgive", "vtake", "setcash", "setvault", "kids", "house", "reset", "cd"):
        async def handler(m: Message, command: CommandObject, _act: str = act) -> None:
            await _quick(m, command, _act)
        router.message.register(handler, Command(act), F.func(_is_admin_msg))


_register_quick()


@router.message(Command("giveitem", "takeitem"), F.func(_is_admin_msg))
async def cmd_item(m: Message, command: CommandObject) -> None:
    give = command.command == "giveitem"
    u, rest = await _cmd_target(m, command)
    if u is None or not rest:
        return await m.reply(f"❌ مثال: <code>/{command.command} @یوزر g2 5</code>\nکلیدها: /itemlist")
    n = 1
    if len(rest) > 1 and (v := _num(rest[-1])):
        n, rest = v, rest[:-1]
    await m.reply(await do_item(m.bot, m.from_user.id, u, " ".join(rest), n, give))


@router.message(Command("itemlist", "items"), F.func(_is_admin_msg))
async def cmd_itemlist(m: Message) -> None:
    await _reply_long(m, itemlist_text())


@router.message(Command("fmarry"), F.func(_is_admin_msg))
async def cmd_fmarry(m: Message, command: CommandObject) -> None:
    first, rest = await _cmd_target(m, command)
    second = await _resolve(rest[0]) if rest else None
    if first is None or second is None:
        return await m.reply("❌ مثال: <code>/fmarry 111 222</code> (یا ریپلای روی نفر اول + <code>/fmarry 222</code>)")
    await m.reply(await do_force_marry(m.bot, m.from_user.id, first, second))


@router.message(Command("fdivorce"), F.func(_is_admin_msg))
async def cmd_fdivorce(m: Message, command: CommandObject) -> None:
    u, _ = await _cmd_target(m, command)
    if u is None:
        return await m.reply("❌ مثال: <code>/fdivorce 123456789</code> یا ریپلای روی پیامش.")
    await m.reply(await do_force_divorce(m.bot, m.from_user.id, u))


@router.message(Command("dm"), F.func(_is_admin_msg))
async def cmd_dm(m: Message, command: CommandObject) -> None:
    u, rest = await _cmd_target(m, command)
    if u is None or not rest:
        return await m.reply("❌ مثال: <code>/dm 123456789 سلام!</code> یا ریپلای روی پیامش + <code>/dm متن</code>")
    try:
        await m.bot.send_message(u.tg_id, f"📩 <b>پیام از طرف ادمین:</b>\n\n{esc(' '.join(rest))}")
    except Exception:  # noqa: BLE001
        return await m.reply("😕 نتونستم پیام بدم (شاید ربات رو بلاک کرده).")
    await m.reply(f"✅ پیام برای {_who(u)} رفت.")


@router.message(Command("find", "search"), F.func(_is_admin_msg))
async def cmd_find(m: Message, command: CommandObject) -> None:
    await m.reply(await find_text(command.args or ""))


@router.message(Command("top"), F.func(_is_admin_msg))
async def cmd_top(m: Message, command: CommandObject) -> None:
    n = _num((command.args or "10").split()[0]) or 10
    await m.reply(await top_text(n))


@router.message(Command("stats"), F.func(_is_admin_msg))
async def cmd_stats(m: Message) -> None:
    await m.reply(await stats_text())


@router.message(Command("banlist"), F.func(_is_admin_msg))
async def cmd_banlist(m: Message) -> None:
    await m.reply(await banlist_text())


@router.message(Command("giveall"), F.func(_is_admin_msg))
async def cmd_giveall(m: Message, command: CommandObject) -> None:
    if m.chat.type != "private":
        return await m.reply("🎁 این دستور فقط تو پیوی ربات کار می‌کنه (که اشتباهی تو گروه نزنی).")
    amount = _num((command.args or "").split()[0]) if (command.args or "").split() else None
    if not amount or amount > MAX_AMOUNT:
        return await m.reply("❌ مثال: <code>/giveall 100k</code>")
    total = await eco.col().count_documents({"banned": {"$ne": True}})
    await m.reply(f"🎁 به <b>{fa(total)}</b> بازیکن هر کدوم <b>{fa(amount)}</b> دلار داده بشه؟",
                  reply_markup=kb([b("✅ بده", f"adm:gaok:{amount}", SUCCESS)], [b("❌ لغو", "adm:cancel")]))


@router.message(Command("broadcast", "bc"), F.func(_is_admin_msg))
async def cmd_broadcast(m: Message, command: CommandObject, state: FSMContext) -> None:
    if m.chat.type != "private":
        return await m.reply("📢 پیام همگانی رو تو پیوی ربات بفرست.")
    text = (command.args or "").strip()
    if not text:
        return await m.reply("❌ مثال: <code>/broadcast متن پیام</code> (یا از پنل: /admin ← 📢 پیام همگانی)")
    await state.update_data(bc_text=text)
    total = await eco.col().count_documents({"banned": {"$ne": True}})
    await m.answer(f"📢 پیش‌نمایش:\n\n{text}\n\n— — —\nبه <b>{fa(total)}</b> بازیکن فرستاده می‌شه. تأیید؟",
                   reply_markup=kb([b("✅ ارسال", "adm:bcgo", SUCCESS)], [b("❌ لغو", "adm:cancel")]))


@router.message(Command("newgift"), F.func(_is_admin_msg))
async def cmd_gift(m: Message, command: CommandObject) -> None:
    parsed = _parse_gift((command.args or "").split())
    if parsed is None:
        return await m.reply("❌ مثال: <code>/newgift 1m 10</code> یا <code>/newgift 5m 3 EID1405</code>")
    await m.reply(await do_gift(m.from_user.id, m.bot, *parsed))


@router.message(Command("gifts"), F.func(_is_admin_msg))
async def cmd_gifts(m: Message) -> None:
    await m.reply(await gifts_text())


@router.message(Command("delgift"), F.func(_is_admin_msg))
async def cmd_delgift(m: Message, command: CommandObject) -> None:
    code = (command.args or "").strip().upper()
    if not code:
        return await m.reply("❌ مثال: <code>/delgift EID1405</code>")
    r = await GiftCode.get_motor_collection().delete_one({"code": code})
    if r.deleted_count != 1:
        return await m.reply("🎁 همچین کدی نیست.")
    await _audit(m.bot, m.from_user.id, f"کد هدیه <code>{esc(code)}</code> رو حذف کرد")
    await m.reply(f"🗑 کد <code>{esc(code)}</code> حذف شد.")


@router.message(Command("admins"), F.func(_is_admin_msg))
async def cmd_admins(m: Message) -> None:
    await m.reply(await admins_text())


@router.message(Command("addadmin"), F.func(_is_admin_msg))
async def cmd_addadmin(m: Message, command: CommandObject) -> None:
    u, _ = await _cmd_target(m, command)
    if u is None:
        return await m.reply("❌ مثال: <code>/addadmin 123456789</code> یا ریپلای روی پیامش (باید یه بار ربات رو زده باشه).")
    await m.reply(await do_add_admin(m.bot, m.from_user.id, u))


@router.message(Command("deladmin"), F.func(_is_admin_msg))
async def cmd_deladmin(m: Message, command: CommandObject) -> None:
    u, _ = await _cmd_target(m, command)
    if u is None:
        return await m.reply("❌ مثال: <code>/deladmin 123456789</code> یا ریپلای روی پیامش.")
    await m.reply(await do_del_admin(m.bot, m.from_user.id, u))
