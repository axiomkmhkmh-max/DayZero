"""منوی اصلی، شروع، شغل، پاداش روزانه، رتبه‌بندی، راهنما."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config as C
import eco
import rules
from db import User
from ui import b, back, edit, is_private, kb, url_b
from util import dur, esc, fa, left, utcnow

router = Router()


# ───────────────────────── کمک‌ها ─────────────────────────
def job_of(shifts: int) -> tuple[int, dict]:
    idx = 0
    for i, j in enumerate(C.JOBS):
        if shifts >= j["need"]:
            idx = i
    return idx, C.JOBS[idx]


def status_block(u: User) -> str:
    _, job = job_of(u.shifts)
    house = f"سطح {fa(u.house)}" if u.house else "ندارد"
    return (f"👤 <b>{esc(u.name)}</b>  {job['emoji']} {job['name']}\n"
            f"💵 جیب: <b>{fa(u.cash)}</b>   🔐 گاوصندوق: <b>{fa(u.vault)}</b>\n"
            f"🏠 خونه: {house}")


async def render_menu(u: User) -> tuple[str, object]:
    pending = await eco.calc_pending(u)
    text = "🌅 <b>Day Zero</b>\n\n" + status_block(u)
    if pending > 0:
        text += f"\n\n🏪 درآمد کاسبی آماده‌ی برداشت: <b>{fa(pending)}</b>"
    markup = kb(
        [b("💼 کار", "job"), b("🏪 کاسبی", "biz")],
        [b("🏠 خونه", "home"), b("🛒 بازار", "mk")],
        [b("🎰 بازی‌ها", "games"), b("⚔️ مبارزه", "fight")],
        [b("🤝 اتحاد", "al"), b("🏆 رتبه‌بندی", "rank")],
        [b("🎁 جایزه‌ی روزانه", "daily"), b("📖 راهنما", "help:0")],
    )
    return text, markup


async def show_menu(cb: CallbackQuery, uid: int, note: str = "") -> None:
    u = await eco.get_user(uid)
    text, markup = await render_menu(u)
    await edit(cb, (note + "\n\n" if note else "") + text, markup)


# ───────────────────────── شروع و منو ─────────────────────────
WELCOME = """🌅 <b>Day Zero — روز صفر</b>

یه شهر، یه جیب تقریباً خالی و یه زندگی آزاد.
با <b>{cash}</b> دلار شروع کردی. کار کن، کاسبی راه بنداز، خونه بخر و خانواده بساز، قمار کن،
و هر وقت دلت خواست با اسلحه و سپرت بیفتی به جون بقیه. 😈

🛡 تا <b>{hours} ساعت</b> تازه‌واردی و کسی بهت حمله نمی‌کنه (اگه خودت حمله کنی این محافظت از بین می‌ره)."""


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(m: Message, user: User = None, is_new: bool = False, state: FSMContext = None) -> None:
    if m.chat.type != "private":
        await m.reply("🌅 برای منوی بازی بیا پیوی:", reply_markup=kb([url_b("🎮 ورود به Day Zero",
                                                                          f"https://t.me/{C.BOT_USERNAME}?start=go")]))
        return
    if state:
        await state.clear()
    text, markup = await render_menu(user)
    if is_new:
        text = WELCOME.format(cash=fa(C.START_CASH), hours=C.NEWBIE_SHIELD_HOURS) + "\n\n" + text
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await show_menu(cb, user.tg_id)


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery) -> None:
    await cb.answer()


# ───────────────────────── شغل ─────────────────────────
async def render_job(u: User) -> tuple[str, object]:
    idx, job = job_of(u.shifts)
    nxt = C.JOBS[idx + 1] if idx + 1 < len(C.JOBS) else None
    wait = left(u.job_ready_at)
    text = (f"💼 <b>کار</b>\n\n{job['emoji']} شغل فعلی: <b>{job['name']}</b>\n"
            f"💵 دستمزد هر شیفت: <b>{fa(job['pay'])}</b>\n"
            f"⏱ هر {fa(C.JOB_COOLDOWN_MIN)} دقیقه یک شیفت (۱۰٪ شانس انعام دو برابر)\n"
            f"📈 شیفت‌های انجام‌شده: {fa(u.shifts)}\n")
    if nxt:
        text += f"⬆️ {fa(nxt['need'] - u.shifts)} شیفت دیگه تا ارتقا به {nxt['emoji']} <b>{nxt['name']}</b> ({fa(nxt['pay'])} دلار)"
    else:
        text += "👑 به بالاترین شغل رسیدی!"
    if wait > 0:
        text += f"\n\n😮‍💨 خسته‌ای؛ شیفت بعدی: {dur(wait)} دیگه"
        row = [b("🔄 بروزرسانی", "job")]
    else:
        row = [b("⛏ شروع شیفت", "job:work")]
    return text, kb(row, back("menu", "◀️ منو"))


@router.callback_query(F.data == "job")
async def cb_job(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    text, markup = await render_job(user)
    await edit(cb, text, markup)


@router.callback_query(F.data == "job:work")
async def cb_work(cb: CallbackQuery, user: User) -> None:
    now = utcnow()
    idx0, job = job_of(user.shifts)
    pay = job["pay"]
    tip = rules.RNG.random() < C.JOB_TIP_CHANCE
    if tip:
        pay *= 2
    from datetime import timedelta
    ok = await eco.update_user(user.tg_id, cash=pay, inc={"shifts": 1},
                               set_={"job_ready_at": now + timedelta(minutes=C.JOB_COOLDOWN_MIN)},
                               flt={"$or": [{"job_ready_at": None}, {"job_ready_at": {"$lte": now}}]})
    if not ok:
        await cb.answer("😮‍💨 هنوز خسته‌ای!", show_alert=True)
        return
    await cb.answer()
    u = await eco.get_user(user.tg_id)
    idx1, job1 = job_of(u.shifts)
    note = f"✅ شیفت تموم شد: <b>+{fa(pay)}</b> دلار" + (" 🎉 (انعام گرفتی!)" if tip else "")
    if idx1 > idx0:
        note += f"\n🎊 ارتقا گرفتی! حالا {job1['emoji']} <b>{job1['name']}</b> هستی."
    text, markup = await render_job(u)
    await edit(cb, note + "\n\n" + text, markup)


# ───────────────────────── جایزه‌ی روزانه ─────────────────────────
@router.callback_query(F.data == "daily")
async def cb_daily(cb: CallbackQuery, user: User) -> None:
    now = utcnow()
    if user.daily_at and (now - user.daily_at).total_seconds() < 20 * 3600:
        await cb.answer(f"🎁 جایزه‌ی بعدی: {dur(20 * 3600 - (now - user.daily_at).total_seconds())} دیگه", show_alert=True)
        return
    streak = user.streak + 1 if user.daily_at and (now - user.daily_at).total_seconds() < 48 * 3600 else 1
    amount = C.DAILY_BASE + C.DAILY_STEP * (min(streak, C.DAILY_MAX_STREAK) - 1)
    ok = await eco.update_user(user.tg_id, cash=amount, set_={"daily_at": now, "streak": streak},
                               flt={"daily_at": user.daily_at})
    if not ok:
        await cb.answer("⏳", show_alert=False)
        return
    await cb.answer()
    await show_menu(cb, user.tg_id, f"🎁 جایزه‌ی روزانه: <b>+{fa(amount)}</b> دلار (روز {fa(streak)} پشت‌سرهم)")


# ───────────────────────── رتبه‌بندی ─────────────────────────
@router.callback_query(F.data == "rank")
async def cb_rank(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    col = eco.col()
    pipe = [{"$addFields": {"w": {"$add": ["$cash", "$vault", "$invested"]}}}, {"$sort": {"w": -1}}, {"$limit": 10}]
    top = await col.aggregate(pipe).to_list(10)
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
    lines = [f"{medals[i]} {esc(t['name'])} — <b>{fa(t['w'])}</b>" for i, t in enumerate(top)]
    mine = eco.net_worth(user)
    above = await col.aggregate([{"$addFields": {"w": {"$add": ["$cash", "$vault", "$invested"]}}},
                                 {"$match": {"w": {"$gt": mine}}}, {"$count": "n"}]).to_list(1)
    place = (above[0]["n"] if above else 0) + 1
    text = ("🏆 <b>ثروتمندترین‌ها</b>\n<i>(ثروت = جیب + گاوصندوق + ارزش دارایی‌ها)</i>\n\n" + "\n".join(lines) +
            f"\n\n📍 جایگاه تو: <b>{fa(place)}</b> با {fa(mine)} دلار")
    await edit(cb, text, kb(back("menu", "◀️ منو")))


# ───────────────────────── راهنما ─────────────────────────
HELP_PAGES = [
    ("📖 کلیات", """<b>Day Zero</b> یه زندگی آزاده: پول دربیار، پولدارتر شو، یا از بقیه بزن! 😎

💼 <b>کار</b>: هر ۲۰ دقیقه یه شیفت؛ با شیفت‌های بیشتر ارتقا می‌گیری.
🏪 <b>کاسبی</b>: کسب‌وکار بخر، کارگر (تا ۵ نفر) استخدام کن؛ درآمد هر ۶ ساعت جمع می‌شه (تا ۲۴ ساعت ذخیره).
🏠 <b>خونه</b>: گاوصندوق، کمد، نیرو (نگهبان/هکر/بادیگارد)، خانواده و ارتقای خونه.
🛒 <b>بازار</b>: فروشگاه (اسلحه، سپر، ابزار، امنیت خونه) و دارک وب (جن، طلسم، دزد).
🤝 <b>اتحاد</b>: با دوستات تیم بساز؛ بانک مشترک و بونوس درآمد، و به هم حمله نمی‌کنید.

💡 پولی که تو <b>جیب</b>ه قابل غارته؛ پولِ <b>گاوصندوق</b> تقریباً امنه!"""),
    ("⚔️ مبارزه", """⚔️ <b>مبارزه</b>

🎯 <b>حمله با ریپلای (توی گروه)</b>: روی پیام طرف ریپلای کن و بنویس:
<code>حمله</code> ← بهترین اسلحه‌هات
<code>حمله کلاشینکف</code> ← یه اسلحه‌ی مشخص
<code>حمله کلاشینکف و شمشیر</code> ← حمله‌ی ترکیبی (+۱۵٪ قدرت)
توی پیوی هم می‌تونی: <code>حمله @یوزر کلاشینکف</code>

🛡 طرف مقابل لازم نیست کاری کنه: سپر + تجهیزات امنیتی + نگهبان و بادیگاردش خودکار دفاع می‌کنن و ربات خبر می‌ده «حمله دفع شد» یا «غارت شد».
💰 اگه ببری ۱۲ تا ۳۲٪ جیب طرف مال تو (و ۲٪ گاوصندوقش)! اگه دفع بشه، جریمه می‌دی.

⚔️ قدرت حمله = اسلحه(ها) + ابزار دزدی + هکرها + نصف بادیگاردها
🛡 قدرت دفاع = سپر + امنیت خونه + نگهبان‌ها + نصف بادیگاردها"""),
    ("🎰 بازی‌ها", """🎰 <b>بازی‌ها</b>

تنهایی با خونه: 🎰 اسلات (انیمیشن واقعی تلگرام) · 🎡 گردونه · 🪙 شیر یا خط · 🎲 تاس

🆚 <b>شرط‌بندی با آدم‌ها</b> (توی گروه روی پیام طرف ریپلای کن):
<code>دوز 50000</code> ← دوز با شرط ۵۰ هزار
<code>تاس 1m</code> ← تاس با شرط یک میلیون
اون قبول کنه، هر دو شرط می‌ذارید و برنده <b>۹۵٪ کل پول</b> رو می‌بره.
(پیوی: <code>دوز @یوزر 50000</code>)

مبلغ رو می‌تونی <code>5k</code>، <code>2m</code> یا <code>همه</code> بنویسی."""),
    ("👨‍👩‍👧 خانواده", """👨‍👩‍👧 <b>خانواده</b>

🏠 اول باید خونه بخری (سطح ۱) تا بتونی بچه‌دار بشی؛ سطح بالاتر = ظرفیت بچه‌ی بیشتر.
👶 هر <b>۴۸ ساعت</b> می‌شه یه بچه ساخت. 🍌 موز و 🧪 معجون زمانش رو کم می‌کنن.
🍽 بچه‌ها باید <b>غذا بخورن</b> تا کار کنن: هر بچه‌ی سیر = +۲۵٪ درآمد کاسبی. با 👨‍🍳 آشپز، همیشه سیرن.
💍 ازدواج: +۱۰٪ درآمد برای هر دو نفر. توی گروه روی پیام طرف ریپلای کن: <code>ثبت ازدواج</code> · <code>درخواست طلاق</code> · <code>بچه‌دار شدن</code>
💘💔 طلسم عاشقی و طلاق از دارک وب: <code>طلسم عاشقی</code> / <code>طلسم طلاق</code>
👹 جن: <code>جن</code> · 🥷 دزد گاوصندوق: <code>دزد</code> (ریپلای روی طرف)"""),
]


@router.callback_query(F.data.startswith("help:"))
async def cb_help(cb: CallbackQuery) -> None:
    await cb.answer()
    page = int(cb.data.split(":")[1])
    text = HELP_PAGES[page][1]
    tabs = [b(("• " if i == page else "") + name, f"help:{i}") for i, (name, _) in enumerate(HELP_PAGES)]
    await edit(cb, text, kb(tabs[:2], tabs[2:], back("menu", "◀️ منو")))
