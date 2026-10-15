"""کاسبی: خرید کسب‌وکار، ارتقای لول (سقف کارگر و درآمد بالاتر)، شعبه‌ی جدید، استخدام کارگر، برداشت درآمد."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

import config as C
import eco
import rules
from db import User
from ui import b, back, edit, kb, pairs, receipt
from util import fa, utcnow

router = Router()


async def _show(cb: CallbackQuery, uid: int, render, *args, note: str = "") -> None:
    u = await eco.get_user(uid)
    text, markup = await render(u, *args)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


def _bar(level: int) -> str:
    """نوار پیشرفت لول؛ خونه‌ی ۵۰٪ (جایی که شعبه‌ی جدید باز می‌شه) با 🔓/🔒 مشخصه."""
    mx, need = C.BIZ_MAX_LEVEL, rules.branch_unlock_level()
    cells = []
    for i in range(1, mx + 1):
        if i <= level:
            cells.append("🟩")
        else:
            cells.append("⬜")
    return "".join(cells[:need]) + ("🔓" if level >= need else "🔒") + "".join(cells[need:])


def _owned_bases(u: User) -> set[str]:
    return {rules.base_key(k) for k in u.businesses}


async def render_biz(u: User):
    pending = await eco.calc_pending(u)
    lines = ["🏪 <b>کاسبی</b>\n"]
    if u.businesses:
        fed = u.kids > 0 and (u.cook or (u.fed_until is not None and u.fed_until > utcnow()))
        bonus = int(C.KID_BONUS * u.kids * 100) if fed else 0
        lines.append(f"💰 درآمد جمع‌شده: <b>{fa(pending)}</b>")
        lines.append(f"📈 درآمد هر ۶ ساعت: <b>{fa(rules.base_income(u.businesses, u.staff, u.biz_level))}</b>")
        lines.append(f"👶 بچه‌ها: <b>{fa(u.kids)}</b> | ضریب: <b>+{fa(bonus)}٪</b>" + (" 🍽 گرسنه‌ن" if u.kids > 0 and not fed else ""))
        lines.append(f"\n💡 هر بچه‌ی سیر <b>{fa(int(C.KID_BONUS * 100))}٪</b> روی درآمد تأثیر داره")
        lines.append("⭐ روی هر کسب‌وکار بزنی می‌تونی <b>ارتقاش</b> بدی؛ هر لول = سقف کارگر و درآمد بیشتر.")
    else:
        lines.append("هنوز کسب‌وکاری نداری! با یه سوپری شروع کن. 👇")
    rows = []
    if u.businesses:
        rows.append([b(f"💰 برداشت درآمد ({fa(pending)})", "biz:collect")])
        btns = []
        for k in sorted(u.businesses, key=lambda x: (list(rules.BIZ_INDEX).index(rules.base_key(x)) if rules.base_key(x) in rules.BIZ_INDEX else 99, rules.branch_no(x))):
            if rules.base_key(k) not in rules.BIZ_INDEX:
                continue
            bz = rules.BIZ_INDEX[rules.base_key(k)]
            btns.append(b(f"{bz['emoji']} {rules.inst_name(k)} ⭐{fa(rules.biz_level(u, k))}", f"biz:view:{k}"))
        rows += pairs(btns, 2)
    if len(_owned_bases(u)) < len(C.BUSINESSES):
        rows.append([b("🛒 خرید کسب‌وکار", "biz:shop")])
    rows.append(back("menu", "◀️ منو"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "biz")
async def cb_biz(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_biz)


@router.callback_query(F.data == "biz:collect")
async def cb_collect(cb: CallbackQuery, user: User) -> None:
    amount = await eco.settle(user.tg_id)
    if amount <= 0:
        return await cb.answer("هنوز چیزی جمع نشده.", show_alert=True)
    await eco.add_xp(user.tg_id, C.XP["collect"])
    await cb.answer()
    await _show(cb, user.tg_id, render_biz, note=f"💰 <b>+{fa(amount)}</b> دلار اومد تو جیبت!")


async def render_shop(u: User):
    lines = ["🛒 <b>خرید کسب‌وکار</b>\n", "👇 روی هر کسب‌وکار بزن تا مشخصاتش رو ببینی و بخری."]
    rows = []
    for bz in rules.BIZ_INDEX.values():
        if bz["key"] in _owned_bases(u):
            continue
        rows.append([b(f"{bz['emoji']} {bz['name']} — {fa(bz['price'])}", f"biz:info:{bz['key']}")])
    rows.append(back("biz", "◀️ کاسبی"))
    return "\n".join(lines), kb(*rows)


def _staff_lines(bz: dict) -> list[str]:
    """معرفی کارگرهای اختصاصی یه کسب‌وکار: اسم، قیمت، سهم درآمد هر رده و لولی که لازم دارن."""
    lines = [f"👷 <b>کارگرها</b> (سقف: {fa(rules.worker_cap(1))} نفر تو لول ۱ ← {fa(rules.worker_cap(C.BIZ_MAX_LEVEL))} نفر تو لول {fa(C.BIZ_MAX_LEVEL)}):"]
    for w in bz["workers"]:
        need = rules.tier_unlock_level(w["idx"])
        lock = f" 🔒 از لول {fa(need)}" if need > 1 else ""
        lines.append(f"  {w['emoji']} {w['name']}: {fa(w['price'])} ← +{fa(int(bz['income'] * w['mult']))} / ۶ ساعت{lock}")
    lines.append(f"\n⭐ <b>ارتقا</b>: هر لول +{fa(int(C.BIZ_LEVEL_INCOME_BONUS * 100))}٪ درآمد و +{fa(C.BIZ_WORKERS_PER_LEVEL)} جای کارگر.")
    lines.append(f"🏗 وقتی به لول {fa(rules.branch_unlock_level())} (۵۰٪ ارتقا) برسی، می‌تونی <b>شعبه‌ی جدید</b> هم بسازی (تا {fa(C.BIZ_MAX_BRANCHES)} شعبه).")
    return lines


async def render_info(u: User, key: str):
    """مشخصات یه کسب‌وکار قبل از خرید (تو خود کسب‌وکار، نه تو لیست)."""
    bz = rules.BIZ_INDEX[key]
    text = (f"{bz['emoji']} <b>{bz['name']}</b>\n\n"
            f"💵 قیمت خرید: <b>{fa(bz['price'])}</b>\n"
            f"📈 درآمد پایه: <b>{fa(bz['income'])}</b> / ۶ ساعت\n\n"
            + "\n".join(_staff_lines(bz)))
    return text, kb([b(f"🛒 خرید — {fa(bz['price'])}", f"biz:buy:{key}")], back("biz:shop", "◀️ لیست کسب‌وکارها"))


@router.callback_query(F.data == "biz:shop")
async def cb_shop(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_shop)


@router.callback_query(F.data.startswith("biz:info:"))
async def cb_info(cb: CallbackQuery, user: User) -> None:
    key = cb.data.split(":")[2]
    if key not in rules.BIZ_INDEX:
        return await cb.answer()
    await cb.answer()
    if key in user.businesses:                      # قبلاً خریده → صفحه‌ی خود کسب‌وکار
        return await _show(cb, user.tg_id, render_view, key)
    await _show(cb, user.tg_id, render_info, key)


@router.callback_query(F.data.startswith("biz:buy:"))
async def cb_buy(cb: CallbackQuery, user: User) -> None:
    bz = rules.BIZ_INDEX.get(cb.data.split(":")[2])
    if not bz:
        return await cb.answer()
    if bz["key"] in user.businesses:
        return await cb.answer("این رو داری!", show_alert=True)
    if user.cash < bz["price"]:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.settle(user.tg_id)       # ساعت درآمد ریست شه تا کسب‌وکار جدید از «الان» حساب بشه
    if not await eco.spend(user.tg_id, bz["price"], set_={f"businesses.{bz['key']}": 0},
                           flt={f"businesses.{bz['key']}": {"$exists": False}}, invest=bz["price"]):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.add_xp(user.tg_id, C.XP["biz"])
    await cb.answer(f"✅ خرید انجام شد!\n{bz['emoji']} {bz['name']} — {fa(bz['price'])} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_biz, note=f"✅ <b>خرید انجام شد</b> — 🎉 {bz['emoji']} {bz['name']} راه افتاد!")


async def render_view(u: User, key: str):
    """صفحه‌ی یه شعبه: لول، نوار پیشرفت، کارگرها، ارتقا، شعبه‌ی جدید."""
    bk = rules.base_key(key)
    bz = rules.BIZ_INDEX[bk]
    lvl = rules.biz_level(u, key)
    cap = rules.worker_cap(lvl)
    cnt = rules.staff_counts(u, key)
    tot = sum(cnt)
    total = rules.base_income(u.businesses, u.staff, u.biz_level)
    mine_rate = rules.inst_income(u, key)
    mine = int(await eco.calc_pending(u) * mine_rate / total) if total else 0   # سهم همین شعبه از درآمد جمع‌شده
    text = (f"{bz['emoji']} <b>{rules.inst_name(key)}</b> — ⭐ لول {fa(lvl)}/{fa(C.BIZ_MAX_LEVEL)} ({rules.level_title(lvl)})\n"
            f"{_bar(lvl)}\n\n"
            f"💰 درآمد جمع‌شده: <b>{fa(mine)}</b>\n"
            f"📈 درآمد هر ۶ ساعت: <b>{fa(mine_rate)}</b> (ضریب لول ×{rules.level_mult(lvl):.1f})\n\n"
            f"👷 کارگرها ({fa(tot)}/{fa(cap)}):\n" + "\n".join(
                f"  {w['emoji']} {w['name']}: {fa(cnt[w['idx']])}" for w in bz["workers"]))
    rows = []
    if lvl < C.BIZ_MAX_LEVEL:
        cost = rules.upgrade_cost(bk, lvl)
        text += (f"\n\n⬆️ <b>لول {fa(lvl + 1)}</b>: سقف کارگر {fa(cap)} ← {fa(rules.worker_cap(lvl + 1))} | "
                 f"درآمد ×{rules.level_mult(lvl):.1f} ← ×{rules.level_mult(lvl + 1):.1f}")
        for w in bz["workers"]:
            if rules.tier_unlock_level(w["idx"]) == lvl + 1:
                text += f"\n🔓 {w['emoji']} {w['name']} باز می‌شه"
        rows.append([b(f"⬆️ ارتقا به لول {fa(lvl + 1)} — {fa(cost)}", f"biz:up:{key}")])
    else:
        text += "\n\n🏆 این شعبه به <b>آخرین لول</b> رسیده!"
    if tot < cap:
        for w in bz["workers"]:
            need = rules.tier_unlock_level(w["idx"])
            if lvl >= need:
                rows.append([b(f"{w['emoji']} استخدام {w['name']} — {fa(w['price'])}", f"biz:hire:{key}:{w['idx']}")])
            else:
                rows.append([b(f"🔒 {w['name']} (لول {fa(need)})", f"biz:lock:{need}")])
    # شعبه‌ی جدید: فقط تو صفحه‌ی آخرین شعبه نشون داده می‌شه
    brs = rules.branches_of(u, bk)
    if brs and brs[-1] == key:
        st, price, need = rules.branch_status(u, bk)
        if st == "ok":
            n = len(brs) + 1
            text += (f"\n\n🏗 <b>شعبه‌ی {fa(n)} باز شد!</b> قیمت: {fa(price)} — شعبه‌ی جدید از لول ۱ شروع می‌کنه.")
            rows.append([b(f"🏗 ساخت شعبه‌ی {fa(n)} — {fa(price)}", f"biz:branch:{bk}")])
        elif st == "locked":
            text += (f"\n\n🔒 شعبه‌ی {fa(len(brs) + 1)}: وقتی این شعبه به لول {fa(need)} (۵۰٪ ارتقا) برسه باز می‌شه "
                     f"(قیمت: {fa(price)}).")
    rows.append(back("biz", "◀️ کاسبی"))
    return text, kb(*rows)


@router.callback_query(F.data.startswith("biz:view:"))
async def cb_view(cb: CallbackQuery, user: User) -> None:
    key = cb.data.split(":")[2]
    if key not in user.businesses or rules.base_key(key) not in rules.BIZ_INDEX:
        return await cb.answer("این رو نداری!", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_view, key)


@router.callback_query(F.data.startswith("biz:lock:"))
async def cb_lock(cb: CallbackQuery) -> None:
    need = cb.data.split(":")[2]
    await cb.answer(f"🔒 این کارگر از لول {fa(int(need) if need.isdigit() else 1)} به بعد قابل استخدامه. اول شعبه رو ارتقا بده.", show_alert=True)


def _lvl_filter(lvl: int) -> dict:
    """فیلتر اتمیک روی لول فعلی؛ لول ۱ یعنی «نبودن یا ۱»."""
    return {"$in": [None, 1]} if lvl <= 1 else lvl


@router.callback_query(F.data.startswith("biz:up:"))
async def cb_upgrade(cb: CallbackQuery, user: User) -> None:
    key = cb.data.split(":")[2]
    if key not in user.businesses or rules.base_key(key) not in rules.BIZ_INDEX:
        return await cb.answer("این رو نداری!", show_alert=True)
    bk = rules.base_key(key)
    lvl = rules.biz_level(user, key)
    if lvl >= C.BIZ_MAX_LEVEL:
        return await cb.answer("🏆 به آخرین لول رسیده!", show_alert=True)
    cost = rules.upgrade_cost(bk, lvl)
    if user.cash < cost:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.settle(user.tg_id)       # درآمد قبلی با لول قبلی حساب شه
    if not await eco.spend(user.tg_id, cost, set_={f"biz_level.{key}": lvl + 1},
                           flt={f"biz_level.{key}": _lvl_filter(lvl), f"businesses.{key}": {"$exists": True}}, invest=cost):
        return await cb.answer("💸 پول کافی نداری یا همین الان ارتقا خورده!", show_alert=True)
    await eco.add_xp(user.tg_id, C.XP.get("biz_up", C.XP["biz"]))
    await cb.answer(f"✅ ارتقا انجام شد!\n⭐ لول {fa(lvl + 1)} — {fa(cost)} دلار", show_alert=True)
    await receipt(cb, f"⬆️ ارتقای {rules.inst_name(key)} به لول {fa(lvl + 1)} — {fa(cost)} دلار")
    note = (f"✅ <b>ارتقا انجام شد</b> — ⭐ {rules.inst_name(key)} الان لول {fa(lvl + 1)} ({rules.level_title(lvl + 1)}) شد! "
            f"سقف کارگر: <b>{fa(rules.worker_cap(lvl + 1))}</b>")
    need = rules.branch_unlock_level()
    if lvl + 1 == need and len(rules.branches_of(user, bk)) < C.BIZ_MAX_BRANCHES and rules.branches_of(user, bk)[-1] == key:
        note += f"\n🏗 <b>۵۰٪ ارتقا کامل شد!</b> الان می‌تونی شعبه‌ی جدید بسازی."
    await _show(cb, user.tg_id, render_view, key, note=note)


@router.callback_query(F.data.startswith("biz:branch:"))
async def cb_branch(cb: CallbackQuery, user: User) -> None:
    bk = cb.data.split(":")[2]
    bz = rules.BIZ_INDEX.get(bk)
    if not bz or bk not in user.businesses:
        return await cb.answer("اول باید این کسب‌وکار رو داشته باشی!", show_alert=True)
    st, price, need = rules.branch_status(user, bk)
    brs = rules.branches_of(user, bk)
    if st == "max":
        return await cb.answer(f"حداکثر {fa(C.BIZ_MAX_BRANCHES)} شعبه می‌تونی داشته باشی.", show_alert=True)
    if st == "locked":
        return await cb.answer(f"🔒 اول آخرین شعبه رو به لول {fa(need)} برسون.", show_alert=True)
    if user.cash < price:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    new = rules.inst_id(bk, len(brs) + 1)
    await eco.settle(user.tg_id)
    if not await eco.spend(user.tg_id, price, set_={f"businesses.{new}": 0, f"biz_level.{new}": 1},
                           flt={f"businesses.{new}": {"$exists": False}}, invest=price):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.add_xp(user.tg_id, C.XP["biz"])
    await cb.answer(f"✅ شعبه‌ی جدید ساخته شد!\n{bz['emoji']} {rules.inst_name(new)} — {fa(price)} دلار", show_alert=True)
    await receipt(cb, f"🏗 {bz['emoji']} {rules.inst_name(new)} — {fa(price)} دلار")
    await _show(cb, user.tg_id, render_view, new, note=f"✅ <b>شعبه‌ی جدید راه افتاد</b> — 🎉 {bz['emoji']} {rules.inst_name(new)}!")


@router.callback_query(F.data.startswith("biz:hire:"))
async def cb_hire(cb: CallbackQuery, user: User) -> None:
    parts = cb.data.split(":")
    key = parts[2]
    bz = rules.BIZ_INDEX.get(rules.base_key(key))
    if not bz or key not in user.businesses:
        return await cb.answer("این رو نداری!", show_alert=True)
    try:
        idx = int(parts[3]) if len(parts) > 3 else 0      # دکمه‌های قدیمی (بدون رده) = رده‌ی اول
    except ValueError:
        idx = 0
    if not 0 <= idx < len(bz["workers"]):
        return await cb.answer()
    w = bz["workers"][idx]
    lvl = rules.biz_level(user, key)
    if lvl < rules.tier_unlock_level(idx):
        return await cb.answer(f"🔒 این کارگر از لول {fa(rules.tier_unlock_level(idx))} به بعد قابل استخدامه.", show_alert=True)
    cap_n = rules.worker_cap(lvl)
    if rules.total_workers(user, key) >= cap_n:
        return await cb.answer("ظرفیت کارگر پره! شعبه رو ارتقا بده.", show_alert=True)
    if user.cash < w["price"]:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.settle(user.tg_id)       # درآمد قبلی با تعداد کارگر قبلی حساب شه
    field = f"businesses.{key}" if idx == 0 else f"staff.{rules.staff_key(key, idx)}"
    # سقف کل کارگرهای این شعبه (همه‌ی رده‌ها با هم) اتمیک چک می‌شه؛ لول فقط بالا می‌ره پس سقفِ خونده‌شده امنه
    fields = [f"$businesses.{key}"] + [f"$staff.{rules.staff_key(key, i)}" for i in range(1, len(bz["workers"]))]
    cap = {"$expr": {"$lt": [{"$add": [{"$ifNull": [f, 0]} for f in fields]}, cap_n]}}
    if not await eco.spend(user.tg_id, w["price"], inc={field: 1}, flt=cap, invest=w["price"]):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer("✅ خرید انجام شد!")
    await receipt(cb, f"{w['emoji']} {w['name']} جدید — {fa(w['price'])} دلار")
    await _show(cb, user.tg_id, render_view, key, note=f"✅ <b>خرید انجام شد</b> — {w['emoji']} {w['name']} جدید استخدام شد!")
