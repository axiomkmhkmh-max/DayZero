"""🌆 چرخ تو شهر — زندگی روزمره‌ی شخصیت آزاد.
بازیکن یه آدم آزاده که بین کارهاش یه سر می‌ره شهر: از بین ۳ جایی که بسته به ساعت (وقت تهران) پیشنهاد می‌شه یکی رو انتخاب می‌کنه،
اتفاق می‌افته و دو راه جلوشه. عمق: داستان‌های چندمرحله‌ای، اعتبار (کارما) که شانس رو عوض می‌کنه، رفاقت با آدم‌های شهر
(بونوس پاداش)، و دفترچه‌ی شهر با جایزه‌ی کشف. جایزه/ضرر به شغل فعلی بازیکن وابسته‌ست."""
from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

import config as C
import eco
import rules
from db import User
from h_menu import job_of
from redis_service import redis_service
from ui import b, back, edit, kb
from util import dur, esc, fa, left, utcnow

router = Router()

# هر نتیجه: (وزن، ضریب پول نسبت به دستمزد شیفت، متن، ویژه). ضریب منفی = ضرر. ویژه: "shift" = شیفت‌ات آماده می‌شه
# هر اتفاق: (نشان، عنوان، متن، [(برچسب دکمه، [نتیجه‌ها]), …])
MORNING, NOON, EVENING, NIGHT, ANY = "morning", "noon", "evening", "night", "any"

EVENTS: dict[str, list] = {
    MORNING: [
        ("☕", "کافه‌ی سر کوچه",
         "بوی قهوه‌ی تازه تو کوچه پیچیده. یه میز کنار پنجره خالیه و مرد پیری کنارش داره روزنامه می‌خونه.",
         [("☕ یه قهوه می‌گیرم و گپ می‌زنم", [
             (50, -0.3, "قهوه‌ات خوشمزه بود و حساب کافه رو دادی. روزت با یه لبخند شروع شد.", None),
             (30, 1.5, "پیرمرد بهت یه نکته‌ی کاسبی گفت که همون‌جا به دردت خورد! 🧠", "next:chain:3"),
             (20, 4, "معلوم شد پیرمرد صاحب کافه‌ست؛ قهوه‌ات مهمون و یه انعام هم گذاشت کف دستت! 🎉", None)]),
          ("🚶 دیرم شده، رد می‌شم", [
             (80, 0.4, "تند رفتی و سر وقت رسیدی؛ تو راه یه اسکناس از جیب یه رهگذر افتاد و بهش پس دادی، اون هم خوشحال شد و یه چیزی بهت داد 😅", None),
             (20, 0, "رسیدی ولی چیز خاصی نشد. هر روز که هیجان نداره.", None)])]),
        ("🥖", "صف نونوایی",
         "سر صبح صف نونوایی شلوغه. جلوی تو یه خانم مسن با کلی خرید داره سختی می‌کشه.",
         [("🤝 کمکش می‌کنم", [
             (70, 0.8, "خانم دعات کرد و یه انعام کوچیک گذاشت تو دستت. دل آدم گرم می‌شه 🥹", None),
             (30, 2.5, "خانم صاحب یه مغازه بود! بهت یه معرفی‌نامه و پاداش حسابی داد 🤑", "next:chain:0")]),
          ("😎 نوبت منه، نون می‌گیرم", [
             (60, -0.2, "نون داغ گرفتی ولی یه نفر تو صف تیکه‌ای بارت کرد... خرجت شد 😒", None),
             (40, 0.5, "نون داغ و تازه گرفتی و یه سکه تو پول خرده‌ها پیدا کردی 😋", None)])]),
        ("🏃", "پارک سحر",
         "هوا خنکه و پارک خلوته. یه کیف رها شده کنار نیمکت افتاده.",
         [("👀 بازش می‌کنم", [
             (45, 2, "داخلش یه پاکت پول بود! صاحبش رو ندیدی ولی کی می‌دونه 🤷", None),
             (35, 0, "فقط یه سری کاغذ بی‌ارزش. کیف رو سر جاش گذاشتی.", None),
             (20, -1, "صاحبش از پشت سرت پیداش شد و بهت بدوبیراه گفت؛ مجبور شدی راضیش کنی 😬", None)]),
          ("🏃 ورزش می‌کنم و می‌رم", [
             (100, 0.6, "یه دور دویدی، حالت جا اومد و تو راه یه اسکناس پیدا کردی 💪", None)])]),
    ],
    NOON: [
        ("🍢", "رستوران محله",
         "ظهر شده و شکمت قار و قور می‌کنه. رستوران سر چهارراه یه منوی ویژه داره.",
         [("🍽 ناهار ویژه‌ی گرون", [
             (55, -1.2, "یه ناهار حسابی خوردی؛ شکمت سیر و جیبت سبک 😋", None),
             (30, 1, "صاحب رستوران بابت وفاداری‌ات یه تخفیف و یه دسر مهمونت کرد! 🍮", None),
             (15, 3, "یه مشتری پولدار ازت خوشش اومد و حساب میزت رو داد و یه انعام هم بهت داد 🤩", None)]),
          ("🥪 ساندویچ ارزون", [
             (80, -0.3, "ساندویچ ارزون ولی حسابی بود. تو جیبت صرفه‌جویی کردی 👌", None),
             (20, 0.8, "کنار ساندویچی یه رفیق قدیمی دیدی و قرضش رو پس داد 🤝", None)])]),
        ("🛍", "بازارچه‌ی دست‌دوم",
         "بین کلی چیز دست‌دوم یه ساعت قدیمی چشمت رو گرفته. فروشنده می‌گه «اصله!»",
         [("💰 می‌خرمش", [
             (40, 3, "اصل بود! فردا به یه کلکسیونر دو برابر قیمت فروختیش 🕰️", None),
             (45, -1.5, "تقلبی بود... فروشنده هم غیب شد 😤", None),
             (15, 0, "ساعت کار نمی‌کنه ولی قشنگه. حداقل یه خاطره‌ست.", None)]),
          ("🤨 چونه می‌زنم و می‌رم", [
             (70, 0.5, "فروشنده کم نیاورد و بهت یه چیز کوچیک مجانی داد 😅", None),
             (30, 0, "ولش کردی و رفتی. بعضی وقتا نخریدن بهترین خریده.", None)])]),
        ("🚕", "ترافیک ظهر",
         "وسط ترافیک گیر کردی و یه تاکسی می‌گه «سوار شو، بهت تخفیف می‌دم».",
         [("🚕 سوار می‌شم", [
             (60, -0.5, "تو راه خوش گذشت و مقصد رسیدی، فقط کرایه‌ی تاکسی رو حساب کردی.", None),
             (25, 1.5, "راننده یه همکار قدیمی از آب دراومد و یه خبر خوب بهت داد 🚀", None),
             (15, "shift", "راننده تو رو به رئیست رسوند و گفت یه شیفت فوق‌العاده بهت بدن! 💼", "shift")]),
          ("🚶 پیاده می‌رم", [
             (100, 0.3, "پیاده رفتی و تو راه کلی چیز جالب دیدی. یه سکه هم پیدا کردی 🪙", None)])]),
    ],
    EVENING: [
        ("🌇", "غروب کنار رودخونه",
         "غروب قشنگیه. یه نفر با ساز کنار رودخونه می‌زنه و جمعیت دورش جمع شده.",
         [("🎶 می‌شینم و گوش می‌دم", [
             (60, 0.5, "موسیقی دلت رو گرم کرد و یه نفر که از شنیدن لذت برده بود بهت یه پاداش کوچیک داد 🥰", None),
             (40, 2, "نوازنده تو رو دید و گفت «تو خوش‌شانسی»! یه پول بهت داد که شانس بیاره 🍀", None)]),
          ("🤏 جیب‌بر بازی در می‌آرم", [
             (35, 3, "کار تموم شد و کسی نفهمید... ولی وجدانت یه‌کم درد گرفت 😶", None),
             (65, -2, "گرفتنت! مجبور شدی جریمه بدی و پا به فرار بذاری 🏃‍♂️", None)])]),
        ("🏀", "زمین بسکتبال محله",
         "بچه‌های محله دارن بسکتبال بازی می‌کنن و یه نفر کم دارن.",
         [("🏀 می‌پرم وسط", [
             (50, 1, "تیمتون برد و شرط‌شون رو بین‌تون تقسیم کردید! 🏆", None),
             (30, -0.5, "باختید و تو هم شام رو حساب کردی 😅", None),
             (20, 0.3, "بازی خوبی شد و فقط عرق ریختی. حال دلت خوب شد 💪", None)]),
          ("👀 تماشا می‌کنم", [
             (100, 0.4, "تماشاچی‌ها بهت یه نوشیدنی دادن و یه شرط کوچیک بردی 😎", None)])]),
        ("🏪", "سوپرمارکت",
         "موقع حساب‌کتاب، متوجه می‌شی صندوقدار دو برابر پول خرده بهت پس داده.",
         [("😇 صداش می‌زنم و پس می‌دم", [
             (70, 0.7, "صندوقدار قدردانی کرد و بهت یه چیز مجانی داد. سربلند رفتی 😌", None),
             (30, 2, "صاحب مغازه ایستاد و بهت پاداش داد! صداقت به نتیجه می‌رسه 🌟", None)]),
          ("😏 چیزی نمی‌گم", [
             (60, 1.2, "با خیال راحت پول رو برداشتی و رفتی... ولی دلت یه‌کم شور می‌زنه.", None),
             (40, -1.5, "دوربین همه‌چی رو دید! مجبور شدی جریمه بدی و بی‌آبرو شی 😳", None)])]),
    ],
    NIGHT: [
        ("🌃", "کوچه‌ی تاریک",
         "نیمه‌شب تو یه کوچه‌ی خلوتی و صدای قدم‌هایی از پشت سرت می‌آد.",
         [("🏃 فرار می‌کنم", [
             (65, 0, "دویدی و رسیدی به خیابون اصلی؛ فقط یه ترس کوچیک مونده بود 😮‍💨", None),
             (35, -1, "تو فرار یه چیزی از جیب‌ت افتاد... ولی سالم رسیدی 😬", None)]),
          ("😠 برمی‌گردم و رو در رو می‌شم", [
             (45, 2.5, "یه ولگرد بود! وقتی ایستادی، ترسید و پول خرد خودش رو داد دستت 😂", None),
             (55, -2, "دو نفر بودن... زور اومدن؛ مجبور شدی بخشی از پولت رو بدی 😤", None)])]),
        ("🎲", "پاتوق شبانه",
         "یه گروه دور یه میز آتیش جمع شدن و یکی می‌گه «بیا یه دست بازی کن».",
         [("🃏 می‌شینم و بازی می‌کنم", [
             (45, 3, "ورق‌ها با تو مهربون بودن! خیلی خوب تموم شد 🔥", "next:chain:1"),
             (55, -2, "شانس نیاوردی و جیبت سبک شد... ولی خوش گذشت 😅", None)]),
          ("🚪 نه، می‌رم", [
             (100, 0.2, "عاقلانه تصمیم گرفتی و با یه لبخند رفتی. یه شبِ آروم هم نعمته 🌙", None)])]),
        ("🌙", "پشت‌بوم",
         "از پشت‌بوم شهر رو نگاه می‌کنی. یه گربه‌ی ولگرد کنارت می‌شینه.",
         [("🐈 بهش غذا می‌دم", [
             (60, 0.3, "گربه خوشحال شد و ازت جدا نشد. یه حس خوب گرفتی 🐾", None),
             (40, 2, "گربه رفت و یه گردنبند طلا از گوشه‌ی پشت‌بوم جلوی پات انداخت! 🤩", "next:chain:2")]),
          ("🌌 فقط ستاره‌ها رو نگاه می‌کنم", [
             (100, 0.5, "آروم شدی و فکرات منظم شد. فردا با انرژی بیشتر شروع می‌کنی ✨", None)])]),
    ],
    ANY: [
        ("📱", "تماس ناشناس",
         "گوشی‌ات زنگ می‌خوره. یه شماره‌ی ناشناس می‌گه «یه کار سریع دارم، سودش خوبه!»",
         [("📞 گوش می‌دم", [
             (40, 3, "یه کار ساده‌ی پردرآمد بود و درست انجام دادی! 💼", None),
             (45, -1, "کلاه‌برداری بود! قطع کردی ولی یه‌کم پولت رو از دست دادی 😒", None),
             (15, "shift", "رئیس قدیمیت بود و برات یه شیفت فوق‌العاده داشت! 💥", "shift")]),
          ("🚫 قطع می‌کنم", [
             (100, 0.2, "عاقلانه بود. هیچ‌وقت به تماس‌های ناشناس اعتماد نکن 🙅", None)])]),
        ("🎁", "بسته‌ی جلوی در",
         "یه بسته‌ی بی‌نام و نشون جلوی در خونه‌ات هست.",
         [("📦 بازش می‌کنم", [
             (50, 2, "داخلش هدیه‌ای از یه دوست قدیمی بود! 🎉", None),
             (30, 0, "داخلش فقط یه نامه‌ی قدیمی و تبریک بود. حس خوبی داره 💌", None),
             (20, -1, "یه شوخی بی‌مزه بود و مجبور شدی خسارتش رو بدی 😠", None)]),
          ("🗑 دست نمی‌زنم", [
             (100, 0.3, "محتاط بودی. بعداً فهمیدی از طرف مغازه‌ی کناری بود و یه سکه برات گذاشته بودن 😄", None)])]),
        ("🧓", "غریبه‌ی مهربان",
         "یه غریبه ازت آدرس می‌پرسه و می‌گه راه رو گم کرده.",
         [("🧭 راه رو نشونش می‌دم", [
             (70, 1, "خوشحال شد و یه تشکر خالی نکرد؛ یه چیزی هم بهت داد 🙏", None),
             (30, 3, "یه توریست پولدار بود و بهت پاداش حسابی داد! 💸", None)]),
          ("🕒 وقت ندارم", [
             (100, 0, "گذشتی و رفتی. هر روز یه آدم تازه!", None)])]),
    ],
    "chain": [
        ("🍵", "چای تو خونه‌ی خانم",
         "خانم نونوایی اصرار می‌کنه: «بیا بالا یه چای بخور، چند تا حرف دارم.» خونه‌اش پر از عکس‌های قدیمیه.",
         [("🍵 می‌رم بالا", [
             (60, 2.5, "ساعت‌ها حرف زدید و از خاطراتش گفت. موقع رفتن یه پاکت بهت داد و گفت «یادگاری» 🥹", None),
             (40, 5, "خانم یه گنج کوچیک از خانواده‌اش رو بهت سپرد؛ می‌گه تو آدم درست‌حسابی‌ای 💎", None)]),
          ("⏰ ممنون، وقت ندارم", [
             (100, 0.3, "ببخشید گفتی و رفتی. خانم لبخند زد و گفت «هر وقت خواستی در باز.» 😌", None)])]),
        ("🕶", "غریبه‌ی تعقیب‌کننده",
         "از پاتوق که بیرون اومدی حس می‌کنی یکی دنبالته؛ یه مرد با عینک دودی چند قدم عقب‌تر می‌آد.",
         [("🗣 صداش می‌زنم", [
             (50, 4, "برنده‌ی دست قبل بودی و اون هم شریک می‌خواست؛ یه پیشنهاد کاری پرسود بهت داد! 💼", None),
             (50, -2, "یه کلاهبردار بود و سرت کلاه گذاشت 😤", None)]),
          ("🏃 فرار می‌کنم", [
             (100, 0.5, "پیچیدی تو یه کوچه و گمش کردی. نفس‌نفس‌زنان یه سکه تو جیبت پیدا کردی 😅", None)])]),
        ("🐈", "ردپای گربه",
         "گربه از پشت‌بوم پایین می‌پره و آروم آروم تو رو دنبال خودش می‌کشه؛ انگار می‌خواد یه جا نشونت بده.",
         [("🐾 دنبالش می‌رم", [
             (50, 5, "تو یه خرابه‌ی قدیمی یه صندوقچه‌ی خاک‌گرفته پیدا کردی! گربه‌ی باهوش! 🪙", None),
             (30, 0, "فقط تا یه ظرف خالی رفت و برگشت... همین! 😹", None),
             (20, -1, "پات رفت تو یه چاله و یه چیزی از جیبت افتاد 😖", None)]),
          ("🏠 برمی‌گردم", [
             (100, 0.4, "گربه یه میو کرد و رفت. شاید یه روز دیگه...", None)])]),
        ("📈", "فرصت پیرمرد",
         "حاجی کافه‌چی آروم می‌گه: «یه کار هست که سودش خوبه، ولی ریسک داره. تو آدم باهوشی، شریک می‌شی؟»",
         [("🤝 قبول می‌کنم", [
             (50, 3.5, "کار گرفت و سهمت رو گرفتی! حاجی لبخند زد: «می‌دونستم.» 🎉", None),
             (50, -1.5, "کار نگرفت و ضرر کردید؛ حاجی عذرخواهی کرد و بخشی رو جبران کرد 😔", None)]),
          ("🙏 ممنون، نه", [
             (100, 0.3, "حاجی سر تکون داد: «احتیاط خوبه.» و یه قهوه مهمونت کرد ☕", None)])]),
    ],
}


# ───────────────────────── عمق‌دهی: اعتبار، آدم‌ها، دفترچه ─────────────────────────
TOTAL_EVENTS = sum(len(v) for v in EVENTS.values())
MILESTONES = [(5, 5), (10, 10), (15, 20), (TOTAL_EVENTS, 40)]       # (تعداد اتفاق کشف‌شده, جایزه = ضریبی از دستمزد شیفت)

# آدم‌های تکراری شهر: اتفاق → (کلید, اسم)
NPCS = {
    "morning:0": ("haj", "حاجی کافه‌چی ☕"), "chain:3": ("haj", "حاجی کافه‌چی ☕"),
    "morning:1": ("khanom", "خانم نونوایی 🥖"), "chain:0": ("khanom", "خانم نونوایی 🥖"),
    "noon:0": ("chef", "صاحب رستوران 🍢"), "noon:2": ("taxi", "راننده تاکسی 🚕"),
    "evening:0": ("music", "نوازنده‌ی رودخونه 🎶"), "evening:2": ("shop", "صندوقدار سوپر 🏪"),
    "night:2": ("cat", "گربه‌ی پشت‌بوم 🐈"), "chain:2": ("cat", "گربه‌ی پشت‌بوم 🐈"),
}
REL_LEVELS = [(5, "یار غار 💛", 0.5), (2, "رفیق 🤝", 0.25), (1, "آشنا 🙂", 0.0)]    # (حداقل دیدار, لقب, بونوس پاداش)

# اثر هر انتخاب روی اعتبار (کارما): "اتفاق:انتخاب" → امتیاز
KARMA = {
    "morning:1:0": 1, "morning:1:1": -1, "evening:0:1": -2, "evening:2:0": 2, "evening:2:1": -2,
    "evening:1:0": 1, "any:2:0": 1, "any:2:1": -1, "night:2:0": 1, "chain:0:0": 1, "chain:1:1": -1,
    "noon:2:1": 0, "any:0:1": 1,
}
KARMA_TITLES = [(10, "دل‌پاک 😇"), (4, "مهربون 🙂"), (-3, "معمولی 😐"), (-9, "بدنام 😈"), (-999, "سایه‌ی شهر 🥷")]


def karma_title(k: int) -> str:
    return next(t for need, t in KARMA_TITLES if k >= need)


def rel_level(n: int) -> tuple[str, float]:
    for need, name, bonus in REL_LEVELS:
        if n >= need:
            return name, bonus
    return "غریبه", 0.0


WALK_TITLES = [(100, "افسانه‌ی شهر 👑"), (50, "پاتوق‌دار 🏅"), (20, "چهره‌ی شناخته‌شده 🌟"),
               (5, "آشنای محل 🙂"), (0, "تازه‌وارد محله 🌱")]


def walk_title(n: int) -> str:
    return next(t for need, t in WALK_TITLES if n >= need)


def slot_now() -> str:
    h = (utcnow() + timedelta(hours=3, minutes=30)).hour          # وقت تهران
    return MORNING if 5 <= h < 12 else NOON if 12 <= h < 17 else EVENING if 17 <= h < 21 else NIGHT


def _pool(slot: str) -> list[str]:
    return [f"{slot}:{i}" for i in range(len(EVENTS[slot]))] + [f"{ANY}:{i}" for i in range(len(EVENTS[ANY]))]


def _ev_key(uid: int) -> str:
    return f"walk:ev:{uid}"


def _event(ref: str) -> tuple:
    slot, i = ref.split(":")
    return EVENTS[slot][int(i)]


def _seen_key(ref: str) -> str:
    return "ev_" + ref.replace(":", "_")


def discovered(u: User) -> int:
    return sum(1 for k in u.stats if k.startswith("ev_"))


def _render_event(ref: str) -> tuple[str, object]:
    emoji, title, body, choices = _event(ref)
    text = f"{emoji} <b>{title}</b>\n\n{body}\n\n<i>چیکار می‌کنی؟</i>"
    rows = [[b(label, f"walk:c:{ref}:{j}")] for j, (label, _) in enumerate(choices)]
    return text, kb(*rows)


def _intro(slot: str) -> str:
    return {MORNING: "☀️ صبح شهر داره بیدار می‌شه؛ بوی نون و قهوه تو هواست.",
            NOON: "🌤 ظهره و شهر پر از شلوغی و بوی غذاست.",
            EVENING: "🌇 غروب شده و چراغ‌ها دارن روشن می‌شن.",
            NIGHT: "🌙 شبه و شهر یه رنگ دیگه داره... هر اتفاقی ممکنه."}[slot]


def _profile(u: User) -> str:
    st = u.stats
    n, k = st.get("walks", 0), st.get("karma", 0)
    lines = [f"🏷 <b>{walk_title(n)}</b> · {fa(n)} چرخ",
             f"⚖️ اعتبار: <b>{karma_title(k)}</b>",
             f"📔 دفترچه‌ی شهر: <b>{fa(discovered(u))}</b> از {fa(TOTAL_EVENTS)} اتفاق"]
    seen: set[str] = set()
    friends = []
    for _, (key, name) in NPCS.items():
        cnt = st.get(f"rel_{key}", 0)
        if key in seen or cnt < 1:
            continue
        seen.add(key)
        friends.append(f"{name}: {rel_level(cnt)[0]}")
    if friends:
        lines.append("👥 " + " · ".join(friends))
    return "\n".join(lines)


async def render_walk(u: User) -> tuple[str, object]:
    ref = await redis_service.r.get(_ev_key(u.tg_id))
    if ref:                                                         # اتفاقی که هنوز جوابش رو ندادی
        return _render_event(ref)
    head = f"🌆 <b>چرخ تو شهر</b>\n{_profile(u)}\n\n"
    wait = left(u.walk_ready_at)
    if wait > 0:
        return (head + f"😮‍💨 تازه از بیرون برگشتی و خسته‌ای.\nدفعه‌ی بعد: <b>{dur(wait)}</b> دیگه",
                kb([b("🔄 بروزرسانی", "walk")], back("menu", "◀️ منو")))
    slot = slot_now()
    picks = rules.RNG.sample(_pool(slot), 3)
    rows = [[b(f"{_event(r)[0]} {_event(r)[1]}", f"walk:p:{r}")] for r in picks]
    return (head + _intro(slot) + "\n\n<b>امروز کجا می‌ری؟</b> هر جا یه داستان داره...",
            kb(*rows, back("menu", "◀️ منو")))


@router.callback_query(F.data == "walk")
async def cb_walk(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    text, markup = await render_walk(user)
    await edit(cb, text, markup)


@router.callback_query(F.data.startswith("walk:p:"))
async def cb_walk_place(cb: CallbackQuery, user: User) -> None:
    ref = cb.data[len("walk:p:"):]
    if await redis_service.r.get(_ev_key(user.tg_id)):               # اتفاق نیمه‌کاره داری
        await cb.answer()
        text, markup = await render_walk(user)
        return await edit(cb, text, markup)
    slot = ref.split(":")[0]
    if slot not in (MORNING, NOON, EVENING, NIGHT, ANY):             # داستان‌های زنجیره‌ای فقط از مسیر خودشون
        await cb.answer()
        return
    now = utcnow()
    ok = await eco.update_user(user.tg_id, inc={"stats.walks": 1},
                               set_={"walk_ready_at": now + timedelta(minutes=C.WALK_COOLDOWN_MIN)},
                               flt={"$or": [{"walk_ready_at": None}, {"walk_ready_at": {"$lte": now}}]})
    if not ok:
        await cb.answer("😮‍💨 هنوز خسته‌ای!", show_alert=True)
        return
    await cb.answer()
    await redis_service.r.set(_ev_key(user.tg_id), ref, ex=3600)
    text, markup = _render_event(ref)
    await edit(cb, text, markup)


def _adjust(outcomes: list, karma: int) -> list[float]:
    """اعتبار خوب شانس نتیجه‌های خوب رو بیشتر می‌کنه و اعتبار بد برعکس (حداکثر ±۳۰٪)."""
    k = max(-20, min(20, karma)) * 0.015
    return [o[0] * (1 + k if (not isinstance(o[1], (int, float)) or o[1] > 0) else 1 - k if o[1] < 0 else 1)
            for o in outcomes]


@router.callback_query(F.data.startswith("walk:c:"))
async def cb_walk_choice(cb: CallbackQuery, user: User) -> None:
    _, _, slot, i, j = cb.data.split(":")
    ref = f"{slot}:{i}"
    got = await redis_service.r.getdel(_ev_key(user.tg_id))
    if got != ref:
        await cb.answer("⌛ این اتفاق دیگه تموم شده.", show_alert=True)
        return
    await cb.answer()
    emoji, title, _, choices = _event(ref)
    _, outcomes = choices[int(j)]
    u = await eco.get_user(user.tg_id)
    _, mult, story, special = rules.RNG.choices(outcomes, weights=_adjust(outcomes, u.stats.get("karma", 0)))[0]
    unit = job_of(u.shifts)[1]["pay"]
    npc = NPCS.get(ref)
    rel_before = u.stats.get(f"rel_{npc[0]}", 0) if npc else 0
    bonus = rel_level(rel_before)[1] if npc else 0.0
    delta = int(round(unit * mult * (1 + bonus), -2)) if isinstance(mult, (int, float)) else 0
    if delta < 0:                                                    # دوستی ضرر رو کم نمی‌کنه ولی زیادش هم نمی‌کنه
        delta = int(round(unit * mult, -2))
    inc: dict[str, int] = {}
    notes: list[str] = []
    line = ""
    if delta > 0:
        inc["cash"] = delta
        line = f"💰 <b>+{fa(delta)}</b> دلار" + (f"  (بونوس دوستی +{fa(int(bonus * 100))}٪)" if bonus else "")
    elif delta < 0:
        loss = min(-delta, u.cash)
        if loss > 0 and await eco.update_user(u.tg_id, cash=-loss):
            line = f"💸 <b>−{fa(loss)}</b> دلار"
    kd = KARMA.get(f"{ref}:{j}", 0)
    if kd:
        inc["stats.karma"] = kd
        notes.append("⚖️ اعتبارت بیشتر شد 😇" if kd > 0 else "⚖️ اعتبارت کم شد 😈")
    if npc:
        inc[f"stats.rel_{npc[0]}"] = 1
        new_name, _ = rel_level(rel_before + 1)
        if new_name != rel_level(rel_before)[0]:
            notes.append(f"🤝 رابطه‌ات با {npc[1]} شد: <b>{new_name}</b>")
    new_event = u.stats.get(_seen_key(ref), 0) == 0
    inc[f"stats.{_seen_key(ref)}"] = 1
    if new_event:
        count = discovered(u) + 1
        notes.append(f"📔 اتفاق تازه تو دفترچه‌ی شهر ثبت شد ({fa(count)}/{fa(TOTAL_EVENTS)})")
        for need, k in MILESTONES:
            if count == need:
                prize = unit * k
                inc["cash"] = inc.get("cash", 0) + prize
                notes.append(f"🎁 جایزه‌ی دفترچه: <b>+{fa(prize)}</b> دلار!")
    set_ = {"job_ready_at": None} if special == "shift" else None
    if special == "shift":
        line = (line + "\n" if line else "") + "⚡ شیفت بعدیت همین الان آماده‌ست!"
    if inc or set_:
        await eco.update_user(u.tg_id, inc=inc, set_=set_)
    text = f"{emoji} <b>{title}</b>\n\n{story}" + (f"\n\n{line}" if line else "")
    if notes:
        text += "\n\n" + "\n".join(notes)
    if isinstance(special, str) and special.startswith("next:"):      # داستان ادامه داره
        nxt = special[5:]
        await redis_service.r.set(_ev_key(u.tg_id), nxt, ex=3600)
        t2, m2 = _render_event(nxt)
        await edit(cb, text + "\n\n— — —\n\n" + t2, m2)
        return
    await edit(cb, text, kb([b("🚶 یه چرخ دیگه", "walk")], back("menu", "◀️ منو")))
