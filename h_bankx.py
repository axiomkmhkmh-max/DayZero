"""رابط بانک کامل: کارت من، کارت‌به‌کارت (پنل + دستور متنی)، صورت‌حساب، امتیاز اعتباری، سطح حساب، مسدودسازی کارت.
منطق و عملیات پولی توی bankx.py هست.

دستورهای متنی (گروه با ریپلای / @یوزر / #آیدی ، پیوی با @یوزر یا شماره‌ی کارت):
  کارت به کارت 5m · کارت به کارت 5m بابت قرض  · کارت به کارت 7010123412341234 2m
  کارت من · صورتحساب · امتیاز اعتباری · سطح حساب · مسدود کارت · رفع مسدودی
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import bank
import bankx
import config as C
import eco
import h_target
from db import User
from redis_service import redis_service
from states import BankAsk
from ui import DANGER, PRIMARY, SUCCESS, b, back, edit, in_group, kb, reply_owned, replying_to_bot
from util import esc, fa, parse_amount

router = Router()

CANCEL_WORDS = ("لغو", "/cancel", "/menu", "/start")
USAGE = ("🔁 <b>کارت به کارت</b> (از موجودی بانک؛ بدون سقف، فقط کارمزد بسته به سطح حساب)\n"
         "روی پیام طرف ریپلای کن و بنویس: <code>کارت به کارت 5m</code>\n"
         "با شماره‌ی کارت: <code>کارت به کارت 7010123412341234 2m</code>\n"
         "پیوی: <code>کارت به کارت @یوزر 500k</code>\n"
         "می‌تونی بعد از مبلغ یه توضیح هم بنویسی.\n\n"
         "💡 «انتقال» پول <b>نقدی</b> جیب رو می‌فرسته؛ «کارت به کارت» پول <b>بانک</b> رو.")


async def _fresh(uid: int) -> User:
    await bank.accrue(uid)
    return await eco.get_user(uid)


# ───────────────────────── صفحه‌ها ─────────────────────────
async def render_card(u: User):
    await bank.ensure_fields(u.tg_id)
    card = await bankx.ensure_card(u.tg_id)
    u = await _fresh(u.tg_id)
    rows = [[b("🔁 کارت به کارت", "bx:send", style=SUCCESS)],
            [b("✅ رفع مسدودی کارت" if u.card_frozen else "🧊 مسدود کردن کارت", "bx:freeze", style=None if u.card_frozen else DANGER)],
            [b("🧾 صورت‌حساب", "bx:hist:0", style=PRIMARY), b("📊 امتیاز اعتباری", "bx:credit", style=PRIMARY)],
            [b("⭐ سطح حساب", "bx:tier", style=PRIMARY)],
            back("bank", "◀️ بانک")]
    return bankx.card_text(u, card), kb(*rows)


async def render_hist(uid: int, page: int = 0):
    rows, total = await bankx.history(uid, max(0, page))
    pages = max(1, (total + bankx.PAGE - 1) // bankx.PAGE)
    page = max(0, min(page, pages - 1))
    if page != 0 and not rows:
        rows, total = await bankx.history(uid, page)
    body = "\n\n".join(bankx.tx_line(r) for r in rows) if rows else "هنوز تراکنشی نداری."
    text = f"🧾 <b>صورت‌حساب</b> — صفحه {fa(page + 1)} از {fa(pages)}\n\n{body}"
    nav = []
    if page > 0:
        nav.append(b("◀️ جدیدتر", f"bx:hist:{page - 1}"))
    if page + 1 < pages:
        nav.append(b("قدیمی‌تر ▶️", f"bx:hist:{page + 1}"))
    return text, kb(nav, back("bx:card", "◀️ کارت من"))


async def render_tier(u: User):
    u = await _fresh(u.tg_id)
    lvl = max(1, u.bank_level)
    rows = []
    if lvl < len(C.BANK_TIERS):
        nxt = C.BANK_TIERS[lvl]
        rows.append([b(f"⭐ ارتقا به {nxt['emoji']} {nxt['name']} — {fa(nxt['price'])}", "bx:up", style=SUCCESS)])
    rows.append(back("bx:card", "◀️ کارت من"))
    return bankx.tier_text(u) + f"\n\n🏦 موجودی بانک: {fa(u.bank)} · 💵 جیب: {fa(u.cash)}\n<i>(هزینه‌ی ارتقا اول از جیب، بعد از بانک کم می‌شه)</i>", kb(*rows)


# ───────────────────────── دکمه‌ها ─────────────────────────
@router.callback_query(F.data == "bx:card")
async def cb_card(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    text, markup = await render_card(user)
    await edit(cb, text, markup)


@router.callback_query(F.data == "bx:freeze")
async def cb_freeze(cb: CallbackQuery, user: User) -> None:
    u = await eco.get_user(user.tg_id)
    await bankx.set_frozen(user.tg_id, not u.card_frozen)
    await cb.answer("🧊 کارت مسدود شد" if not u.card_frozen else "✅ کارت باز شد", show_alert=True)
    text, markup = await render_card(user)
    await edit(cb, text, markup)


@router.callback_query(F.data.regexp(r"^bx:hist:\d+$"))
async def cb_hist(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    text, markup = await render_hist(user.tg_id, int(cb.data.split(":")[2]))
    await edit(cb, text, markup)


@router.callback_query(F.data == "bx:credit")
async def cb_credit(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    u = await _fresh(user.tg_id)
    await edit(cb, bankx.credit_text(u), kb(back("bx:card", "◀️ کارت من")))


@router.callback_query(F.data == "bx:tier")
async def cb_tier(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    text, markup = await render_tier(user)
    await edit(cb, text, markup)


@router.callback_query(F.data == "bx:up")
async def cb_up(cb: CallbackQuery, user: User) -> None:
    ok, msg = await bankx.upgrade(user.tg_id)
    if not ok:
        return await cb.answer(msg.replace("<b>", "").replace("</b>", ""), show_alert=True)
    await cb.answer("⭐ ارتقا انجام شد!")
    text, markup = await render_tier(user)
    await edit(cb, msg + "\n\n" + text, markup)


# ───────────────────────── کارت‌به‌کارت: پیش‌نمایش و تایید ─────────────────────────
async def _dm_recipient(bot, rc: dict) -> None:
    await bank._dm(bot, rc["dst"], "💰 <b>پول به کارتت اومد!</b>\n\n" + bankx.receipt_text(rc, "dst"),
                   kb([b("🏦 بانک", "bank", style=PRIMARY)]))


async def do_transfer(bot, src: int, dst: int, amount: int, note: str = "") -> tuple[bool, str]:
    ok, err, rc = await bankx.transfer(src, dst, amount, note)
    if not ok:
        return False, err
    await _dm_recipient(bot, rc)
    return True, bankx.receipt_text(rc, "src")


async def build_preview(u: User, v: User, amount: int, note: str = "") -> tuple[Optional[str], Optional[object]]:
    """(متن پیش‌نمایش, دکمه‌ها) یا (None-خطا در متن)؛ خروجی: (متن, markup) — اگه markup None بود متن یه خطاست."""
    err = bankx.precheck(u, amount)
    if err:
        return err, None
    fee = bankx.fee_for(u.bank_level, amount)
    vcard = await bankx.ensure_card(v.tg_id)
    tok = uuid.uuid4().hex[:10]
    await redis_service.r.set(f"bx:p:{tok}", json.dumps({"src": u.tg_id, "dst": v.tg_id, "amount": amount, "note": note}),
                              ex=C.CARD_PENDING_TTL)
    text = (f"🔁 <b>تایید کارت به کارت</b>\n\n"
            f"👤 به: <b>{esc(v.name)}</b>\n💳 <code>{bankx.mask_card(vcard)}</code>\n\n"
            f"💰 مبلغ: <b>{fa(amount)}</b>\n🧾 کارمزد ({bankx.pct(bankx.tier(u.bank_level)['fee'])}٪): {fa(fee)}\n"
            f"➖ مجموع کسر از بانک: <b>{fa(amount + fee)}</b>\n🏦 مانده بعد از انتقال: {fa(u.bank - amount - fee)}"
            + (f"\n📝 {esc(note)}" if note else "")
            + f"\n\n⏳ تا {fa(C.CARD_PENDING_TTL // 60)} دقیقه‌ی دیگه اعتبار داره.")
    markup = kb([b("✅ تایید و انتقال", f"bx:ok:{tok}", style=SUCCESS), b("❌ لغو", f"bx:no:{tok}", style=DANGER)])
    return text, markup


@router.callback_query(F.data.regexp(r"^bx:(ok|no):[0-9a-f]{10}$"))
async def cb_confirm(cb: CallbackQuery, user: User) -> None:
    _, op, tok = cb.data.split(":")
    key = f"bx:p:{tok}"
    raw = await redis_service.r.get(key)
    if raw is None:
        await cb.answer("⌛ این درخواست منقضی شده؛ دوباره کارت به کارت بزن.", show_alert=True)
        return await edit(cb, "⌛ درخواست منقضی شد.", kb(back("bank", "◀️ بانک")))
    d = json.loads(raw)
    if d["src"] != user.tg_id:
        return await cb.answer("🚫 این تایید مال تو نیست.", show_alert=True)
    if not await redis_service.r.delete(key):                  # فقط یک بار مصرف می‌شه (ضد دوبار-کلیک)
        return await cb.answer("⏳")
    if op == "no":
        await cb.answer("لغو شد")
        return await edit(cb, "❌ کارت به کارت لغو شد.", kb(back("bank", "◀️ بانک")))
    ok, text = await do_transfer(cb.bot, d["src"], d["dst"], d["amount"], d.get("note", ""))
    if not ok:
        await cb.answer(text.replace("<b>", "").replace("</b>", ""), show_alert=True)
        return await edit(cb, "❌ " + text, kb(back("bank", "◀️ بانک")))
    await cb.answer("✅ انجام شد")
    await edit(cb, text, kb([b("🧾 صورت‌حساب", "bx:hist:0", style=PRIMARY), b("🔁 انتقال دیگر", "bx:send", style=SUCCESS)],
                            back("bank", "◀️ بانک")))


# ───────────────────────── پنل: گرفتن مقصد و مبلغ (FSM) ─────────────────────────
@router.callback_query(F.data == "bx:send")
async def cb_send(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    u = await _fresh(user.tg_id)
    if u.card_frozen:
        return await cb.answer("🧊 کارتت مسدوده؛ اول رفع مسدودی کن.", show_alert=True)
    if u.bank < C.CARD_MIN:
        return await cb.answer("🏦 موجودی بانکت کمه؛ اول پول به بانک واریز کن.", show_alert=True)
    await state.set_state(BankAsk.dest)
    await cb.answer()
    t = bankx.tier(u.bank_level)
    await edit(cb, "🔁 <b>کارت به کارت</b>\n\n🎯 <b>@یوزرنیم</b>، <b>آیدی عددی</b> یا <b>شماره‌ی کارت ۱۶ رقمی</b> مقصد رو بفرست.\n"
                   "می‌تونی مبلغ رو هم کنارش بنویسی: <code>@ali 5m</code>\n\n"
                   f"🏦 موجودی: {fa(u.bank)} · بدون سقف انتقال · کارمزد {bankx.pct(t['fee'])}٪"
               + ("\n\n↩️ <b>روی همین پیام ریپلای کن</b> و جوابت رو بفرست." if in_group(cb) else ""),
               kb([b("❌ لغو", "bank", style=DANGER)]))


async def _locked(m: Message, user: Optional[User], state: FSMContext, fn) -> None:
    if user is None:                                   # گروه: میان‌افزار کاربر رو نساخته
        if m.from_user is None or m.from_user.is_bot:
            return
        async with redis_service.lock(f"u:{m.from_user.id}", ttl=20, wait=6):
            actor, _ = await eco.ensure_user(m.from_user)
            return await fn(m, actor, state)
    return await fn(m, user, state)


def _split_amount_note(tokens: list[str], available: int) -> tuple[Optional[int], str]:
    amount, note = None, []
    for t in tokens:
        if t.startswith(("@", "#")):
            continue
        if amount is None:
            a = parse_amount(t, available)
            if a:
                amount = a
                continue
        else:
            note.append(t)
    return amount, " ".join(note)[:60]


async def _preview_reply(m: Message, u: User, v: User, amount: int, note: str) -> None:
    text, markup = await build_preview(u, v, amount, note)
    await reply_owned(m, text, markup or kb(back("bank", "◀️ بانک")))


@router.message(StateFilter(BankAsk.dest), F.func(replying_to_bot), F.text)
async def got_dest(m: Message, state: FSMContext, user: User = None) -> None:
    await _locked(m, user, state, _got_dest)


async def _got_dest(m: Message, user: User, state: FSMContext) -> None:
    txt = (m.text or "").strip()
    if txt in CANCEL_WORDS:
        await state.clear()
        return await reply_owned(m, "❌ لغو شد.")
    toks = txt.split()
    card, rest = bankx.extract_card(toks)
    if card:
        dst = await bankx.find_by_card(card)
    else:
        dst, rest = await h_target.resolve(toks[0] if toks else ""), toks[1:]
    if dst is None:
        return await reply_owned(m, "🤷 همچین کارت/بازیکنی پیدا نشد. دوباره بفرست یا «لغو» بنویس.")
    if dst.tg_id == user.tg_id:
        return await reply_owned(m, "😅 به خودت که نمی‌تونی بفرستی! یکی دیگه رو بفرست.")
    u = await _fresh(user.tg_id)
    amount, note = _split_amount_note(rest, bankx.max_sendable(u))
    if amount:
        await state.clear()
        return await _preview_reply(m, u, dst, amount, note)
    await state.set_state(BankAsk.amount)
    await state.update_data(dst=dst.tg_id)
    await reply_owned(m, f"👤 مقصد: <b>{esc(dst.name)}</b>\n\n💰 مبلغ رو بفرست (مثل <code>500k</code> یا <code>2m</code> یا <code>همه</code>).\n"
                         f"حداکثر الان: {fa(bankx.max_sendable(u))}")


@router.message(StateFilter(BankAsk.amount), F.func(replying_to_bot), F.text)
async def got_amount(m: Message, state: FSMContext, user: User = None) -> None:
    await _locked(m, user, state, _got_amount)


async def _got_amount(m: Message, user: User, state: FSMContext) -> None:
    txt = (m.text or "").strip()
    if txt in CANCEL_WORDS:
        await state.clear()
        return await reply_owned(m, "❌ لغو شد.")
    u = await _fresh(user.tg_id)
    amount, note = _split_amount_note(txt.split(), bankx.max_sendable(u))
    if not amount:
        return await reply_owned(m, "🤔 مبلغ نامعتبره. یه عدد بفرست (مثل 500k یا 2m) یا «لغو» بنویس.")
    dst = await eco.get_user((await state.get_data()).get("dst", 0))
    await state.clear()
    if dst is None:
        return await reply_owned(m, "😕 مقصد پیدا نشد؛ از اول شروع کن.")
    await _preview_reply(m, u, dst, amount, note)


# ───────────────────────── دستورهای متنی ─────────────────────────
async def text_ctc(m: Message, actor: User, rest: list[str], in_group: bool, slash: bool, target: Optional[User]) -> None:
    """«کارت به کارت <مبلغ> [توضیح]» + (ریپلای | @یوزر | #آیدی | شماره کارت)."""
    reply = m.reply if in_group else m.answer
    card, rest2 = bankx.extract_card(rest)
    dst = target
    if card:
        dst = await bankx.find_by_card(card)
        if dst is None:
            return await reply("💳 همچین شماره کارتی وجود نداره.")
    if dst is None:
        if slash or not in_group or not rest:               # توی گروه برای حرف‌های معمولی ساکت می‌مونیم
            await reply(USAGE)
        return
    await bank.ensure_fields(actor.tg_id)
    u = await _fresh(actor.tg_id)
    amount, note = _split_amount_note(rest2, bankx.max_sendable(u))
    if not amount:
        if in_group and not slash and rest2:
            return
        return await reply(USAGE)
    if dst.tg_id == actor.tg_id:
        return await reply("😅 به کارت خودت که نمی‌تونی بفرستی!")
    if amount >= C.CARD_CONFIRM_ABOVE:
        text, markup = await build_preview(u, dst, amount, note)
        return await reply_owned(m, text, markup)
    ok, text = await do_transfer(m.bot, actor.tg_id, dst.tg_id, amount, note)
    await reply(text if ok else "❌ " + text)


async def text_misc(m: Message, actor: User, cmd: str, in_group: bool) -> None:
    reply = m.reply if in_group else m.answer
    if cmd == "mycard":
        text, markup = await render_card(actor)
        return await reply_owned(m, text, markup)
    if cmd == "statement":
        text, markup = await render_hist(actor.tg_id, 0)
        return await reply_owned(m, text, markup)
    if cmd == "credit":
        u = await _fresh(actor.tg_id)
        return await reply_owned(m, bankx.credit_text(u), kb(back("bank", "◀️ بانک")))
    if cmd == "tierinfo":
        text, markup = await render_tier(actor)
        return await reply_owned(m, text, markup)
    if cmd in ("freezecard", "unfreezecard"):
        await bankx.set_frozen(actor.tg_id, cmd == "freezecard")
        return await reply("🧊 کارتت مسدود شد؛ کارت‌به‌کارت خروجی بسته‌ست. برای باز کردن بنویس «رفع مسدودی»." if cmd == "freezecard"
                           else "✅ کارتت باز شد.")


# ───────────────────────── راهنما ─────────────────────────
HELP = f"""💳 <b>کارت و کارت‌به‌کارت</b>

هر بازیکن یه <b>شماره‌ی کارت ۱۶ رقمی</b> داره (بنویس <code>کارت من</code>).
🔁 <b>کارت به کارت</b>: از پول <b>بانکت</b> به بانک طرف می‌فرستی (امن، قابل غارت نیست):
<code>کارت به کارت 5m</code> (ریپلای روی طرف) · <code>کارت به کارت 7010… 2m</code> (شماره کارت) · پیوی: <code>کارت به کارت @یوزر 500k</code>
از {fa(C.CARD_CONFIRM_ABOVE)} به بالا دکمه‌ی تایید می‌یاد؛ بعد از انتقال رسید با <b>کد پیگیری</b> برای هر دو نفر می‌ره.

⭐ <b>سطح حساب</b>: {" ← ".join(t['emoji'] + t['name'] for t in C.BANK_TIERS)}
هر سطح = کارمزد کمتر، سقف وام بالاتر و تخفیف بهره (بنویس <code>سطح حساب</code>).
📊 <b>امتیاز اعتباری</b> (۳۰۰ تا ۸۵۰): قسط به‌موقع و تسویه‌ی وام بالاش می‌بره؛ دیرکرد و ضبط دارایی پایین می‌آره؛ روی سقف و بهره‌ی وام اثر داره.
🧾 <code>صورتحساب</code> ← آخرین تراکنش‌ها · 🧊 <code>مسدود کارت</code> / <code>رفع مسدودی</code>

💸 «انتقال 1000» = پول <b>نقدی</b> جیب؛ «کارت به کارت» = پول <b>بانک</b>."""
