"""خونه: خانواده، گاوصندوق، کمد، نیروها، ارتقا."""
from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import actions
import config as C
import eco
import rules
from db import User
from h_target import ask_target
from ui import b, back, edit, kb, receipt
from util import dur, esc, fa, left, utcnow

router = Router()


def kid_cap(u: User) -> int:
    return C.HOUSES[u.house][0] if u.house else 0


async def _show(cb: CallbackQuery, uid: int, render, *args, note: str = "") -> None:
    u = await eco.get_user(uid)
    text, markup = await render(u, *args)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


# ───────────────────────── خونه ─────────────────────────
def _house_assets(u: User) -> str:
    """تجهیزات امنیتی خریده‌شده + نیروهای استخدام‌شده، برای صفحه‌ی خونه."""
    stat = C.SHOP["security"]["stat"]
    lines = ["\n\n🛡 <b>تجهیزات امنیتی</b>"]
    sec = rules.owned(u.gear, "security")
    if sec:
        for i, it in enumerate(sec):
            lines.append(f"  {it['emoji']} {it['name']} ({stat}{fa(it['power'])})" + (" ⭐" if i == 0 else ""))
    else:
        lines.append("  هنوز چیزی نخریدی (بازار ← امنیت خونه)")
    lines.append("\n💂 <b>نیروها</b>")
    found = False
    for c in C.FORCES.values():
        for i in range(3):
            f = rules.FORCE_INDEX.get(f"{c['code']}{i}")
            n = u.forces.get(f"{c['code']}{i}", 0)
            if f and n > 0:
                found = True
                lines.append(f"  {c['emoji']} {f['name']} × {fa(n)}")
    if not found:
        lines.append("  هنوز نیرویی نداری (دکمه‌ی نیروها)")
    return "\n".join(lines)


async def render_home(u: User):
    cap, fcap = kid_cap(u), rules.force_cap(u.house)
    title = f"سطح {fa(u.house)}" if u.house else "هنوز خونه نداری"
    text = (f"🏠 <b>خونه</b> — {title}\n\n"
            f"👩 همسر: <b>{fa(1 if u.spouse_id else 0)}</b>    "
            f"👶 بچه: <b>{fa(u.kids)}/{fa(cap)}</b>    "
            f"💂 نیرو: <b>{fa(rules.force_count(u.forces))}/{fa(fcap)}</b>")
    if not u.house:
        text += f"\n\n💡 با خرید خونه‌ی سطح ۱ ({fa(C.HOUSES[1][1])} دلار) می‌تونی بچه‌دار بشی."
    text += _house_assets(u)
    return text, kb(
        [b("👨‍👩‍👧 مدیریت خانواده", "fam")],
        [b("🔐 گاوصندوق", "vault"), b("🗄 کمد", "closet")],
        [b("💂 نیروها", "forces"), b("⬆️ ارتقای خونه", "house")],
        back("menu", "◀️ منو"))


@router.callback_query(F.data == "home")
async def cb_home(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await _show(cb, user.tg_id, render_home)


# ───────────────────────── خانواده ─────────────────────────
async def render_family(u: User):
    text = ("👨‍👩‍👧 <b>مدیریت خانواده</b>\n\n"
            f"👶 بچه‌ها: {fa(u.kids)}/{fa(kid_cap(u))}\n"
            f"{fed_status(u)}")
    return text, kb([b("🍽 غذا دادن به بچه‌ها", "fam:feed")], [b("💪 قوای جنسی", "fam:power")],
                    [b("💍 همسر", "fam:marry"), b("📋 راهنما", "fam:guide")], back("home", "◀️ خونه"))


def fed_status(u: User) -> str:
    if u.cook:
        return "👨‍🍳 آشپز داری؛ بچه‌ها همیشه سیرن ✅"
    w = left(u.fed_until)
    return f"🍽 بچه‌ها: سیر ✅ ({dur(w)} دیگه)" if w > 0 else "🍽 بچه‌ها: گرسنه ⛔ (تا غذا نخورن کار نمی‌کنن)"


@router.callback_query(F.data == "fam")
async def cb_fam(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_family)


async def render_feed(u: User):
    text = ("🍽 <b>غذا دادن به بچه‌ها</b>\n\n"
            "بچه‌ها باید غذا بخورن تا کار کنن؛ هر بچه‌ی سیر <b>+۲۵٪</b> درآمد کاسبی می‌ده.\n"
            f"{fed_status(u)}")
    rows = [[b(f"{f['emoji']} {f['name']} — {fa(f['price'])} — {fa(f['hours'])} ساعت کار", f"fam:eat:{f['key']}")]
            for f in rules.FOOD_INDEX.values()]
    cook_label = "👨‍🍳 آشپز داری ✅" if u.cook else f"👨‍🍳 استخدام آشپز — {fa(C.COOK_PRICE)} — کار دائمی"
    rows.append([b(cook_label, "fam:cook")])
    rows.append(back("fam", "◀️ خانواده"))
    return text, kb(*rows)


@router.callback_query(F.data == "fam:feed")
async def cb_feed(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_feed)


@router.callback_query(F.data.startswith("fam:eat:"))
async def cb_eat(cb: CallbackQuery, user: User) -> None:
    f = rules.FOOD_INDEX.get(cb.data.split(":")[2])
    if not f:
        return await cb.answer()
    if user.kids <= 0:
        return await cb.answer("👶 هنوز بچه‌ای نداری که غذا بدی!", show_alert=True)
    if user.cash < f["price"]:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.settle(user.tg_id)
    u = await eco.get_user(user.tg_id)
    now = utcnow()
    until = max(now, u.fed_until or now) + timedelta(hours=f["hours"])
    if not await eco.spend(user.tg_id, f["price"], set_={"fed_until": until}):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer(f"✅ خرید انجام شد!\n{f['emoji']} {f['name']} — {fa(f['price'])} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_feed, note=f"✅ <b>خرید انجام شد</b> — {f['emoji']} {f['name']} خوردن؛ {fa(f['hours'])} ساعت کار!")


@router.callback_query(F.data == "fam:cook")
async def cb_cook(cb: CallbackQuery, user: User) -> None:
    if user.cook:
        return await cb.answer("👨‍🍳 آشپز داری!", show_alert=True)
    await eco.settle(user.tg_id)
    if not await eco.spend(user.tg_id, C.COOK_PRICE, set_={"cook": True}, invest=C.COOK_PRICE):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer(f"✅ خرید انجام شد!\n👨‍🍳 آشپز — {fa(C.COOK_PRICE)} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_feed, note="✅ <b>خرید انجام شد</b> — 👨‍🍳 آشپز استخدام شد؛ از این به بعد بچه‌ها همیشه سیرن!")


# ───────────────────────── قوای جنسی / بچه ─────────────────────────
async def render_power(u: User):
    cap, w = kid_cap(u), left(u.child_ready_at)
    if not u.house:
        timer = "🏠 اول باید خونه‌ی سطح ۱ بخری."
    elif u.kids >= cap:
        timer = "🏠 ظرفیت خونه پره؛ خونه رو ارتقا بده."
    else:
        timer = "👶 آماده‌ای! می‌تونی بچه‌دار بشی." if w <= 0 else f"⏳ بچه‌ی بعدی: {dur(w)} دیگه"
    base = rules.base_income(u.businesses, u.staff, u.biz_level)                       # درآمد کاسبی هر ۶ ساعت (بدون تقویتی)

    def _gain(cfg: dict) -> int:                                  # سود تقریبی تقویتی تو مدتش
        return int(base * cfg["boost"] * cfg["boost_hours"] * 3600 / C.CYCLE_SECONDS)

    def _active(until) -> str:
        return f" — ✅ فعاله: {dur(left(until))} دیگه" if left(until) > 0 else ""
    if base > 0:
        mine = (f"\n\n📈 <b>برای کسب‌وکار فعلی تو</b> (هر ۶ ساعت {fa(base)} دلار):\n"
                f"🍌 موز تو {fa(C.BANANA['boost_hours'])} ساعت حدود <b>+{fa(_gain(C.BANANA))}</b> دلار بیشتر درمیاره (خرجش {fa(C.BANANA['price'])})\n"
                f"🧪 معجون تو {fa(C.POTION['boost_hours'])} ساعت حدود <b>+{fa(_gain(C.POTION))}</b> دلار بیشتر درمیاره (خرجش {fa(C.POTION['price'])})")
    else:
        mine = "\n\n🏪 هنوز کسب‌وکاری نداری؛ تقویتی درآمد کاسبی فقط وقتی به دردت می‌خوره که کسب‌وکار داشته باشی."
    text = (f"💪 <b>قوای جنسی</b>\n\n👶 بچه‌ها: {fa(u.kids)}/{fa(cap)}\n{timer}\n\n"
            f"هر {fa(C.CHILD_COOLDOWN_H)} ساعت می‌شه یه بچه ساخت.\n💬 دستور متنی: <code>بزن توش</code>\n\n"
            f"🍌 <b>موز</b>{_active(u.banana_until)}\n"
            f"• {fa(C.BANANA['cut_hours'])} ساعت از تایمر بچه‌دار شدن کم می‌کنه\n"
            f"• تا {fa(C.BANANA['boost_hours'])} ساعت، پولِ کسب‌وکارهات {int(C.BANANA['boost'] * 100)}٪ بیشتر جمع می‌شه\n\n"
            f"🧪 <b>معجون</b>{_active(u.potion_until)}\n"
            f"• تایمر بچه‌دار شدن رو یکدفعه صفر می‌کنه\n"
            f"• تا {fa(C.POTION['boost_hours'])} ساعت، پولِ کسب‌وکارهات {int(C.POTION['boost'] * 100)}٪ بیشتر جمع می‌شه (۱٫۵ برابر)\n\n"
            f"ℹ️ <b>«درآمد کاسبی» یعنی چی؟</b> همون پولی که از 🏪 کسب‌وکارهات (منوی کاسبی) جمع می‌شه. "
            f"فقط همون بیشتر می‌شه؛ پول کار، جیبی و چرخ تو شهر تغییر نمی‌کنه. "
            f"موز و معجون و بچه‌های سیر با هم جمع می‌شن. دوباره بخری، {fa(C.POTION['boost_hours'])} ساعت به مدتش اضافه می‌شه (درصدش دو برابر نمی‌شه)."
            f"{mine}")
    rows = []
    if u.house and u.kids < cap and w <= 0:
        rows.append([b("👶 بچه‌دار شو", "fam:child")])
    rows.append([b(f"🍌 موز — {fa(C.BANANA['price'])}", "fam:banana")])
    rows.append([b(f"🧪 معجون — {fa(C.POTION['price'])}", "fam:potion")])
    rows.append(back("fam", "◀️ خانواده"))
    return text, kb(*rows)


@router.callback_query(F.data == "fam:power")
async def cb_power(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_power)


@router.callback_query(F.data == "fam:child")
async def cb_child(cb: CallbackQuery, user: User) -> None:
    ok, msg = await actions.baby(user.tg_id)
    if not ok:
        return await cb.answer(msg, show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_power, note=msg)


@router.callback_query(F.data.in_({"fam:banana", "fam:potion"}))
async def cb_boost(cb: CallbackQuery, user: User) -> None:
    banana = cb.data == "fam:banana"
    cfg = C.BANANA if banana else C.POTION
    if user.cash < cfg["price"]:
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.settle(user.tg_id)
    u = await eco.get_user(user.tg_id)
    now = utcnow()
    field = "banana_until" if banana else "potion_until"
    until = max(now, getattr(u, field) or now) + timedelta(hours=cfg["boost_hours"])
    set_ = {field: until}
    if banana:
        if u.child_ready_at:
            set_["child_ready_at"] = u.child_ready_at - timedelta(hours=cfg["cut_hours"])
    else:
        set_["child_ready_at"] = None
    if not await eco.spend(user.tg_id, cfg["price"], set_=set_):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    name = "🍌 موز" if banana else "🧪 معجون"
    await cb.answer(f"✅ خرید انجام شد!\n{name} — {fa(cfg['price'])} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_power,
                note=f"✅ <b>خرید انجام شد</b> — {name} — {fa(cfg['price'])} دلار\n"
                     + ("🍌 خوردی! انرژی گرفتی." if banana else "🧪 معجون رو سر کشیدی! 🔥 تایمر بچه صفر شد."))


# ───────────────────────── همسر ─────────────────────────
async def render_marry(u: User):
    if u.spouse_id:
        sp = await eco.get_user(u.spouse_id)
        lock = left(u.love_lock_until)
        text = (f"💍 <b>همسر</b>\n\nهمسرت: <b>{esc(sp.name if sp else '؟')}</b>\n"
                f"بونوس ازدواج: +{int(C.MARRIAGE_BONUS * 100)}٪ درآمد کاسبی برای هر دو نفر")
        if u.mahr > 0:
            who = "بدهکارش تویی" if u.mahr_to == u.spouse_id else "طلبکارش تویی"
            text += f"\n💰 مهریه: <b>{fa(u.mahr)}</b> دلار ({who})"
        text += f"\n💞 جایزه‌ی زوجی: دستور <code>بکنش</code> هر {fa(C.INTIMACY_COOLDOWN_MIN)} دقیقه"
        if lock > 0:
            text += f"\n💘 طلسم عاشقی فعاله؛ {dur(lock)} دیگه نمی‌تونی طلاق بدی."
        rows = [[b("📨 درخواست طلاق", "fam:divorce")]] if lock <= 0 else []
        text += "\n\n💬 دستور متنی: <code>درخواست طلاق</code>"
    else:
        text = ("💍 <b>همسر</b>\n\nمجردی. ازدواج = +۱۰٪ درآمد کاسبی برای هر دو نفر.\n\n"
                "• پیوی: دکمه‌ی پایین رو بزن و یوزرنیم طرف رو بفرست؛ بهش پیشنهاد می‌ره.\n"
                "• گروه: روی پیام طرف ریپلای کن و بنویس <code>ثبت ازدواج</code>\n"
                "• 💘 با «طلسم عاشقی» (دارک وب) بدون اجازه هم می‌شه!")
        rows = [[b("💍 پیشنهاد ازدواج", "fam:propose")]]
    rows.append(back("fam", "◀️ خانواده"))
    return text, kb(*rows)


@router.callback_query(F.data == "fam:marry")
async def cb_marry(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_marry)


@router.callback_query(F.data == "fam:propose")
async def cb_propose(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    if user.spouse_id:
        return await cb.answer("تو که متاهلی!", show_alert=True)
    await ask_target(cb, state, "marry")


@router.callback_query(F.data == "fam:divorce")
async def cb_divorce(cb: CallbackQuery, user: User) -> None:
    res = await actions.divorce_request(cb.bot, user, cb.message.chat.id, in_group=cb.message.chat.type != "private")
    await cb.answer(res or "📨", show_alert=True)


@router.callback_query(F.data == "fam:guide")
async def cb_guide(cb: CallbackQuery) -> None:
    await cb.answer()
    text = ("📋 <b>راهنمای خانواده</b>\n\n"
            "👶 هر ۲۴ ساعت یه بچه؛ ظرفیت به سطح خونه بستگی داره.\n"
            "🍽 بچه‌ی گرسنه کار نمی‌کنه. سیب/همبرگر/قرمه‌سبزی/اکبرجوجه یا آشپز دائمی.\n"
            "📈 هر بچه‌ی سیر +۲۵٪ درآمد کاسبی (با هم جمع می‌شن).\n"
            "💍 همسر: +۱۰٪ درآمد برای هر دو نفر.\n"
            "💰 مهریه: موقع ازدواج می‌شه تعیین کرد؛ اگه طرف راضی به طلاق نبود می‌تونه «مطالبه‌ی مهریه» بزنه.\n"
            "🍌🧪 موز و معجون: تایمر بچه رو کم می‌کنن و درآمد رو بالا می‌برن.\n\n"
            "<b>دستورها</b> (ریپلای روی طرف، توی گروه یا پیوی با @یوزر):\n"
            "<code>ثبت ازدواج</code> · <code>ثبت ازدواج 1m</code> (با مهریه) · <code>درخواست طلاق</code>\n"
            "<code>بزن توش</code> / <code>بکنش</code> (بدون ریپلای؛ زوج‌ها هر ۲ دقیقه جایزه‌ی ۵۰۰ تا ۳٬۰۰۰ دلاری می‌گیرن)\n<code>طلسم عاشقی</code> · <code>طلسم طلاق</code>\n"
            "<code>حمله اجنه</code> · <code>دزد</code> · <code>حمله</code> · <code>دوز 5000</code> · <code>تاس 5000</code>")
    await edit(cb, text, kb(back("fam", "◀️ خانواده")))


# ───────────────────────── گاوصندوق ─────────────────────────
async def render_vault(u: User):
    text = (f"🔐 <b>گاوصندوق</b>\n\n💵 جیب: <b>{fa(u.cash)}</b>\n🔐 گاوصندوق: <b>{fa(u.vault)}</b>\n\n"
            "پولِ جیب با حمله قابل غارته، ولی گاوصندوق تقریباً امنه: حمله‌ی موفق فقط "
            f"{int(C.VAULT_LEAK * 100)}٪ ازش می‌بره و فقط «دزد گاوصندوق» دارک وب می‌تونه ۲۰ تا ۵۰٪ بدزده.")
    return text, kb(
        [b("📥 واریز ۲۵٪", "vault:dep:25"), b("📥 ۵۰٪", "vault:dep:50"), b("📥 همه", "vault:dep:100")],
        [b("📤 برداشت ۲۵٪", "vault:wd:25"), b("📤 ۵۰٪", "vault:wd:50"), b("📤 همه", "vault:wd:100")],
        back("home", "◀️ خونه"))


@router.callback_query(F.data == "vault")
async def cb_vault(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_vault)


@router.callback_query(F.data.startswith("vault:"))
async def cb_vault_move(cb: CallbackQuery, user: User) -> None:
    _, op, pct = cb.data.split(":")
    u = await eco.get_user(user.tg_id)
    amount = (u.cash if op == "dep" else u.vault) * int(pct) // 100
    if amount <= 0:
        return await cb.answer("چیزی برای جابجایی نیست.", show_alert=True)
    ok = await (eco.to_vault(u.tg_id, amount) if op == "dep" else eco.from_vault(u.tg_id, amount))
    if not ok:
        return await cb.answer("⏳ دوباره امتحان کن.", show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_vault,
                note=("📥 " if op == "dep" else "📤 ") + f"{fa(amount)} دلار " + ("به گاوصندوق رفت." if op == "dep" else "اومد تو جیبت."))


# ───────────────────────── کمد ─────────────────────────
async def render_closet(u: User):
    atk, ws = rules.attack_power(u.gear, u.forces)
    dfn, sh, se = rules.defense_power(u.gear, u.forces)
    lines = [f"🗄 <b>کمد</b>\n\n⚔️ قدرت حمله: <b>{fa(atk)}</b>   🛡 قدرت دفاع: <b>{fa(dfn)}</b>\n"
             f"🥷 قدرت دزدی: <b>{fa(rules.theft_power(u.gear))}</b>   🔒 دفاع کمد: <b>{fa(rules.home_defense(u.gear, u.forces)[0])}</b>   "
             f"💻 قدرت هک/ضدهک: <b>{fa(rules.hack_power(u.forces))}</b>\n"]
    any_gear = False
    for cat, c in C.SHOP.items():
        own = rules.owned(u.gear, cat)
        if not own:
            continue
        any_gear = True
        lines.append(f"{c['emoji']} <b>{c['name']}</b>")
        for i, it in enumerate(own):
            mark = " ⭐" if i < (2 if cat == "shield" else 1) else ""
            lines.append(f"  {it['emoji']} {it['name']} ({c['stat']}{fa(it['power'])}){mark}")
    if not any_gear:
        lines.append("هنوز چیزی از بازار نخریدی. 🛒")
    dark = [(k, rules.DARK_INDEX[k], n) for k, n in u.items.items() if n > 0 and k in rules.DARK_INDEX]
    if dark:
        lines.append("\n🕶 <b>دارک وب</b>")
        for k, d, n in dark:
            extra = f" ({fa(n)} بار دفاع)" if k == "jinngir" else f" × {fa(n)}"
            lines.append(f"  {d['emoji']} {d['name']}{extra}")
    lines.append("\n<i>⭐ = اثر داره (از هر دسته فقط قوی‌ترین؛ از سپرها دو تای قوی‌تر).</i>")
    rows = [[b(f"🎯 استفاده از {d['name']}", f"use:{k}")] for k, d, _ in dark if k in ("jinn", "vthief", "love", "divorce")]
    rows.append([b("💰 فروش وسایل کمد", "sell")])
    rows.append(back("home", "◀️ خونه"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "closet")
async def cb_closet(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_closet)


# ───────────────────────── نیروها ─────────────────────────
async def render_forces(u: User):
    cnt, cap = rules.force_count(u.forces), rules.force_cap(u.house)
    text = (f"💂 <b>نیروها</b> — {fa(cnt)}/{fa(cap)}\n\n"
            "💂 نگهبان: دفاع (از کمد و خونه هم محافظت می‌کنه)\n💻 هکر: حمله + جلوگیری از هک شدن\n🕴 بادیگارد: همه‌فن‌حریف — ۷۵٪ حمله + ۷۵٪ دفاع (تو هک و ضدهک هم ۷۵٪). با یه جای نیرو، هم حمله می‌کنه هم دفاع\n\n"
            "هر نیرو همیشه فعاله؛ ظرفیت نیرو با ارتقای خونه بیشتر می‌شه.")
    rows = []
    for cat, c in C.FORCES.items():
        have = sum(u.forces.get(f"{c['code']}{i}", 0) for i in range(3))
        rows.append([b(f"{c['emoji']} {c['name']}ها ({fa(have)})", f"forces:{cat}")])
    rows.append([b("💰 فروش نیرو", "sell:force"), b("🚪 اخراج نیرو", "fire:force")])
    rows.append(back("home", "◀️ خونه"))
    return text, kb(*rows)


async def render_force_cat(u: User, cat: str):
    c = C.FORCES[cat]
    cnt, cap = rules.force_count(u.forces), rules.force_cap(u.house)
    lines = [f"{c['emoji']} <b>{c['name']}ها</b> — کل نیرو: {fa(cnt)}/{fa(cap)}\n"]
    rows = []
    for i, (name, power, price) in enumerate(c["tiers"]):
        n = u.forces.get(f"{c['code']}{i}", 0)
        lines.append(f"• {name}: قدرت {fa(power)} — {fa(price)} دلار — داری: {fa(n)}")
        rows.append([b(f"خرید {name} — {fa(price)}", f"forces:buy:{c['code']}{i}")])
    rows.append(back("forces", "◀️ نیروها"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "forces")
async def cb_forces(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_forces)


@router.callback_query(F.data.regexp(r"^forces:(guard|hacker|bodyguard)$"))
async def cb_force_cat(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_force_cat, cb.data.split(":")[1])


@router.callback_query(F.data.startswith("forces:buy:"))
async def cb_force_buy(cb: CallbackQuery, user: User) -> None:
    code = cb.data.split(":")[2]
    f = rules.FORCE_INDEX.get(code)
    if not f:
        return await cb.answer()
    if rules.force_count(user.forces) >= rules.force_cap(user.house):
        return await cb.answer("🏠 ظرفیت نیرو پره! خونه رو ارتقا بده.", show_alert=True)
    if not await eco.spend(user.tg_id, f["price"], inc={f"forces.{code}": 1}, invest=f["price"]):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await cb.answer(f"✅ خرید انجام شد!\n{f['name']} — {fa(f['price'])} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_force_cat, f["cat"], note=f"✅ <b>خرید انجام شد</b> — {f['name']} استخدام شد!")


# ───────────────────────── ارتقای خونه ─────────────────────────
async def render_house(u: User):
    lines = ["⬆️ <b>ارتقای خونه</b>\n"]
    for lvl, (cap, price) in C.HOUSES.items():
        mark = "✅" if u.house >= lvl else ("👉" if lvl == u.house + 1 else "▫️")
        lines.append(f"{mark} سطح {fa(lvl)}: ظرفیت بچه {fa(cap)} — {fa(price)} دلار")
    rows = []
    if u.house < C.MAX_HOUSE:
        cap, price = C.HOUSES[u.house + 1]
        rows.append([b(f"⬆️ خرید سطح {fa(u.house + 1)} — {fa(price)}", "house:up")])
    else:
        lines.append("\n👑 خونه‌ات به بالاترین سطح رسیده!")
    rows.append(back("home", "◀️ خونه"))
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "house")
async def cb_house(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_house)


@router.callback_query(F.data == "house:up")
async def cb_house_up(cb: CallbackQuery, user: User) -> None:
    if user.house >= C.MAX_HOUSE:
        return await cb.answer("به سقف رسیدی!", show_alert=True)
    nxt = user.house + 1
    price = C.HOUSES[nxt][1]
    if not await eco.spend(user.tg_id, price, set_={"house": nxt}, flt={"house": user.house}, invest=price):
        return await cb.answer("💸 پول کافی نداری!", show_alert=True)
    await eco.add_xp(user.tg_id, C.XP["house"])
    await cb.answer(f"✅ خرید انجام شد!\n🏠 خونه سطح {fa(nxt)} — {fa(price)} دلار", show_alert=True)
    await _show(cb, user.tg_id, render_house, note=f"✅ <b>خرید انجام شد</b> — 🏠 خونه‌ات شد سطح {fa(nxt)}!")
