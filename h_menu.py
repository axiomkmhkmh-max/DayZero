"""منوی اصلی، شروع، شغل، پاداش روزانه، رتبه‌بندی، راهنما."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config as C
import eco
import econ
import rules
from db import User
from redis_service import redis_service
from ui import b, back, claim, edit, is_private, kb, url_b
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
    house = f"سطح {fa(u.house)}" if u.house else "هنوز نداری"
    lvl = rules.level_of(u.xp)
    nxt = rules.xp_for_level(lvl + 1)
    return (f"{job['emoji']} {job['name']}  ·  ⭐ سطح <b>{fa(lvl)}</b> <i>({fa(u.xp)}/{fa(nxt)} XP)</i>\n\n"
            f"💵 تو جیبت: <b>{fa(u.cash)}</b> دلار\n"
            f"🔐 تو گاوصندوقت: <b>{fa(u.vault)}</b> دلار\n"
            + (f"🏦 تو بانک: <b>{fa(u.bank)}</b> دلار\n" if u.bank else "")
            + (f"💳 وام فعال: <b>{fa(max(0, u.loan_total - u.loan_paid))}</b> دلار\n" if u.loan_total else "")
            + f"🏠 خونه: {house}")


def greeting(u: User, pending: int) -> str:
    """یه جمله‌ی دوستانه بر اساس وضعیت بازیکن (هر بار یکی از چند حالت)."""
    pick = rules.RNG.choice
    if u.cash < 1000 and u.vault < 1000:
        return pick(["جیبت خالیِ خالیه، وقتشه یه شیفت بزنی! 💪",
                     "دست‌به‌جیب شدی؟ یه شیفت کار همه‌چی رو درست می‌کنه 😄"])
    if pending > 0:
        return pick(["کاسبیت برات پول جمع کرده، بیا ببرش! 🤑",
                     "صندوق مغازه پر شده، منتظر توئه 😉"])
    if u.cash > 200_000 and u.vault < u.cash // 2:
        return "پول زیادی تو جیبته؛ یه سر به گاوصندوق بزن، دزدا بیدارن 👀"
    return pick(["خوش اومدی، امروز چی تو سرته؟ 😎",
                 "آماده‌ای؟ شهر منتظرته! 🌆",
                 "یه روز دیگه، یه فرصت دیگه ✨",
                 "هر کاری دوست داری بکن، فقط حواست به جیبت باشه 😏"])


RUMORS = [
    "می‌گن گاوصندوق بهترین دوست آدمه؛ دزدا ازش متنفرن 🔐",
    "یه نفر دیشب تو قمارخونه همه‌چیزش رو باخت و با لبخند رفت خونه 🎰",
    "می‌گن هر بچه‌ی سیر یعنی یه کاسبی پرسودتر 👶",
    "یه جن تو شهر پرسه می‌زنه؛ جنگیر داری؟ 👹",
    "شنیدم یه پولدار تازه‌به‌دوران‌رسیده کلی دشمن پیدا کرده 😏",
    "دزدا امشب بیدارن، پولت رو تو جیبت نذار 🥷",
    "می‌گن هر که کار کنه، بالاخره یه روز رئیس می‌شه 💼",
    "ازدواج درآمد رو بالا می‌بره، البته اگه طلاق نگیرید 💍",
    "تو شهر پچ‌پچه که یکی دنبال دعوا می‌گرده... مواظب باش ⚔️",
]


def time_greeting(name: str) -> str:
    from datetime import timedelta
    h = (utcnow() + timedelta(hours=3, minutes=30)).hour      # وقت تهران (بدون ساعت تابستانی)
    if 5 <= h < 12:
        return f"☀️ صبح بخیر، {name}!"
    if 12 <= h < 16:
        return f"🌤 ظهر بخیر، {name}!"
    if 16 <= h < 20:
        return f"🌇 عصر بخیر، {name}!"
    return f"🌙 شب بخیر، {name}! بیداری؟"


def daily_ready(u: User) -> bool:
    return not u.daily_at or (utcnow() - u.daily_at).total_seconds() >= 20 * 3600


async def render_menu(u: User) -> tuple[str, object]:
    pending = await eco.calc_pending(u)
    ready = daily_ready(u)
    text = (f"🌅 <b>Day Zero</b>\n"
            f"{time_greeting(esc(u.name))}\n"
            f"<i>{greeting(u, pending)}</i>\n\n" + status_block(u))
    if pending > 0:
        text += f"\n\n🏪 کاسبیت <b>{fa(pending)}</b> دلار برات جمع کرده، برو برش‌دار!"
    if ready:
        text += "\n🎁 جایزه‌ی روزانه‌ات آماده‌ست!" + (f" (🔥 روز {fa(u.streak)} پشت‌سرهم)" if u.streak else "")
    ev = econ.current_event()
    if ev:
        text += f"\n📰 اتفاق شهر: {ev['emoji']} <b>{ev['title']}</b>"
    text += f"\n\n💭 <i>{rules.RNG.choice(RUMORS)}</i>"
    markup = kb(
        [b("💼 کار", "job"), b("🏪 کاسبی", "biz")],
        [b("🏠 خونه", "home"), b("🛒 بازار", "mk")],
        [b("🎰 بازی‌ها", "games"), b("⚔️ مبارزه", "fight")],
        [b("🏦 بانک", "bank"), b("🤝 اتحاد", "al")],
        [b("🏆 رتبه‌بندی", "rank"), b("📈 بورس", "bo")],
        [b("🎁 جایزه‌ی روزانه ✨" if ready else "🎁 جایزه‌ی روزانه", "daily"),
         b("🌆 چرخ تو شهر ✨" if not left(u.walk_ready_at) else "🌆 چرخ تو شهر", "walk")],
        [b("📖 راهنما", "help:0")],
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
و هر وقت دلت خواست با اسلحه و سپرت بیفتی به جون بقیه. 😈"""


async def open_menu(m: Message, actor: User, is_new: bool = False) -> None:
    """منو رو باز می‌کنه — پیوی یا گروه (توی گروه به فرستنده سپرده می‌شه)."""
    text, markup = await render_menu(actor)
    if is_new:
        text = WELCOME.format(cash=fa(C.START_CASH), hours=C.NEWBIE_SHIELD_HOURS) + "\n\n" + text
    if m.chat.type == "private":
        await m.answer(text, reply_markup=markup)
        return
    sent = await m.reply(text, reply_markup=markup)
    await claim(sent, actor.tg_id)


@router.message(CommandStart())
@router.message(Command("menu", "dashboard"))
async def cmd_start(m: Message, user: User = None, is_new: bool = False, state: FSMContext = None) -> None:
    if m.chat.type != "private":                       # گروه: میان‌افزار کاری نکرده؛ خودمون قفل می‌گیریم
        if m.from_user is None or m.from_user.is_bot:
            return
        if not await redis_service.r.set(f"gthr:{m.from_user.id}", "1", nx=True, px=900):
            return
        async with redis_service.lock(f"u:{m.from_user.id}", ttl=20, wait=6):
            actor, new = await eco.ensure_user(m.from_user)
            await open_menu(m, actor, new)
        return
    if state:
        await state.clear()
    await open_menu(m, user, is_new)


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
    lv0, lv1 = await eco.add_xp(user.tg_id, C.XP["shift"])
    u = await eco.get_user(user.tg_id)
    idx1, job1 = job_of(u.shifts)
    note = f"✅ شیفت تموم شد: <b>+{fa(pay)}</b> دلار" + (" 🎉 (انعام گرفتی!)" if tip else "")
    if lv1 > lv0:
        note += f"\n⭐ <b>سطحت رفت بالا! حالا سطح {fa(lv1)} هستی.</b>"
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
    await eco.add_xp(user.tg_id, C.XP["daily"])
    await show_menu(cb, user.tg_id, f"🎁 جایزه‌ی روزانه: <b>+{fa(amount)}</b> دلار (روز {fa(streak)} پشت‌سرهم)")


# ───────────────────────── رتبه‌بندی ─────────────────────────
@router.callback_query(F.data == "rank")
async def cb_rank(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    col = eco.col()
    pipe = [{"$addFields": {"w": {"$add": ["$cash", "$vault", "$invested", {"$ifNull": ["$bank", 0]}]}}}, {"$sort": {"w": -1}}, {"$limit": 10}]
    top = await col.aggregate(pipe).to_list(10)
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
    lines = [f"{medals[i]} {esc(t['name'])} — <b>{fa(t['w'])}</b>" for i, t in enumerate(top)]
    mine = eco.net_worth(user)
    above = await col.aggregate([{"$addFields": {"w": {"$add": ["$cash", "$vault", "$invested", {"$ifNull": ["$bank", 0]}]}}},
                                 {"$match": {"w": {"$gt": mine}}}, {"$count": "n"}]).to_list(1)
    place = (above[0]["n"] if above else 0) + 1
    text = ("🏆 <b>ثروتمندترین‌ها</b>\n<i>(ثروت = جیب + گاوصندوق + بانک + ارزش دارایی‌ها)</i>\n\n" + "\n".join(lines) +
            f"\n\n📍 جایگاه تو: <b>{fa(place)}</b> با {fa(mine)} دلار")
    await edit(cb, text, kb(back("menu", "◀️ منو")))


# ───────────────────────── راهنما ─────────────────────────
HELP_PAGES = [
    ("📖 کلیات", """<b>Day Zero</b> یه زندگی آزاده: پول دربیار، پولدارتر شو، یا از بقیه بزن! 😎

💼 <b>کار</b>: هر ۱۵ دقیقه یه شیفت (دستمزد بالاتر)؛ با شیفت‌های بیشتر ارتقا می‌گیری.
🏪 <b>کاسبی</b>: کسب‌وکار بخر، کارگرهای مخصوصش (مثلاً کارگر معمولی، اوستاکار، سرکارگر؛ جمعاً تا ۱۰ نفر) استخدام کن؛ درآمد هر ۶ ساعت جمع می‌شه و سقف نداره؛ هر وقت خواستی برداشت کن.
📈 <b>بورس</b>: سهام کسب‌وکارها رو بخر و بفروش؛ قیمتشون با خرید و فروش بازیکن‌ها و اتفاق‌های شهر (رونق، رکود، تورم، ...) بالا و پایین می‌شه و سهام‌دارها سود سهام می‌گیرن. اتفاق‌های شهر روی درآمد کاسبی همه هم اثر می‌ذارن.
🏠 <b>خونه</b>: گاوصندوق، کمد، نیرو (نگهبان/هکر/بادیگارد)، خانواده و ارتقای خونه.
🛒 <b>بازار</b>: فروشگاه (اسلحه، سپر، ابزار، امنیت خونه) و دارک وب (جن، طلسم، دزد).
🤝 <b>اتحاد</b>: با دوستات تیم بساز؛ بانک مشترک و بونوس درآمد، و به هم حمله نمی‌کنید.
🌆 <b>چرخ تو شهر</b>: هر چند دقیقه یه سر برو شهر؛ جایی که می‌ری و تصمیمی که می‌گیری اعتبار، دوستی‌ها و داستانت رو می‌سازه. 🔥 هر روز بیای استریک می‌گیری، ⚡ پشت‌سرهم بزنی کمبو، 🌟 تو ده دقیقه‌ی اول هر ساعت پاداش +۵۰٪ داره، و گاهی ✨ اتفاق نادر پیدا می‌شه!

💡 پولی که تو <b>جیب</b>ه قابل غارته؛ پولِ <b>گاوصندوق</b> تقریباً امنه!"""),
    ("⚔️ مبارزه", """⚔️ <b>مبارزه</b>

🎯 <b>حمله با ریپلای (توی گروه)</b>: روی پیام طرف ریپلای کن و بنویس:
<code>حمله</code> ← بهترین اسلحه‌هات
<code>حمله کلاشینکف</code> ← یه اسلحه‌ی مشخص
<code>حمله کلاشینکف و شمشیر</code> ← حمله‌ی ترکیبی (+۱۵٪ قدرت)
توی پیوی هم می‌تونی: <code>حمله @یوزر کلاشینکف</code>

🛡 طرف مقابل لازم نیست کاری کنه: سپر + تجهیزات امنیتی + نگهبان و بادیگاردش خودکار دفاع می‌کنن و ربات خبر می‌ده «حمله دفع شد» یا «غارت شد».
💰 اگه ببری ۱۲ تا ۳۲٪ جیب طرف مال تو (و ۲٪ گاوصندوقش)! اگه دفع بشه، تا ۶٪ جیبت رو به مدافع می‌دی.
⚖️ قدرت برابر = شانس ۵۰/۵۰ ؛ قوی‌تر معمولاً می‌بره (نبرد، هک و دزدی همگی).

💻 <b>هک</b>: <code>هکش کن</code> ← هکر لازمه؛ هکرها هم هک می‌کنن هم جلوی هک شدن رو می‌گیرن. هک موفق ۵ تا ۵۰٪ پول طرف (جیب و گاوصندوق).
🥷 <b>دزدی از خانه</b>: ابزار دزدی ‹در برابر› امنیت خونه + نگهبان‌ها؛ دزد برنده بشه یه وسیله از کمد طرف مال خودش می‌شه.

⭐ <b>سطح و ضدزورگیری</b>: با هر کار (شیفت، چرخ تو شهر، حمله، هک، بچه، خرید کاسبی...) XP می‌گیری و سطحت بالا می‌ره. هر کس ۵ سطح (یا بیشتر) پایین‌تر از مهاجم باشه «ضعیف» حساب می‌شه؛ ضعیف‌ها می‌تونن از «بازار ← 🛡 ضدزورگیری» بخرن (فقط یکی) تا قوی‌ترها نتونن بهشون حمله، هک، دزدی یا جن و طلسم بفرستن.

⚔️ قدرت حمله = اسلحه(ها) + ابزار دزدی + هکرها + ۷۵٪ بادیگاردها
🛡 قدرت دفاع = (دو سپر قوی‌ترت با بونوس ترکیبی +۱۵٪ + امنیت خونه + نگهبان‌ها + ۷۵٪ بادیگاردها) × ۱٫۱"""),
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
👶 هر <b>۲۴ ساعت</b> می‌شه یه بچه ساخت. 🍌 موز و 🧪 معجون زمانش رو کم می‌کنن.
🍽 بچه‌ها باید <b>غذا بخورن</b> تا کار کنن: هر بچه‌ی سیر = +۲۵٪ درآمد کاسبی. با 👨‍🍳 آشپز، همیشه سیرن.
💍 ازدواج: +۱۰٪ درآمد برای هر دو نفر. توی گروه روی پیام طرف ریپلای کن: <code>ثبت ازدواج</code> · <code>درخواست طلاق</code> · <code>بزن توش</code>
💘💔 طلسم عاشقی و طلاق از دارک وب: <code>طلسم عاشقی</code> / <code>طلسم طلاق</code>
👹 جن: <code>حمله اجنه</code> · 🥷 دزد گاوصندوق: <code>دزد</code> (ریپلای روی طرف)
🥷 دزدی از خانه: <code>دزدی از خانه</code> (ابزار دزدی تو ‹در برابر› امنیت خونه + نگهبان‌های طرف؛ برنده بشی یه وسیله از کمدش مال تو؛ روی هر نفر هر ۴ ساعت یک بار، روی بقیه آزاد)
💻 هک: <code>هکش کن</code> (هکرهای تو ‹در برابر› هکرهای طرف؛ برنده بشی ۵ تا ۵۰٪ پولش مال تو؛ روی هر نفر هر ۴ ساعت یک بار، روی بقیه آزاد)
🛒 باز کردن سریع: <code>بازار</code> · <code>خونه</code> · <code>کاسبی</code>
💸 پول تو جیبی: <code>جیبی</code> (ساعتی یک بار، ۱٬۰۰۰ تا ۱۰٬۰۰۰ دلار)
💸 انتقال پول: ریپلای روی طرف + <code>انتقال 1000</code>
🎁 کد هدیه: <code>کد هدیه کدت</code> (فقط تو پیوی ربات)"""),
]


@router.callback_query(F.data.startswith("help:"))
async def cb_help(cb: CallbackQuery) -> None:
    await cb.answer()
    page = int(cb.data.split(":")[1])
    text = HELP_PAGES[page][1]
    tabs = [b(("• " if i == page else "") + name, f"help:{i}") for i, (name, _) in enumerate(HELP_PAGES)]
    await edit(cb, text, kb(tabs[:2], tabs[2:], back("menu", "◀️ منو")))
