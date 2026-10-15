"""🏦💥 سرقت بانکی گروهی: ثبت‌نام ۶۰ ثانیه‌ای → نقشه (آروم / تهاجمی / خیانت) → نتیجه.
خزانه‌ی مشترک از کارمزد کارت‌به‌کارت، ورودی‌ها، جریمه‌ها و مزایده‌ها پر می‌شه."""
from __future__ import annotations

import asyncio
import math
import time
from typing import Optional

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

import eco
import evt
import rules
from db import User
from redis_service import redis_service
from ui import b, kb
from util import esc, fa, mention, norm

router = Router()
log = structlog.get_logger("heist")

ENTRY = 100_000                 # ورودی هر نفر (می‌ره تو خزانه)
LOBBY_SEC = 60
PLAN_SEC = 25
MIN_PLAYERS = 3
MIN_POT = 500_000
LOOT_CAP = 15_000_000
FINE = 150_000                  # جریمه‌ی هر نفر وقتی لو می‌رن (می‌ره تو خزانه)
USER_CD = 1800                  # هر نفر هر ۳۰ دقیقه یک بار
BASE_CHANCE = 40
ROLES = {
    "in": ("🥷", "نفوذی"),
    "dr": ("🚗", "راننده"),
    "hk": ("💻", "هکر"),
    "gd": ("🔫", "نگهبان‌زن"),
}
KEY = "hz:{chat}:{msg}"
ACTIVE = "hz:active:{chat}"
DUE = "hz:due"
TTL = 900

ALIASES = {norm(a): "start" for a in ("نقشه سرقت", "سرقت گروهی", "سرقت بانک", "heist")}
ALIASES.update({norm(a): "pot" for a in ("خزانه", "خزانه سرقت", "گاوصندوق سرقت")})


def _key(chat: int, msg: int) -> str:
    return KEY.format(chat=chat, msg=msg)


# ───────────────────────── نمایش ─────────────────────────
def _lobby_text(h: dict, pot: int) -> str:
    left = max(0, int(h["end"] - time.time()))
    seats = {p["role"]: (uid, p) for uid, p in h["players"].items()}
    lines = []
    for rk, (em, nm) in ROLES.items():
        if rk in seats:
            uid, p = seats[rk]
            lines.append(f"{em} {nm}: {mention(int(uid), p['name'])}")
        else:
            lines.append(f"{em} {nm}: <i>— خالی —</i>")
    return (f"🏦💥 <b>نقشه‌ی سرقت از بانک مرکزی</b>\n\n"
            f"💰 خزانه‌ی بانک: <b>{fa(pot)}</b>\n"
            f"🎟 ورودی هر نفر: <b>{fa(ENTRY)}</b> (می‌ره تو خزانه) · حداقل {fa(MIN_PLAYERS)} نفر\n\n"
            + "\n".join(lines)
            + f"\n\n⏳ ثبت‌نام: حدود <b>{fa(left)}</b> ثانیه\n"
              "<i>یه نقش بگیر. بعدش هر کس پنهانی انتخاب می‌کنه آروم بره، تهاجمی بره، یا خیانت کنه!</i>")


def _lobby_kb(h: dict):
    taken = {p["role"] for p in h["players"].values()}
    rows = [[b(("✅ " if rk in taken else "") + f"{em} {nm}", f"hz:j:{rk}") for rk, (em, nm) in list(ROLES.items())[i:i + 2]]
            for i in range(0, len(ROLES), 2)]
    rows.append([b("🚪 انصراف", "hz:l")])
    return kb(*rows)


def _plan_text(h: dict) -> str:
    left = max(0, int(h["end"] - time.time()))
    ready = sum(1 for p in h["players"].values() if p["mode"])
    crew = "\n".join(f"{ROLES[p['role']][0]} {ROLES[p['role']][1]}: {mention(int(uid), p['name'])}" for uid, p in h["players"].items())
    return (f"🏦💥 <b>عملیات شروع شد!</b>\n\n{crew}\n\n"
            f"🤫 <b>آروم</b>: شانس معمولی، غنیمت معمولی\n"
            f"💥 <b>تهاجمی</b>: شانس بیشتر و غنیمت بیشتر، ولی لو بری جریمه‌ت دو برابره\n"
            f"🗡 <b>خیانت</b>: اگه موفق بشن و فقط تو خیانت کنی، ۶۰٪ غنیمت مال توئه!\n\n"
            f"انتخابت پنهانیه (فقط خودت می‌بینی). آماده: <b>{fa(ready)}/{fa(len(h['players']))}</b> · ⏳ {fa(left)} ثانیه")


def _plan_kb():
    return kb([b("🤫 آروم", "hz:q"), b("💥 تهاجمی", "hz:o")], [b("🗡 خیانت", "hz:t")])


async def _edit(bot, chat: int, msg: int, text: str, markup=None) -> None:
    try:
        await bot.edit_message_text(text, chat_id=chat, message_id=msg, reply_markup=markup)
    except Exception:  # noqa: BLE001
        pass


async def _enqueue(chat: int, msg: int, when: float) -> None:
    await redis_service.r.zadd(DUE, {f"{chat}:{msg}": when})


# ───────────────────────── دستورها ─────────────────────────
def _is_cmd(m: Message) -> bool:
    return bool(m.text) and norm(m.text) in ALIASES


@router.message(F.func(_is_cmd))
async def cmd(m: Message) -> None:
    what = ALIASES[norm(m.text)]
    if what == "pot":
        return await m.reply(f"💰 خزانه‌ی بانک مرکزی: <b>{fa(await evt.pot())}</b>\n<i>از کارمزد کارت‌به‌کارت، ورودی‌ها، جریمه‌ها و مزایده‌ها پر می‌شه. «نقشه سرقت» بزن!</i>")
    if m.chat.type == "private":
        return await m.answer("🏦 سرقت گروهیه؛ ربات رو ببر یه گروه و بنویس «نقشه سرقت».")
    return await evt.as_actor(m, _start)


async def _start(m: Message, actor: User) -> None:
    r = redis_service.r
    chat = m.chat.id
    cd = await r.ttl(f"hz:cd:{actor.tg_id}")
    if cd and cd > 0:
        return await m.reply(f"🕒 تازه عملیات داشتی؛ {fa(cd // 60 + 1)} دقیقه‌ی دیگه می‌تونی دوباره.")
    pot = await evt.pot()
    if pot < MIN_POT:
        return await m.reply(f"🏦 خزانه فقط <b>{fa(pot)}</b> داره؛ حداقل {fa(MIN_POT)} لازمه. بذار پر بشه.")
    if not await r.set(ACTIVE.format(chat=chat), "1", nx=True, ex=TTL):
        return await m.reply("🚨 همین الان یه نقشه‌ی سرقت توی این گروه بازه.")
    h = {"phase": "lobby", "end": time.time() + LOBBY_SEC, "players": {}, "host": actor.tg_id}
    sent = await m.answer(_lobby_text(h, pot), reply_markup=_lobby_kb(h))
    await evt.jsave(_key(chat, sent.message_id), h, TTL)
    await _enqueue(chat, sent.message_id, h["end"])
    await r.set(ACTIVE.format(chat=chat), str(sent.message_id), ex=TTL)


# ───────────────────────── دکمه‌ها ─────────────────────────
@router.callback_query(F.data.startswith("hz:"))
async def cb(cb_: CallbackQuery, user: User) -> None:
    chat, msg = cb_.message.chat.id, cb_.message.message_id
    op = cb_.data.split(":")[1:]
    async with redis_service.lock(f"hz:lk:{chat}:{msg}", ttl=10, wait=5):
        h = await evt.jload(_key(chat, msg))
        if not h:
            return await cb_.answer("⌛ این عملیات تموم شده.", show_alert=True)
        uid = str(user.tg_id)
        if op[0] in ("j", "l"):
            if h["phase"] != "lobby":
                return await cb_.answer("⏳ ثبت‌نام بسته شده.", show_alert=True)
            if op[0] == "l":
                p = h["players"].pop(uid, None)
                if not p:
                    return await cb_.answer("تو ثبت‌نام نکرده بودی.", show_alert=True)
                await eco.gain(user.tg_id, ENTRY)
                await cb_.answer("🚪 انصراف دادی؛ ورودی برگشت.")
            else:
                role = op[1]
                if role not in ROLES:
                    return await cb_.answer()
                if any(q["role"] == role and k != uid for k, q in h["players"].items()):
                    return await cb_.answer("این نقش پره! یکی دیگه رو بگیر.", show_alert=True)
                if uid in h["players"]:
                    h["players"][uid]["role"] = role
                    await cb_.answer(f"{ROLES[role][0]} نقشت شد {ROLES[role][1]}.")
                else:
                    cd = await redis_service.r.ttl(f"hz:cd:{user.tg_id}")
                    if cd and cd > 0:
                        return await cb_.answer(f"🕒 {fa(cd // 60 + 1)} دقیقه‌ی دیگه می‌تونی عضو بشی.", show_alert=True)
                    if not await eco.spend(user.tg_id, ENTRY):
                        return await cb_.answer(f"💸 برای ورودی {fa(ENTRY)} تو جیبت پول نداری!", show_alert=True)
                    h["players"][uid] = {"name": user.name, "role": role, "mode": None, "traitor": False}
                    await cb_.answer(f"{ROLES[role][0]} عضو شدی ({ROLES[role][1]})! ورودی {fa(ENTRY)} کم شد.")
            await evt.jsave(_key(chat, msg), h, TTL)
            return await _edit(cb_.bot, chat, msg, _lobby_text(h, await evt.pot()), _lobby_kb(h))
        # ── مرحله‌ی نقشه ──
        if h["phase"] != "plan":
            return await cb_.answer("هنوز عملیات شروع نشده.", show_alert=True)
        p = h["players"].get(uid)
        if not p:
            return await cb_.answer("🚫 تو تو این عملیات نیستی.", show_alert=True)
        if op[0] == "q":
            p["mode"] = "quiet"
            await cb_.answer("🤫 آروم می‌ری.")
        elif op[0] == "o":
            p["mode"] = "loud"
            await cb_.answer("💥 تهاجمی می‌ری!")
        elif op[0] == "t":
            p["traitor"] = not p["traitor"]
            p["mode"] = p["mode"] or "quiet"
            await cb_.answer("🗡 خیانتت ثبت شد (پنهانی)." if p["traitor"] else "😇 از خیانت پشیمون شدی.", show_alert=True)
        await evt.jsave(_key(chat, msg), h, TTL)
        await _edit(cb_.bot, chat, msg, _plan_text(h), _plan_kb())


# ───────────────────────── زمان‌بندی ─────────────────────────
def _bonus(power: int, cap: int) -> int:
    return min(cap, int(math.log10(max(1, power)) * 3)) if power > 0 else 0


async def _chance(h: dict) -> tuple[int, list[str]]:
    chance, notes = BASE_CHANCE, []
    present = set()
    for uid, p in h["players"].items():
        u = await eco.get_user(int(uid))
        if not u:
            continue
        present.add(p["role"])
        lvl = rules.level_of(u.xp)
        if p["role"] == "in":
            bonus = min(12, lvl // 2)
        elif p["role"] == "dr":
            bonus = min(10, lvl // 3)
        elif p["role"] == "hk":
            bonus = _bonus(rules.hack_power(u.forces), 15)
        else:
            bonus = _bonus(rules.attack_power(u.gear, u.forces)[0], 15)
        chance += bonus
        notes.append(f"{ROLES[p['role']][0]} +{fa(bonus)}٪")
        if p["mode"] == "loud":
            chance += 4
    chance -= 8 * (len(ROLES) - len(present))
    return max(25, min(85, chance)), notes


async def _advance(bot, chat: int, msg: int) -> None:
    async with redis_service.lock(f"hz:lk:{chat}:{msg}", ttl=20, wait=10):
        h = await evt.jload(_key(chat, msg))
        if not h:
            return
        r = redis_service.r
        if h["phase"] == "lobby":
            if len(h["players"]) < MIN_PLAYERS:
                for uid in h["players"]:
                    await eco.gain(int(uid), ENTRY)
                await r.delete(_key(chat, msg), ACTIVE.format(chat=chat))
                return await _edit(bot, chat, msg, "🚪 نفرات کافی نشد؛ عملیات لغو شد و ورودی‌ها برگشت.")
            await evt.pot_add(ENTRY * len(h["players"]))
            for uid in h["players"]:
                await r.set(f"hz:cd:{uid}", "1", ex=USER_CD)
            h["phase"], h["end"] = "plan", time.time() + PLAN_SEC
            await evt.jsave(_key(chat, msg), h, TTL)
            await _edit(bot, chat, msg, _plan_text(h), _plan_kb())
            return await _enqueue(chat, msg, h["end"])
        await r.delete(_key(chat, msg))              # «کلیم» یک‌بارمصرف: اگه وسط کار خطا بخوره دوباره پرداخت نمی‌شه
        await _resolve(bot, chat, msg, h)


async def _resolve(bot, chat: int, msg: int, h: dict) -> None:
    r = redis_service.r
    players = h["players"]
    loud = sum(1 for p in players.values() if p["mode"] == "loud")
    traitors = [uid for uid, p in players.items() if p["traitor"]]
    chance, notes = await _chance(h)
    win = rules.RNG.randint(1, 100) <= chance
    crew = {uid: p for uid, p in players.items() if uid not in traitors}
    head = f"🏦💥 <b>نتیجه‌ی سرقت</b>\n🎯 شانس موفقیت: <b>{fa(chance)}٪</b> ({' · '.join(notes)})\n\n"
    lines: list[str] = []
    if win:
        frac = 0.20 + 0.05 * loud
        loot = await evt.pot_take(min(LOOT_CAP, int(await evt.pot() * frac)))
        if len(traitors) >= 2:
            lost = loot // 2
            await evt.pot_add(lost)
            loot -= lost
            lines.append("🗡🗡 چند نفر هم‌زمان خیانت کردن و سر غنیمت به هم افتادن؛ نصفش گم شد!")
            share_to = crew
        elif len(traitors) == 1:
            t_uid = traitors[0]
            cut = int(loot * 0.6)
            await eco.gain(int(t_uid), cut)
            lines.append(f"🗡 {mention(int(t_uid), players[t_uid]['name'])} خیانت کرد و با <b>{fa(cut)}</b> فرار کرد!")
            loot -= cut
            share_to = crew
        else:
            share_to = players
        each = loot // max(1, len(share_to))
        for uid in share_to:
            await eco.gain(int(uid), each)
            await eco.add_xp(int(uid), 40)
            await eco.update_user(int(uid), inc={"stats.heist_w": 1})
        rest = loot - each * max(1, len(share_to))
        await evt.pot_add(rest)
        who = "، ".join(mention(int(u), p["name"]) for u, p in share_to.items()) or "—"
        lines.append(f"🎉 <b>سرقت موفق!</b> {who} هر کدوم <b>{fa(each)}</b> گرفتن.")
    else:
        lines.append("🚨 <b>لو رفتین!</b> پلیس رسید.")
        mult = 2 if loud else 1
        for uid, p in players.items():
            if uid in traitors and len(traitors) == 1:
                lines.append(f"🗡 {mention(int(uid), p['name'])} قبل از لو رفتن جیم شد و جریمه نشد.")
                continue
            u = await eco.get_user(int(uid))
            fine = min(u.cash if u else 0, FINE * (mult if p["mode"] == "loud" else 1))
            if fine > 0 and await eco.spend(int(uid), fine):
                await evt.pot_add(fine)
                lines.append(f"💸 {mention(int(uid), p['name'])}: جریمه {fa(fine)}")
    await r.delete(ACTIVE.format(chat=chat))
    await _edit(bot, chat, msg, head + "\n".join(lines) + f"\n\n💰 خزانه‌ی بانک: <b>{fa(await evt.pot())}</b>")


async def sweeper(bot) -> None:
    r = redis_service.r
    while True:
        try:
            await asyncio.sleep(2)
            for member in await r.zrangebyscore(DUE, 0, time.time()):
                if not await r.zrem(DUE, member):
                    continue
                chat, msg = (int(x) for x in member.split(":"))
                try:
                    await _advance(bot, chat, msg)
                except Exception:  # noqa: BLE001
                    log.exception("heist_advance_error", member=member)
                    await r.zadd(DUE, {member: time.time() + 15})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("heist_sweeper_error")
