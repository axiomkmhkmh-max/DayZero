"""مبارزه: حمله‌ی بازیکن به بازیکن (دفاع خودکار با سپر/امنیت/نیروها)."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import config as C
import eco
import rules
from db import FightLog, User
from redis_service import redis_service
from util import dur, esc, fa, left, mention, utcnow


def protected_reason(target: User, now) -> Optional[str]:
    return None          # تایمر محافظت (تازه‌وارد / تازه غارت‌شده) حذف شده


def same_alliance(a: User, b: User) -> bool:
    return a.alliance_id is not None and a.alliance_id == b.alliance_id


async def hostile_checks(attacker: User, target: User, now) -> Optional[str]:
    """چک‌های مشترک همه‌ی کارهای خصمانه (حمله، جن، دزد، طلسم)."""
    if attacker.tg_id == target.tg_id:
        return "😅 خودت رو که نمی‌تونی بزنی!"
    if same_alliance(attacker, target):
        return "🤝 هم‌اتحادی‌ها به هم دست نمی‌زنن!"
    if target.antirob and rules.is_weak_vs(target.xp, attacker.xp):          # ضدزورگیری: قوی‌ها به ضعیف‌ها دست نمی‌زنن
        return (f"🛡 {esc(target.name)} (سطح {fa(rules.level_of(target.xp))}) «ضدزورگیری» داره و از تو "
                f"(سطح {fa(rules.level_of(attacker.xp))}) حداقل {fa(C.WEAK_GAP)} سطح ضعیف‌تره؛ نمی‌تونی بهش دست بزنی!")
    return protected_reason(target, now)


async def drop_shield(uid: int, user: User, now) -> None:
    if user.shield_until and user.shield_until > now:
        await eco.update_user(uid, set_={"shield_until": None})


async def do_attack(bot, attacker: User, target: User, weapon_text: str = "") -> str:
    now = utcnow()
    err = await hostile_checks(attacker, target, now)
    if err:
        return err
    wait = left(attacker.attack_ready_at, now)
    if wait > 0:
        return f"😮‍💨 هنوز خسته‌ای؛ {dur(wait)} دیگه می‌تونی حمله کنی."

    # اسلحه‌های انتخابی
    keys, rest = rules.parse_weapons(weapon_text) if weapon_text.strip() else ([], "")
    if rest:
        return f"🤔 اسلحه‌ی «{esc(rest)}» رو نشناختم. مثال: <code>حمله کلاشینکف و شمشیر</code>"
    if len(keys) > 2:
        return "⚠️ حداکثر ۲ اسلحه رو می‌شه ترکیب کرد."
    for k in keys:
        if k not in attacker.gear:
            return f"🚫 {rules.SHOP_INDEX[k]['name']} رو نداری! از «بازار ← فروشگاه» بخر."
    weapons: Optional[list[str]] = keys or None

    if target.cash < C.MIN_VICTIM_CASH:
        return f"🕳 جیب {esc(target.name)} خالیه (پولش رو تو گاوصندوق گذاشته)؛ ارزش حمله نداره."

    atk, chosen = rules.attack_power(attacker.gear, attacker.forces, weapons)
    dfn, shield, sec = rules.defense_power(target.gear, target.forces)
    win, a, d = rules.roll_fight(atk, dfn)

    an, tn = mention(attacker.tg_id, attacker.name), mention(target.tg_id, target.name)
    wnames = " + ".join(f"{w['emoji']} {w['name']}" for w in chosen) if chosen else "✊ دست خالی"
    head = (f"⚔️ {an} به {tn} حمله کرد!\n{wnames}"
            + (" (حمله‌ی ترکیبی 🔥)" if len(chosen) >= 2 else "") + "\n"
            f"⚔️ قدرت حمله: <b>{fa(atk)}</b>   🛡 قدرت دفاع: <b>{fa(dfn)}</b>\n")

    # هزینه‌ی حمله: کول‌داون + از بین رفتن سپر تازه‌وارد
    await eco.update_user(attacker.tg_id, set_={"attack_ready_at": now + timedelta(seconds=C.ATTACK_COOLDOWN_SEC)})
    await drop_shield(attacker.tg_id, attacker, now)

    await eco.add_xp(attacker.tg_id, C.XP["win"] if win else C.XP["lose"])
    if win:
        fresh = await eco.get_user(target.tg_id)
        loot = 0
        for _ in range(2):
            amount = rules.loot_amount(fresh.cash, atk, a, d)
            if amount > 0 and await eco.transfer(target.tg_id, attacker.tg_id, amount):
                loot = amount
                break
            fresh = await eco.get_user(target.tg_id)
        leak = int(fresh.vault * C.VAULT_LEAK)
        if leak > 0 and await eco.transfer(target.tg_id, attacker.tg_id, leak, field="vault"):
            pass
        else:
            leak = 0
        if loot <= 0 and leak <= 0:
            return head + "💨 تا رسیدی، طرف پولش رو جابجا کرده بود؛ دست‌خالی برگشتی!"
        await eco.update_user(attacker.tg_id, inc={"stats.wins": 1, "stats.loot": loot + leak})
        await eco.update_user(target.tg_id, inc={"stats.lost": loot + leak, "stats.hit": 1})
        await FightLog(attacker_id=attacker.tg_id, defender_id=target.tg_id, a_name=attacker.name, d_name=target.name,
                       win=True, loot=loot + leak, atk=atk, dfn=dfn).insert()
        text = head + f"💥 <b>حمله موفق!</b> {an} مبلغ <b>{fa(loot)}</b> دلار از جیب {tn} کش رفت!"
        if leak:
            text += f"\n🔐 و <b>{fa(leak)}</b> دلار هم از گاوصندوقش نشت کرد!"
        victim_dm = f"💥 {esc(attacker.name)} بهت حمله کرد و <b>{fa(loot + leak)}</b> دلار ازت برد!\nسپر/نیروی قوی‌تر بخر!"
    else:
        fresh_att = await eco.get_user(attacker.tg_id)
        pen = rules.repel_penalty(fresh_att.cash, dfn)
        if pen > 0 and not await eco.transfer(attacker.tg_id, target.tg_id, pen):
            pen = 0
        await eco.update_user(attacker.tg_id, inc={"stats.losses": 1})
        await eco.update_user(target.tg_id, inc={"stats.repelled": 1})
        await FightLog(attacker_id=attacker.tg_id, defender_id=target.tg_id, a_name=attacker.name, d_name=target.name,
                       win=False, loot=-pen, atk=atk, dfn=dfn).insert()
        by = shield["name"] if shield else (sec["name"] if sec else "نیروها و تجهیزاتش")
        text = (head + f"🛡 <b>حمله دفع شد!</b> {tn} با «{esc(by)}» جوابت رو داد."
                + (f"\n💸 {an} مجبور شد <b>{fa(pen)}</b> دلار خسارت بده." if pen else ""))
        victim_dm = f"🛡 {esc(attacker.name)} بهت حمله کرد ولی دفعش کردی!" + (f" 💰 {fa(pen)} دلار خسارت گرفتی." if pen else "")
    try:
        await bot.send_message(target.tg_id, victim_dm)
    except Exception:  # noqa: BLE001
        pass
    return text
