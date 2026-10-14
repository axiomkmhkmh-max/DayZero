"""بانک: سپرده با سود روزانه، ارتقای بانک، وام و قسط.  (منطق توی bank.py)"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

import bank
import config as C
import eco
import h_menu
import rules
from db import User
from ui import b, back, edit, kb
from util import dur, fa, left, parse_amount, utcnow

router = Router()


async def _fresh(uid: int) -> User:
    await bank.accrue(uid)
    return await eco.get_user(uid)


async def _show(cb: CallbackQuery, uid: int, render, *args, note: str = "") -> None:
    u = await _fresh(uid)
    text, markup = await render(u, *args)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


# ───────────────────────── صفحه‌ی اصلی بانک ─────────────────────────
async def render_bank(u: User):
    if u.tg_id:
        await bank.accrue(u.tg_id)
        u = await eco.get_user(u.tg_id) or u
    cap, lvl = bank.cap_of(u), u.bank_level
    text = (f"🏦 <b>بانک</b> — سطح {fa(lvl)}\n\n"
            f"💰 موجودی بانک: <b>{fa(u.bank)}</b> / {fa(cap)}\n"
            f"📈 سود: <b>{fa(int(C.BANK_INTEREST * 100))}٪ روزانه</b> (الان روزی ≈ {fa(bank.daily_interest(u))} دلار)\n"
            f"💵 تو جیبت: {fa(u.cash)}\n\n"
            "🔒 پول بانک کاملاً امنه؛ حمله، هک و دزد گاوصندوق بهش نمی‌رسن.\n"
            "ℹ️ سود هر ۲۴ ساعت یک بار به موجودی اضافه می‌شه و فقط روی مقدار تا سقف سطح بانک حساب می‌شه.")
    if bank.has_loan(u):
        st = bank.loan_state(u)
        text += (f"\n\n💳 <b>وام فعال</b>: باقی‌مونده {fa(st['remaining'])} دلار — قسط روزانه {fa(st['daily'])}"
                 "\n🚫 تا وقتی وام داری نمی‌تونی واریز کنی.")
    rows = [[b("📥 واریز ۲۵٪", "bank:dep:25"), b("📥 ۵۰٪", "bank:dep:50"), b("📥 همه", "bank:dep:100")],
            [b("📤 برداشت ۲۵٪", "bank:wd:25"), b("📤 ۵۰٪", "bank:wd:50"), b("📤 همه", "bank:wd:100")]]
    if lvl < C.MAX_BANK_LEVEL:
        ncap, cost = C.BANK_LEVELS[lvl + 1]
        nj = C.JOBS[C.BANK_LEVEL_JOB.get(lvl + 1, 0)]
        ok_job = rules.job_index(u.shifts) >= C.BANK_LEVEL_JOB.get(lvl + 1, 0)
        text += (f"\n\n⬆️ سطح بعدی: سقف {fa(ncap)} · هزینه {fa(cost)} · شغل لازم: {nj['emoji']} {nj['name']}"
                 + ("" if ok_job else " 🔒"))
        rows.append([b(f"⬆️ ارتقای بانک به سطح {fa(lvl + 1)} — {fa(cost)}" + ("" if ok_job else " 🔒"), "bank:up")])
    rows.append([b("💳 وام" + (" (فعال ⚠️)" if bank.has_loan(u) else ""), "bank:loan")])
    rows.append(back("menu", "◀️ منو"))
    return text, kb(*rows)


@router.callback_query(F.data == "bank")
async def cb_bank(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    await _show(cb, user.tg_id, render_bank)


@router.callback_query(F.data.regexp(r"^bank:(dep|wd):(25|50|100)$"))
async def cb_move(cb: CallbackQuery, user: User) -> None:
    _, op, pct = cb.data.split(":")
    await bank.accrue(user.tg_id)
    u = await eco.get_user(user.tg_id)
    base = u.cash if op == "dep" else u.bank
    amount = base * int(pct) // 100
    if amount <= 0:
        return await cb.answer("چیزی برای جابجایی نیست.", show_alert=True)
    ok, msg = await (bank.deposit if op == "dep" else bank.withdraw)(user.tg_id, amount)
    if not ok:
        return await cb.answer(msg.replace("<b>", "").replace("</b>", ""), show_alert=True)
    await cb.answer()
    await _show(cb, user.tg_id, render_bank, note=msg)


@router.callback_query(F.data == "bank:up")
async def cb_up(cb: CallbackQuery, user: User) -> None:
    ok, msg = await bank.upgrade(user.tg_id)
    if not ok:
        return await cb.answer(msg.replace("<b>", "").replace("</b>", ""), show_alert=True)
    await cb.answer("✅ خرید انجام شد!")
    await _show(cb, user.tg_id, render_bank, note="✅ <b>خرید انجام شد</b> — " + msg)


# ───────────────────────── وام ─────────────────────────
async def render_loan(u: User):
    if bank.has_loan(u):
        st = bank.loan_state(u)
        paid_pct = int(u.loan_paid * 100 / max(1, u.loan_total))
        text = (f"💳 <b>وام فعال</b>\n\n"
                f"💵 مبلغ وام: {fa(u.loan_principal)}   📄 کل بدهی (با بهره): <b>{fa(u.loan_total)}</b>\n"
                f"✅ پرداخت‌شده: {fa(u.loan_paid)} ({fa(paid_pct)}٪)   🧾 باقی‌مونده: <b>{fa(st['remaining'])}</b>\n"
                f"📅 قسط روزانه: <b>{fa(st['daily'])}</b> دلار\n")
        if st["behind"] > 0:
            text += (f"\n🚨 <b>{fa(st['behind'])} قسط عقب‌افتاده</b> ({fa(st['owed_now'])} دلار)!\n"
                     f"⏳ {fa(st['days_left'])} روز دیگه وقت داری وگرنه 🏠 خونه و 🏪 کاسبی‌هات ضبط می‌شن.")
        else:
            nd = left(st["next_due"])
            text += f"\n✅ قسط‌هات به‌روزه. موعد قسط بعدی: {dur(nd)} دیگه"
        text += "\n\n💡 پرداخت اول از جیب و اگه نبود از بانکت کم می‌شه."
        pay_label = f"💳 پرداخت قسط — {fa(min(st['remaining'], max(st['daily'], st['owed_now'])))}"
        return text, kb([b(pay_label, "bank:pay")], [b(f"🧾 صاف کردن کل وام — {fa(st['remaining'])}", "bank:payall")],
                        back("bank", "◀️ بانک"))
    cap = bank.max_loan(u)
    job = C.JOBS[rules.job_index(u.shifts)]
    text = (f"💳 <b>وام</b>\n\n"
            f"🏪 ارزش کاسبی‌هات (با کارگرها): <b>{fa(bank.biz_value(u))}</b>\n"
            f"🏠 ارزش خونه‌ات: <b>{fa(bank.house_value(u))}</b>\n"
            f"💼 سقف شغلی ({job['emoji']} {job['name']}): <b>{fa(bank.job_limit(u))}</b>\n"
            f"🔓 سقف وام تو (کمترین این دو): <b>{fa(cap)}</b> دلار\n\n"
            f"📜 <b>شرایط</b>\n• مدت {fa(C.LOAN_DAYS)} روز، بهره {fa(int(C.LOAN_INTEREST * 100))}٪\n"
            f"• هر روز یه قسط پرداخت می‌کنی؛ اگه ندی یادآوری می‌گیری\n"
            f"• {fa(C.LOAN_MAX_OVERDUE_DAYS)} روز قسط ندی = ضبط 🏠 خونه و 🏪 کاسبی‌هات\n"
            "• تا وقتی وام داری واریز به بانک بسته‌ست")
    if cap < C.LOAN_MIN:
        return text + "\n\n🏚 وثیقه‌ای نداری؛ اول یه کاسبی یا خونه بخر.", kb(back("bank", "◀️ بانک"))
    rows = [[b(f"💳 وام {fa(cap * p // 100)} ({fa(p)}٪)", f"bank:take:{p}")] for p in (25, 50, 100) if cap * p // 100 >= C.LOAN_MIN]
    rows.append(back("bank", "◀️ بانک"))
    return text, kb(*rows)


@router.callback_query(F.data == "bank:loan")
async def cb_loan(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    if await bank.check_default(cb.bot, user.tg_id):         # موعدش گذشته بود → ضبط شد
        return await _show(cb, user.tg_id, render_bank, note="🚨 دارایی‌هات به‌خاطر قسط‌های پرداخت‌نشده ضبط شد.")
    await _show(cb, user.tg_id, render_loan)


@router.callback_query(F.data.regexp(r"^bank:take:(25|50|100)$"))
async def cb_take(cb: CallbackQuery, user: User) -> None:
    pct = int(cb.data.split(":")[2])
    u = await eco.get_user(user.tg_id)
    ok, msg = await bank.take_loan(user.tg_id, bank.max_loan(u) * pct // 100)
    if not ok:
        return await cb.answer(msg.replace("<b>", "").replace("</b>", ""), show_alert=True)
    await cb.answer("✅ وام واریز شد!")
    await _show(cb, user.tg_id, render_loan, note="✅ " + msg)


@router.callback_query(F.data.in_({"bank:pay", "bank:payall"}))
async def cb_pay(cb: CallbackQuery, user: User) -> None:
    u = await eco.get_user(user.tg_id)
    if not bank.has_loan(u):
        await cb.answer("وامی نداری 👌", show_alert=True)
        return await _show(cb, user.tg_id, render_bank)
    amount = (u.loan_total - u.loan_paid) if cb.data == "bank:payall" else None
    ok, msg = await bank.pay_loan(user.tg_id, amount)
    if not ok:
        return await cb.answer(msg.replace("<b>", "").replace("</b>", ""), show_alert=True)
    await cb.answer("✅ پرداخت شد!")
    u = await eco.get_user(user.tg_id)
    await _show(cb, user.tg_id, render_loan if bank.has_loan(u) else render_bank, note=msg)


# ───────────────────────── دستورهای متنی (گروه و پیوی) ─────────────────────────
USAGE = ("🏦 <b>دستورهای بانک</b>\n<code>بانک</code> ← پنل بانک\n<code>واریز 5m</code> · <code>برداشت 2m</code> · <code>برداشت همه</code>\n"
         "<code>وام 10m</code> (تا سقف ارزش کاسبی و خونه‌ات) · <code>قسط</code> (پرداخت قسط روزانه)")


async def bank_text(m: Message, actor: User, cmd: str, rest: list[str], in_group: bool, slash: bool) -> None:
    reply = m.reply if in_group else m.answer
    uid = actor.tg_id
    if cmd == "payloan":
        ok, msg = await bank.pay_loan(uid)
        return await reply(msg)
    await bank.accrue(uid)
    u = await eco.get_user(uid)
    avail = {"bdep": u.cash, "bwd": u.bank, "loan": bank.max_loan(u)}[cmd]
    amount = next((a for a in (parse_amount(t, avail) for t in rest if not t.startswith(("@", "#"))) if a), None)
    if not amount:
        if in_group and not slash:          # «وام» وسط حرف‌های عادی گروه: ساکت
            return
        return await reply(USAGE)
    if cmd == "loan":
        if bank.has_loan(u):
            st = bank.loan_state(u)
            return await reply(f"🚫 یه وام فعال داری (باقی‌مونده {fa(st['remaining'])}). با «قسط» پرداخت کن.")
        ok, msg = await bank.take_loan(uid, amount)
    elif cmd == "bdep":
        ok, msg = await bank.deposit(uid, amount)
    else:
        ok, msg = await bank.withdraw(uid, amount)
    await reply(msg)


# ───────────────────────── راهنما ─────────────────────────
_HELP = f"""🏦 <b>بانک و وام</b>

💰 <b>سپرده</b>: پولت رو بذار تو بانک؛ هر ۲۴ ساعت <b>{int(C.BANK_INTEREST * 100)}٪ سود</b> می‌گیری (مرکب). پول بانک از حمله، هک و دزد گاوصندوق امنه.
📏 سقف سپرده به <b>سطح بانک</b> بستگی داره ({fa(C.BANK_LEVELS[1][0])} تا {fa(C.BANK_LEVELS[C.MAX_BANK_LEVEL][0])}) و ارتقاش هم پول می‌خواد هم <b>شغل بالاتر</b>.

💳 <b>وام</b>: سقف = کمترِ «ارزش کاسبی‌ها + خونه‌ات» و «سقف شغلی» (هر چی شغلت بالاتر، وام بزرگ‌تر).
• {fa(C.LOAN_DAYS)} روز، بهره {int(C.LOAN_INTEREST * 100)}٪، هر روز یه قسط
• قسط رو ندی یادآوری می‌گیری؛ {fa(C.LOAN_MAX_OVERDUE_DAYS)} روز نپرداختن = <b>ضبط خونه و همه‌ی کاسبی‌ها</b> 🚨
• تا وقتی وام داری نمی‌تونی به بانک واریز کنی

💬 دستورها: <code>بانک</code> · <code>واریز 5m</code> · <code>برداشت 2m</code> · <code>وام 10m</code> · <code>قسط</code>"""

if not any(name == "🏦 بانک" for name, _ in h_menu.HELP_PAGES):
    h_menu.HELP_PAGES.append(("🏦 بانک", _HELP))
