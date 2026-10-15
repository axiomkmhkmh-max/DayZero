"""بانک کامل: کارت بانکی، کارت‌به‌کارت، سطح حساب، صورت‌حساب (دفتر تراکنش) و امتیاز اعتباری.

- هر بازیکن یه شماره‌ی کارت ۱۶ رقمیِ یکتا (با رقم کنترل Luhn) داره که اولین بار لازم شد ساخته می‌شه.
- کارت‌به‌کارت از «موجودی بانک» برداشت می‌شه و به «بانک» مقصد می‌ره (پول بانک امنه؛ نقدی نیست).
  با @یوزر، آیدی، ریپلای یا شماره‌ی کارت. کارمزد + سقف روزانه + سقف هر انتقال بسته به سطح حساب.
- همه‌ی عملیات پولی اتمیکه (شرط داخل فیلتر Mongo) و اگه واریزِ مقصد خراب بشه پول به مبدأ برمی‌گرده.
- دفتر تراکنش (Ledger) برای صورت‌حساب؛ امتیاز اعتباری ۳۰۰ تا ۸۵۰ که سقف و بهره‌ی وام رو تغییر می‌ده.
"""
from __future__ import annotations

import math
import random
import sys
import uuid
from datetime import timedelta
from typing import Any, Optional

import structlog
from pymongo.errors import DuplicateKeyError

import config as C
import eco
from db import Ledger, User
from util import en_digits, esc, fa, utcnow

log = structlog.get_logger("bankx")

TEHRAN = timedelta(hours=3, minutes=30)


def _emit(text: str) -> None:
    gl = sys.modules.get("gamelog")
    if gl is not None:
        try:
            gl.emit(text)
        except Exception:  # noqa: BLE001
            pass


# ───────────────────────── شماره‌ی کارت ─────────────────────────
def _luhn_check_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def luhn_ok(num: str) -> bool:
    return len(num) >= 2 and num.isdigit() and _luhn_check_digit(num[:-1]) == num[-1]


def new_card_number() -> str:
    body = C.CARD_PREFIX + "".join(random.choice("0123456789") for _ in range(15 - len(C.CARD_PREFIX)))
    return body + _luhn_check_digit(body)


def fmt_card(n: str) -> str:
    return " ".join(n[i:i + 4] for i in range(0, len(n), 4))


def mask_card(n: str) -> str:
    return f"{n[:4]} •••• •••• {n[-4:]}" if len(n) == 16 else n


def norm_card(text: str) -> Optional[str]:
    """«۷۰۱۰-۱۲۳۴ …» → ۱۶ رقم معتبر (Luhn) یا None."""
    d = "".join(ch for ch in en_digits(text) if ch.isdigit())
    return d if len(d) == 16 and luhn_ok(d) else None


def extract_card(tokens: list[str]) -> tuple[Optional[str], list[str]]:
    """از توکن‌های دستور، شماره‌ی کارت (یک توکن ۱۶ رقمی یا چهارتا ۴ رقمی پشت‌هم) رو بیرون می‌کشه.
    خروجی: (کارت یا None, توکن‌های باقی‌مانده)."""
    toks = [en_digits(t).replace("-", "") for t in tokens]
    for i, t in enumerate(toks):
        if t.isdigit() and len(t) == 16 and norm_card(t):
            return t, tokens[:i] + tokens[i + 1:]
        if i + 4 <= len(toks) and all(x.isdigit() and len(x) == 4 for x in toks[i:i + 4]):
            c = "".join(toks[i:i + 4])
            if norm_card(c):
                return c, tokens[:i] + tokens[i + 4:]
    return None, tokens


async def ensure_card(uid: int) -> str:
    col = eco.col()
    doc = await col.find_one({"tg_id": uid}, {"card_no": 1})
    if doc is None:
        return ""
    if doc.get("card_no"):
        return doc["card_no"]
    for _ in range(10):
        n = new_card_number()
        try:
            r = await col.update_one({"tg_id": uid, "card_no": None}, {"$set": {"card_no": n}})
        except DuplicateKeyError:
            continue
        if r.matched_count == 1:
            return n
        doc = await col.find_one({"tg_id": uid}, {"card_no": 1})      # یکی دیگه همزمان ساخته
        return (doc or {}).get("card_no") or ""
    return ""


async def find_by_card(card: str) -> Optional[User]:
    doc = await eco.col().find_one({"card_no": card}, {"tg_id": 1})
    return await eco.get_user(doc["tg_id"]) if doc else None


# ───────────────────────── سطح حساب ─────────────────────────
def pct(x: float) -> str:
    """۰٫۰۱۵ → «۱٫۵»  ·  ۰٫۰۲ → «۲»."""
    v = round(x * 100, 1)
    whole, tenth = int(v), int(round(v * 10)) % 10
    return fa(whole) + (f"٫{fa(tenth)}" if tenth else "")


def tier(level: int) -> dict:
    return C.BANK_TIERS[max(1, min(int(level or 1), len(C.BANK_TIERS))) - 1]


def tier_label(level: int) -> str:
    t = tier(level)
    return f"{t['emoji']} {t['name']}"


def fee_for(level: int, amount: int) -> int:
    return max(C.CARD_FEE_MIN, int(math.ceil(amount * tier(level)["fee"])))


def max_sendable(u: User) -> int:
    """بیشترین مبلغی که (با کارمزدش) از بانک کم می‌شه. سقف انتقال نداریم؛ فقط موجودی."""
    t = tier(u.bank_level)
    amt = int(u.bank / (1 + t["fee"]))
    while amt > 0 and amt + fee_for(u.bank_level, amt) > u.bank:
        amt -= 1
    return max(0, amt)


def _today() -> str:
    return utcnow().strftime("%Y-%m-%d")


def used_today(u: User) -> int:
    return u.xfer_total if u.xfer_day == _today() else 0


def precheck(u: User, amount: int) -> Optional[str]:
    """بررسی بدون جابجایی پول (برای پیش‌نمایش). None = مشکلی نیست."""
    if u.card_frozen:
        return "🧊 کارتت مسدوده. اول «رفع مسدودی» رو بزن."
    if amount < C.CARD_MIN:
        return f"💸 حداقل مبلغ کارت‌به‌کارت {fa(C.CARD_MIN)} دلاره."
    total = amount + fee_for(u.bank_level, amount)
    if u.bank < total:
        return f"💸 موجودی بانکت کافی نیست: لازم {fa(total)} (با کارمزد) ، داری {fa(u.bank)}."
    return None


# ───────────────────────── دفتر تراکنش ─────────────────────────
async def log_tx(uid: int, kind: str, amount: int, bal: int = -1, note: str = "", peer: Optional[int] = None,
                 ref: str = "") -> None:
    try:
        await Ledger.get_motor_collection().insert_one(
            {"uid": uid, "kind": kind, "amount": int(amount), "bal": int(bal), "note": note[:120], "peer": peer,
             "ref": ref, "at": utcnow()})
    except Exception:  # noqa: BLE001
        log.exception("ledger_error", uid=uid, kind=kind)


KIND = {"dep": ("📥", "واریز به بانک"), "wd": ("📤", "برداشت از بانک"), "interest": ("📈", "سود سپرده"),
        "xfer_out": ("🔁", "کارت‌به‌کارت"), "xfer_in": ("🔁", "دریافت کارت‌به‌کارت"), "fee": ("🧾", "کارمزد"),
        "loan": ("💳", "دریافت وام"), "inst": ("💳", "پرداخت قسط وام"), "payoff": ("🎉", "تسویه‌ی وام"),
        "seize": ("🚨", "ضبط دارایی"), "upgrade": ("⭐", "ارتقای سطح حساب")}
PAGE = 8


async def history(uid: int, page: int = 0) -> tuple[list[dict], int]:
    col = Ledger.get_motor_collection()
    total = await col.count_documents({"uid": uid})
    rows = await col.find({"uid": uid}).sort("at", -1).skip(page * PAGE).limit(PAGE).to_list(PAGE)
    return rows, total


def _stamp(dt) -> str:
    return (dt + TEHRAN).strftime("%m/%d %H:%M")


def tx_line(r: dict) -> str:
    ic, name = KIND.get(r["kind"], ("•", r["kind"]))
    amt = int(r.get("amount", 0))
    sign = "➕" if amt > 0 else "➖" if amt < 0 else "▫️"
    note = f" · {esc(r['note'])}" if r.get("note") else ""
    ref = f" · کد <code>{esc(r['ref'])}</code>" if r.get("ref") else ""
    bal = f"\n      مانده: {fa(r['bal'])}" if r.get("bal", -1) >= 0 else ""
    return f"{ic} <b>{name}</b>{note}\n      {sign} {fa(abs(amt))}  🕒 {_stamp(r['at'])}{ref}{bal}"


# ───────────────────────── کارت‌به‌کارت ─────────────────────────
async def transfer(src: int, dst: int, amount: int, note: str = "") -> tuple[bool, str, Optional[dict]]:
    """کارت‌به‌کارتِ اتمیک از بانکِ src به بانکِ dst. خروجی: (موفق؟, پیام خطا, رسید)."""
    import bank
    amount = int(amount)
    if src == dst:
        return False, "😅 به کارت خودت که نمی‌تونی بفرستی!", None
    if amount < C.CARD_MIN:
        return False, f"💸 حداقل مبلغ کارت‌به‌کارت {fa(C.CARD_MIN)} دلاره.", None
    await bank.ensure_fields(src)
    await bank.accrue(src)
    u = await eco.get_user(src)
    v = await eco.get_user(dst)
    if u is None:
        return False, "اول /start بزن.", None
    if v is None:
        return False, "😕 صاحب این کارت/یوزر توی بازی ثبت‌نام نکرده.", None
    if v.banned:
        return False, "🚫 حساب مقصد مسدود شده و پول نمی‌پذیره.", None
    err = precheck(u, amount)
    if err:
        return False, err, None
    fee = fee_for(u.bank_level, amount)
    total = amount + fee
    col = eco.col()

    # ۱) برداشت از مبدأ (اتمیک؛ موجودی کافی و کارت باز)
    r = await col.update_one({"tg_id": src, "bank": {"$gte": total}, "card_frozen": {"$ne": True}},
                             {"$inc": {"bank": -total}})
    if r.matched_count != 1:
        return False, "⏳ موجودی بانکت عوض شد یا کارتت مسدوده؛ دوباره امتحان کن.", None

    # ۲) واریز به مقصد
    try:
        await bank.ensure_fields(dst)
        await bank.accrue(dst)                       # سود قبل از ورود پول جدید حساب بشه
        v2 = await eco.get_user(dst)
        upd: dict[str, Any] = {"$inc": {"bank": amount, "bstats.in_n": 1, "bstats.in_sum": amount}}
        if v2 is None or v2.bank <= 0 or v2.bank_at is None:
            upd["$set"] = {"bank_at": utcnow()}
        r2 = await col.update_one({"tg_id": dst}, upd)
        ok = r2.matched_count == 1
    except Exception:  # noqa: BLE001
        log.exception("c2c_credit_error", src=src, dst=dst)
        ok = False
    if not ok:                                       # برگردوندن پول
        await col.update_one({"tg_id": src}, {"$inc": {"bank": total}})
        return False, "⚠️ انتقال انجام نشد (مشکل موقت)؛ پولی کم نشد. دوباره امتحان کن.", None

    # ۳) رسید + دفتر
    try:
        ref = f"DZ{10_000_000 + await eco.next_seq('c2c')}"
    except Exception:  # noqa: BLE001
        ref = "DZ" + uuid.uuid4().hex[:8].upper()
    after = max(0, u.bank - total)
    sc, dc = await ensure_card(src), await ensure_card(dst)
    await col.update_one({"tg_id": src}, {"$inc": {"bstats.out_n": 1, "bstats.out_sum": amount}})
    await log_tx(src, "xfer_out", -amount, after + fee, f"به {v.name}", dst, ref)
    await log_tx(src, "fee", -fee, after, f"کارمزد {ref}", None, ref)
    await log_tx(dst, "xfer_in", amount, -1, f"از {u.name}" + (f" — {note}" if note else ""), src, ref)
    rc = {"ref": ref, "src": src, "dst": dst, "amount": amount, "fee": fee, "at": utcnow(), "note": note,
          "src_name": u.name, "dst_name": v.name, "src_card": sc, "dst_card": dc, "src_after": after}
    _emit(f"🔁 کارت‌به‌کارت: {esc(u.name)} <code>{src}</code> ← {esc(v.name)} <code>{dst}</code> — {fa(amount)} (کارمزد {fa(fee)}) کد {ref}")
    return True, "", rc


def receipt_text(rc: dict, side: str = "src") -> str:
    head = "🧾 <b>رسید کارت‌به‌کارت</b> ✅"
    t = (f"{head}\n\n📌 کد پیگیری: <code>{rc['ref']}</code>\n🕒 {_stamp(rc['at'])}\n\n"
         f"از: <b>{esc(rc['src_name'])}</b>  <code>{mask_card(rc['src_card'])}</code>\n"
         f"به: <b>{esc(rc['dst_name'])}</b>  <code>{mask_card(rc['dst_card'])}</code>\n\n"
         f"💰 مبلغ: <b>{fa(rc['amount'])}</b> دلار")
    if side == "src":
        t += f"\n🧾 کارمزد: {fa(rc['fee'])}\n🏦 مانده‌ی بانک: <b>{fa(rc['src_after'])}</b>"
    if rc.get("note"):
        t += f"\n📝 {esc(rc['note'])}"
    return t


# ───────────────────────── مسدودسازی کارت ─────────────────────────
async def set_frozen(uid: int, frozen: bool) -> bool:
    r = await eco.col().update_one({"tg_id": uid}, {"$set": {"card_frozen": bool(frozen)}})
    return r.matched_count == 1


# ───────────────────────── ارتقای سطح حساب ─────────────────────────
async def upgrade(uid: int) -> tuple[bool, str]:
    import bank
    await bank.ensure_fields(uid)
    await bank.accrue(uid)
    u = await eco.get_user(uid)
    if u is None:
        return False, "اول /start بزن."
    lvl = max(1, u.bank_level)
    if lvl >= len(C.BANK_TIERS):
        return False, "👑 حسابت از قبل بالاترین سطحه."
    nxt = C.BANK_TIERS[lvl]
    price = nxt["price"]
    c = min(u.cash, price)
    bk = price - c
    if bk > u.bank:
        return False, f"💸 پول کافی نداری: هزینه {fa(price)} (جیب {fa(u.cash)} + بانک {fa(u.bank)})."
    r = await eco.col().update_one(
        {"tg_id": uid, "bank_level": lvl, "cash": {"$gte": c}, "bank": {"$gte": bk}},
        {"$inc": {"cash": -c, "bank": -bk}, "$set": {"bank_level": lvl + 1}})
    if r.matched_count != 1:
        return False, "⏳ دوباره امتحان کن."
    await log_tx(uid, "upgrade", -bk, u.bank - bk, f"سطح {nxt['name']}")
    _emit(f"⭐ ارتقای حساب بانکی: {esc(u.name)} <code>{uid}</code> → {nxt['emoji']} {nxt['name']}")
    return True, (f"⭐ حسابت شد <b>{nxt['emoji']} {nxt['name']}</b>!\n"
                  f"کارمزد: {pct(nxt['fee'])}٪ · سقف وام ×{nxt['loan_mult']}")


# ───────────────────────── امتیاز اعتباری ─────────────────────────
def credit(u: User, now=None) -> dict:
    now = now or utcnow()
    st = u.bstats or {}
    g = lambda k: max(0, int(st.get(k, 0) or 0))          # noqa: E731
    age_days = max(0, (now - u.created_at).days)
    parts = [("🕰 قدمت حساب", min(60, age_days * 2)),
             ("✅ قسط‌های به‌موقع", 8 * min(g("ontime"), 20)),
             ("🎯 وام‌های کامل صاف‌شده", 20 * min(g("repaid"), 5)),
             ("⭐ سطح حساب", 10 * (max(1, u.bank_level) - 1)),
             ("💰 موجودی بانک", 40 if u.bank >= 100_000_000 else 20 if u.bank >= 1_000_000 else 0),
             ("⏰ قسط‌های دیرکرد", -25 * min(g("late"), 10)),
             ("🚨 ضبط دارایی", -120 * min(g("seized"), 2))]
    score = max(300, min(850, 500 + sum(p for _, p in parts)))
    label = ("ضعیف 🔴" if score < 450 else "متوسط 🟠" if score < 550 else "خوب 🟡" if score < 650
             else "عالی 🟢" if score < 750 else "ممتاز 💚")
    mult = round(0.7 + 0.8 * (score - 300) / 550, 2)       # ≈۱٫۰ برای امتیاز ۵۰۰ ، ۱٫۵ برای ۸۵۰
    return {"score": score, "label": label, "mult": mult, "parts": parts}


def loan_mult(u: User) -> float:
    """ضریب نهایی سقف شغلی وام = ضریب سطح حساب × ضریب امتیاز اعتباری."""
    return tier(u.bank_level)["loan_mult"] * credit(u)["mult"]


def loan_rate(u: User) -> float:
    return max(0.01, C.LOAN_INTEREST - tier(u.bank_level)["loan_disc"])


def credit_text(u: User) -> str:
    c = credit(u)
    lines = [f"{n}: <b>{'+' if p > 0 else ''}{fa(p)}</b>" for n, p in c["parts"] if p]
    bar = "█" * ((c["score"] - 300) * 10 // 550) + "░" * (10 - (c["score"] - 300) * 10 // 550)
    return (f"📊 <b>امتیاز اعتباری</b>\n\n<b>{fa(c['score'])}</b> از ۸۵۰ — {c['label']}\n<code>{bar}</code>\n\n"
            f"پایه: ۵۰۰\n" + ("\n".join(lines) + "\n" if lines else "") +
            f"\n🔓 ضریب سقف وام شما: <b>×{c['mult']}</b> (با سطح حساب: ×{round(loan_mult(u), 2)})\n"
            f"💳 بهره‌ی وام شما: <b>{pct(loan_rate(u))}٪</b>\n\n"
            "💡 قسط به‌موقع، تسویه‌ی کامل وام و بالا بردن سطح حساب امتیاز رو بالا می‌بره؛ دیرکرد و ضبط دارایی پایین می‌آره.")


# ───────────────────────── متن‌های کارت / سطح ─────────────────────────
def card_text(u: User, card: str) -> str:
    t = tier(u.bank_level)
    return (f"💳 <b>کارت بانکی Day Zero</b>\n\n<code>{fmt_card(card)}</code>\n"
            f"👤 {esc(u.name)}\n"
            f"🏷 سطح حساب: <b>{tier_label(u.bank_level)}</b>\n"
            f"وضعیت: {'🧊 <b>مسدود</b>' if u.card_frozen else '✅ فعال'}\n\n"
            f"🏦 موجودی: <b>{fa(u.bank)}</b> دلار\n"
            "♾ سقف انتقال نداره\n"
            f"🧾 کارمزد: {pct(t['fee'])}٪ (حداقل {fa(C.CARD_FEE_MIN)})\n\n"
            "برای کارت‌به‌کارت شماره‌ی کارتت رو به دوستت بده؛ اون هم می‌تونه با «کارت به کارت» بهت بفرسته.")


def tier_text(u: User) -> str:
    lvl = max(1, u.bank_level)
    lines = []
    for i, t in enumerate(C.BANK_TIERS, 1):
        mark = "👉 " if i == lvl else ""
        price = "رایگان" if not t["price"] else fa(t["price"])
        lines.append(f"{mark}{t['emoji']} <b>{t['name']}</b> — {price}\n"
                     f"   کارمزد {pct(t['fee'])}٪\n"
                     f"   وام ×{t['loan_mult']} · تخفیف بهره {pct(t['loan_disc'])}٪")
    return "⭐ <b>سطح حساب بانکی</b>\n\n" + "\n\n".join(lines)
