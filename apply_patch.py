#!/usr/bin/env python3
"""Day Zero patch — یک بار اجرا کن (توی پوشه‌ی پروژه، کنار main.py):  python apply_patch.py

۱) ازدواج رندوم: فوری، بدون قبول/رد؛ «درخواست طلاق» تو ازدواج رندوم هم فوری و یک‌طرفه.
۲) سرعت بازی: ضریب GAME_SPEED (پیش‌فرض ۲) روی کولداون‌ها و درآمد کاسبی.
از هر فایل قبل از تغییر یه نسخه‌ی .bak می‌سازه. اگه دوباره اجرا بشه، چیزی رو دوباره عوض نمی‌کنه.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHES = {
    'actions.py': [
        ('    return True, (f"💸 <b>پول تو جیبی!</b> +{fa(amount)} دلار اومد تو جیبت 😎\\n"\n                  f"⏳ دفعه\u200cی بعد: {fa(C.POCKET_COOLDOWN_H)} ساعت دیگه")\n\n',
         '    return True, (f"💸 <b>پول تو جیبی!</b> +{fa(amount)} دلار اومد تو جیبت 😎\\n"\n                  f"⏳ دفعه\u200cی بعد: {dur(C.POCKET_COOLDOWN_H * 3600)} دیگه")\n\n'),
        ('    key = f"intim:{_pair(uid, sp.tg_id)}"\n    cd = C.INTIMACY_COOLDOWN_MIN * 60\n    if not await redis_service.r.set(key, "1", nx=True, ex=cd):\n',
         '    key = f"intim:{_pair(uid, sp.tg_id)}"\n    cd = max(1, int(C.INTIMACY_COOLDOWN_MIN * 60))\n    if not await redis_service.r.set(key, "1", nx=True, ex=cd):\n'),
        ('            f"{mention(sp.tg_id, sp.name)}: <b>+{fa(theirs)}</b> دلار\\n"\n            f"⏳ دفعه\u200cی بعد: {fa(C.INTIMACY_COOLDOWN_MIN)} دقیقه دیگه")\n\n',
         '            f"{mention(sp.tg_id, sp.name)}: <b>+{fa(theirs)}</b> دلار\\n"\n            f"⏳ دفعه\u200cی بعد: {dur(C.INTIMACY_COOLDOWN_MIN * 60)} دیگه")\n\n'),
        ('        return True, (f"👶 مبارک! بچه\u200cی جدیدت به دنیا اومد 🎉\\nخانواده: {fa(u.kids + 1)}/{fa(cap)} بچه — هر بچه\u200cی سیر +۲۵٪ درآمد!"\n                      f"\\n⏳ بچه\u200cی بعدی: {fa(C.CHILD_COOLDOWN_H)} ساعت دیگه{hungry}")\n    return True, (f"{BED_LINE}\\n\\n👶 مبارک! بچه\u200cدار شدین 🎉\\nخانواده: {fa(u.kids + 1)}/{fa(cap)} بچه — هر بچه\u200cی سیر +۲۵٪ درآمد!"\n',
         '        return True, (f"👶 مبارک! بچه\u200cی جدیدت به دنیا اومد 🎉\\nخانواده: {fa(u.kids + 1)}/{fa(cap)} بچه — هر بچه\u200cی سیر +۲۵٪ درآمد!"\n                      f"\\n⏳ بچه\u200cی بعدی: {dur(C.CHILD_COOLDOWN_H * 3600)} دیگه{hungry}")\n    return True, (f"{BED_LINE}\\n\\n👶 مبارک! بچه\u200cدار شدین 🎉\\nخانواده: {fa(u.kids + 1)}/{fa(cap)} بچه — هر بچه\u200cی سیر +۲۵٪ درآمد!"\n'),
        ('    return True, (f"{BED_LINE}\\n\\n👶 مبارک! بچه\u200cدار شدین 🎉\\nخانواده: {fa(u.kids + 1)}/{fa(cap)} بچه — هر بچه\u200cی سیر +۲۵٪ درآمد!"\n                  f"\\n⏳ بچه\u200cی بعدی: {fa(C.CHILD_COOLDOWN_H)} ساعت دیگه{hungry}"\n                  f"\\n\\n✏️ <b>اسم بچه رو چی بذاریم؟</b> روی همین پیام ریپلای کن و اسمش رو بفرست "\n',
         '    return True, (f"{BED_LINE}\\n\\n👶 مبارک! بچه\u200cدار شدین 🎉\\nخانواده: {fa(u.kids + 1)}/{fa(cap)} بچه — هر بچه\u200cی سیر +۲۵٪ درآمد!"\n                  f"\\n⏳ بچه\u200cی بعدی: {dur(C.CHILD_COOLDOWN_H * 3600)} دیگه{hungry}"\n                  f"\\n\\n✏️ <b>اسم بچه رو چی بذاریم؟</b> روی همین پیام ریپلای کن و اسمش رو بفرست "\n'),
        ('\nasync def do_marry(a: int, b_: int, lock_hours: int = 0, mahr: int = 0) -> bool:\n    """ازدواج اتمیک (هر دو باید مجرد باشن)."""\n',
         '\nasync def random_marry(actor: User, target: User) -> str:\n    """«ازدواج رندوم»: بدون پیشنهاد و دکمه\u200cی قبول/رد؛ همون لحظه ازدواج ثبت می\u200cشه (بدون مهریه).\n    اگه خوششون نیومد، هرکدوم «درخواست طلاق» بنویسه، فوری و بدون نظر طرف مقابل طلاق می\u200cگیره."""\n    if actor.spouse_id:\n        return "💍 تو که متاهلی! اول «درخواست طلاق» بده."\n    if target.spouse_id:\n        return f"💔 {esc(target.name)} متاهله."\n    if await redis_service.r.exists(f"nomarry:{_pair(actor.tg_id, target.tg_id)}"):\n        return "🚫 به\u200cخاطر طلسم طلاق، این دو نفر تا مدتی نمی\u200cتونن با هم ازدواج کنن."\n    if not await do_marry(actor.tg_id, target.tg_id, rand=True):\n        return "💔 نشد! یکی\u200cتون همین الان ازدواج کرد؛ دوباره امتحان کن."\n    return (f"💍🎉 {mention(actor.tg_id, actor.name)} و {mention(target.tg_id, target.name)} ازدواج کردن! مبارک باشه 💞\\n"\n            f"+{int(C.MARRIAGE_BONUS * 100)}٪ درآمد کاسبی برای هر دو.\\n"\n            f"<i>خوشت نیومد؟ بنویس «درخواست طلاق» — بدون نیاز به قبول طرف، همون لحظه طلاق ثبت می\u200cشه.</i>")\n\n\nasync def do_marry(a: int, b_: int, lock_hours: int = 0, mahr: int = 0, rand: bool = False) -> bool:\n    """ازدواج اتمیک (هر دو باید مجرد باشن)."""\n'),
        ('    to = b_ if mahr else None                      # مهریه رو a (پیشنهاددهنده) به b_ بدهکاره\n    if not await eco.update_user(a, set_={"spouse_id": b_, "love_lock_until": lock, "mahr": mahr, "mahr_to": to}, flt={"spouse_id": None}):\n        return False\n',
         '    to = b_ if mahr else None                      # مهریه رو a (پیشنهاددهنده) به b_ بدهکاره\n    if not await eco.update_user(a, set_={"spouse_id": b_, "love_lock_until": lock, "mahr": mahr, "mahr_to": to, "rand_marriage": rand}, flt={"spouse_id": None}):\n        return False\n'),
        ('        return False\n    if not await eco.update_user(b_, set_={"spouse_id": a, "love_lock_until": lock, "mahr": mahr, "mahr_to": to}, flt={"spouse_id": None}):\n        await eco.update_user(a, set_={"spouse_id": None, "love_lock_until": None, "mahr": 0, "mahr_to": None})\n        return False\n',
         '        return False\n    if not await eco.update_user(b_, set_={"spouse_id": a, "love_lock_until": lock, "mahr": mahr, "mahr_to": to, "rand_marriage": rand}, flt={"spouse_id": None}):\n        await eco.update_user(a, set_={"spouse_id": None, "love_lock_until": None, "mahr": 0, "mahr_to": None, "rand_marriage": False})\n        return False\n'),
        ('    for uid in (a, b_):\n        await eco.update_user(uid, set_={"spouse_id": None, "love_lock_until": None, "mahr": 0, "mahr_to": None})\n    if ban_hours:\n',
         '    for uid in (a, b_):\n        await eco.update_user(uid, set_={"spouse_id": None, "love_lock_until": None, "mahr": 0, "mahr_to": None, "rand_marriage": False})\n    if ban_hours:\n'),
        ('        return "💔 طلاق ثبت شد."\n    key = f"divreq:{actor.tg_id}"\n',
         '        return "💔 طلاق ثبت شد."\n    if actor.rand_marriage:                                   # ازدواج رندوم: طلاق فوری، بدون قبول/رد\n        await do_divorce(actor.tg_id, sp.tg_id)\n        await redis_service.r.delete(f"divreq:{actor.tg_id}")\n        text = f"💔 {mention(actor.tg_id, actor.name)} و {mention(sp.tg_id, sp.name)} از هم جدا شدن (طلاق ازدواج رندوم)."\n        if in_group:\n            await send(bot, chat_id, text)\n        else:\n            await send(bot, sp.tg_id, f"💔 {esc(actor.name)} طلاق گرفت (ازدواج رندوم).")\n        return None if in_group else "💔 طلاق ثبت شد."\n    key = f"divreq:{actor.tg_id}"\n'),
    ],
    'config.py': [
        ('THROTTLE_MS = _int("THROTTLE_MS", 350)\n\n',
         'THROTTLE_MS = _int("THROTTLE_MS", 350)\n# ضریب سرعت بازی: ۱ = حالت قبلی ، ۲ = همه\u200cی کولداون\u200cها نصف و درآمد کاسبی دو برابر (با env: GAME_SPEED عوض می\u200cشه)\ntry:\n    SPEED = max(0.25, float(os.getenv("GAME_SPEED", "2")))\nexcept (TypeError, ValueError):\n    SPEED = 2.0\n\n'),
        ('INCOME_CAP_HOURS = 0              # سقف ذخیره\u200cی درآمد کاسبی (ساعت)؛ ۰ = بدون سقف، تا هر وقت خواستن برداشت کنن\nCYCLE_SECONDS = 6 * 3600          # «درآمد هر ۶ ساعت»\nDAILY_BASE, DAILY_STEP, DAILY_MAX_STREAK = 10_000, 2_000, 7\n',
         'INCOME_CAP_HOURS = 0              # سقف ذخیره\u200cی درآمد کاسبی (ساعت)؛ ۰ = بدون سقف، تا هر وقت خواستن برداشت کنن\nCYCLE_SECONDS = 6 * 3600 / SPEED          # «درآمد هر ۶ ساعت»\nDAILY_BASE, DAILY_STEP, DAILY_MAX_STREAK = 10_000, 2_000, 7\n'),
        ('# ───────────────────────── شغل (کار شخصی) ─────────────────────────\nJOB_COOLDOWN_MIN = 2\nJOB_TIP_CHANCE = 0.10             # ۱۰٪ شانس انعام (دو برابر)\n',
         '# ───────────────────────── شغل (کار شخصی) ─────────────────────────\nJOB_COOLDOWN_MIN = 2 / SPEED\nJOB_TIP_CHANCE = 0.10             # ۱۰٪ شانس انعام (دو برابر)\n'),
        ('JOB_TIP_CHANCE = 0.10             # ۱۰٪ شانس انعام (دو برابر)\nWALK_COOLDOWN_MIN = 4.5           # فاصله\u200cی «چرخ تو شهر» (زندگی روزمره)\nJOBS = [  # need = تعداد شیفت لازم\n',
         'JOB_TIP_CHANCE = 0.10             # ۱۰٪ شانس انعام (دو برابر)\nWALK_COOLDOWN_MIN = 4.5 / SPEED           # فاصله\u200cی «چرخ تو شهر» (زندگی روزمره)\nJOBS = [  # need = تعداد شیفت لازم\n'),
        ('MARRIAGE_BONUS = 0.10              # متاهل\u200cها +۱۰٪ درآمد کاسبی\nCHILD_COOLDOWN_H = 48\nFOODS = [  # key, emoji, name, price, hours\n',
         'MARRIAGE_BONUS = 0.10              # متاهل\u200cها +۱۰٪ درآمد کاسبی\nCHILD_COOLDOWN_H = 48 / SPEED\nFOODS = [  # key, emoji, name, price, hours\n'),
        ('# «دزدی از خانه»: ابزار دزدی ‹در برابر› امنیت خونه + نگهبان\u200cها. اگه دزد برنده بشه یه وسیله\u200cی تصادفی از کمد طرف می\u200cره تو کمد خودش.\nHTHEFT_COOLDOWN_SEC = 1800          # هر بازیکن هر ۳۰ دقیقه یک بار\n# «هک» (دستور: هکش کن): قدرت هکرهای مهاجم ‹در برابر› هکرهای مدافع. اگه هکر برنده بشه ۵ تا ۵۰٪ پول طرف (جیب و گاوصندوق) کم می\u200cشه\n',
         '# «دزدی از خانه»: ابزار دزدی ‹در برابر› امنیت خونه + نگهبان\u200cها. اگه دزد برنده بشه یه وسیله\u200cی تصادفی از کمد طرف می\u200cره تو کمد خودش.\nHTHEFT_COOLDOWN_SEC = int(1800 / SPEED)          # هر بازیکن هر ۳۰ دقیقه یک بار\n# «هک» (دستور: هکش کن): قدرت هکرهای مهاجم ‹در برابر› هکرهای مدافع. اگه هکر برنده بشه ۵ تا ۵۰٪ پول طرف (جیب و گاوصندوق) کم می\u200cشه\n'),
        ('HACK_MIN, HACK_MAX = 0.05, 0.50\nHACK_COOLDOWN_SEC = 3600            # هر بازیکن ساعتی یک بار\nHACK_MIN_MONEY = 1_000              # طرف کمتر از این پول (جیب + گاوصندوق) داشته باشه ارزش هک نداره\n',
         'HACK_MIN, HACK_MAX = 0.05, 0.50\nHACK_COOLDOWN_SEC = int(3600 / SPEED)            # هر بازیکن ساعتی یک بار\nHACK_MIN_MONEY = 1_000              # طرف کمتر از این پول (جیب + گاوصندوق) داشته باشه ارزش هک نداره\n'),
        ('# ───────────────────────── پول تو جیبی / کد هدیه ─────────────────────────\nPOCKET_COOLDOWN_H = 1\nPOCKET_TIERS = [      # (وزن شانس, حداقل, حداکثر) — بیشترِ وقت\u200cها ۲ تا ۴ هزار، گاهی کمتر/بیشتر؛ گرد به ۱۰۰\n',
         '# ───────────────────────── پول تو جیبی / کد هدیه ─────────────────────────\nPOCKET_COOLDOWN_H = 1 / SPEED\nPOCKET_TIERS = [      # (وزن شانس, حداقل, حداکثر) — بیشترِ وقت\u200cها ۲ تا ۴ هزار، گاهی کمتر/بیشتر؛ گرد به ۱۰۰\n'),
        ('# زوج\u200cهای متاهل با دستور «بکنش» (یا «بزن توش») هر ۱۰ دقیقه یک بار هر کدوم یه پول شانسی می\u200cگیرن (کولداون مشترک بین زوج).\nINTIMACY_COOLDOWN_MIN = 10\nINTIMACY_TIERS = [    # (وزن شانس, حداقل, حداکثر) — بیشتر وقت\u200cها ۱۰۰۰ تا ۲۰۰۰ ؛ میانگین ≈ ۱٬۶۰۰ ؛ گرد به ۵۰\n',
         '# زوج\u200cهای متاهل با دستور «بکنش» (یا «بزن توش») هر ۱۰ دقیقه یک بار هر کدوم یه پول شانسی می\u200cگیرن (کولداون مشترک بین زوج).\nINTIMACY_COOLDOWN_MIN = 10 / SPEED\nINTIMACY_TIERS = [    # (وزن شانس, حداقل, حداکثر) — بیشتر وقت\u200cها ۱۰۰۰ تا ۲۰۰۰ ؛ میانگین ≈ ۱٬۶۰۰ ؛ گرد به ۵۰\n'),
        ('FIGHT_NOISE = 0.25                 # شانسِ تاس: هر طرف ×(۱±این). قدرت برابر = دقیقاً ۵۰/۵۰؛ قوی\u200cتر معمولاً می\u200cبره. (برای نبرد، دزدی و هک یکسانه)\nATTACK_COOLDOWN_SEC = 600\nPAIR_COOLDOWN_SEC = 3600           # حمله\u200cی دوباره به همون نفر\nVICTIM_SHIELD_MIN = 30             # بعد از غارت شدن\n',
         'FIGHT_NOISE = 0.25                 # شانسِ تاس: هر طرف ×(۱±این). قدرت برابر = دقیقاً ۵۰/۵۰؛ قوی\u200cتر معمولاً می\u200cبره. (برای نبرد، دزدی و هک یکسانه)\nATTACK_COOLDOWN_SEC = int(600 / SPEED)\nPAIR_COOLDOWN_SEC = int(3600 / SPEED)           # حمله\u200cی دوباره به همون نفر\nVICTIM_SHIELD_MIN = 30             # بعد از غارت شدن\n'),
    ],
    'db.py': [
        ('    mahr_to: Optional[int] = None                  # گیرنده\u200cی مهریه (طرفی که بهش پیشنهاد شده)\n\n',
         '    mahr_to: Optional[int] = None                  # گیرنده\u200cی مهریه (طرفی که بهش پیشنهاد شده)\n    rand_marriage: bool = False                    # ازدواج رندوم: بدون قبول/رد، طلاقش هم یک\u200cطرفه و فوریه\n\n'),
    ],
    'gamelog.py': [
        ('        def f_marry(orig):\n            async def w(a, b, lock_hours=0):\n                ok = await orig(a, b, lock_hours)\n                if ok:\n',
         '        def f_marry(orig):\n            async def w(a, b, lock_hours=0, *args, **kwargs):\n                ok = await orig(a, b, lock_hours, *args, **kwargs)\n                if ok:\n'),
    ],
    'h_group.py': [
        ('        await reply(f"🎲💍 <b>ازدواج رندوم!</b> قرعه به نام {mention(target.tg_id, target.name)} افتاد 😏")\n        res = await actions.marry(m.bot, actor, target, m.chat.id, True)\n        if res:\n',
         '        await reply(f"🎲💍 <b>ازدواج رندوم!</b> قرعه به نام {mention(target.tg_id, target.name)} افتاد 😏")\n        res = await actions.random_marry(actor, target)\n        if res:\n'),
        ('<b>🎲 رندوم</b> <i>(طرف از بین کسایی که تو همین گروه پیام دادن)</i>\n<code>ازدواج رندوم</code> ← پیشنهاد با دکمه\u200cی قبول/رد\n<code>هک رندوم</code> ← غنیمت {fa(int(RANDOM_LOOT_MULT * 100))}٪ حالت ریپلای\n',
         '<b>🎲 رندوم</b> <i>(طرف از بین کسایی که تو همین گروه پیام دادن)</i>\n<code>ازدواج رندوم</code> ← ازدواج فوری (بدون قبول/رد)؛ «درخواست طلاق» هم فوری و یک\u200cطرفه\u200cست\n<code>هک رندوم</code> ← غنیمت {fa(int(RANDOM_LOOT_MULT * 100))}٪ حالت ریپلای\n'),
    ],
    'h_home.py': [
        ('from ui import b, back, edit, kb\nfrom util import dur, esc, fa, left, utcnow\n\n',
         'from ui import b, back, edit, kb\nfrom util import dur, esc, fa, left, speedy, utcnow\n\n'),
        ('    text = (f"💪 <b>قوای جنسی</b>\\n\\n👶 بچه\u200cها: {fa(u.kids)}/{fa(cap)}\\n{timer}\\n\\n"\n            f"هر {fa(C.CHILD_COOLDOWN_H)} ساعت می\u200cشه یه بچه ساخت.\\n💬 دستور متنی: <code>بزن توش</code>\\n"\n            f"🍌 موز: {fa(C.BANANA[\'cut_hours\'])} ساعت از تایمر کم می\u200cکنه + {int(C.BANANA[\'boost\'] * 100)}٪ درآمد کاسبی برای {fa(C.BANANA[\'boost_hours\'])} ساعت\\n"\n',
         '    text = (f"💪 <b>قوای جنسی</b>\\n\\n👶 بچه\u200cها: {fa(u.kids)}/{fa(cap)}\\n{timer}\\n\\n"\n            f"هر {dur(C.CHILD_COOLDOWN_H * 3600)} می\u200cشه یه بچه ساخت.\\n💬 دستور متنی: <code>بزن توش</code>\\n"\n            f"🍌 موز: {fa(C.BANANA[\'cut_hours\'])} ساعت از تایمر کم می\u200cکنه + {int(C.BANANA[\'boost\'] * 100)}٪ درآمد کاسبی برای {fa(C.BANANA[\'boost_hours\'])} ساعت\\n"\n'),
        ('            text += f"\\n💰 مهریه: <b>{fa(u.mahr)}</b> دلار ({who})"\n        text += f"\\n💞 جایزه\u200cی زوجی: دستور <code>بکنش</code> هر {fa(C.INTIMACY_COOLDOWN_MIN)} دقیقه"\n        if lock > 0:\n',
         '            text += f"\\n💰 مهریه: <b>{fa(u.mahr)}</b> دلار ({who})"\n        text += f"\\n💞 جایزه\u200cی زوجی: دستور <code>بکنش</code> هر {dur(C.INTIMACY_COOLDOWN_MIN * 60)}"\n        if lock > 0:\n'),
        ('            "<code>حمله اجنه</code> · <code>دزد</code> · <code>حمله</code> · <code>دوز 5000</code> · <code>تاس 5000</code>")\n    await edit(cb, text, kb(back("fam", "◀️ خانواده")))\n\n',
         '            "<code>حمله اجنه</code> · <code>دزد</code> · <code>حمله</code> · <code>دوز 5000</code> · <code>تاس 5000</code>")\n    await edit(cb, speedy(text), kb(back("fam", "◀️ خانواده")))\n\n'),
    ],
    'h_menu.py': [
        ('from ui import b, back, claim, edit, is_private, kb, url_b\nfrom util import dur, esc, fa, left, utcnow\n\n',
         'from ui import b, back, claim, edit, is_private, kb, url_b\nfrom util import dur, esc, fa, left, speedy, utcnow\n\n'),
        ('            f"💵 دستمزد هر شیفت: <b>{fa(job[\'pay\'])}</b>\\n"\n            f"⏱ هر {fa(C.JOB_COOLDOWN_MIN)} دقیقه یک شیفت (۱۰٪ شانس انعام دو برابر)\\n"\n            f"📈 شیفت\u200cهای انجام\u200cشده: {fa(u.shifts)}\\n")\n',
         '            f"💵 دستمزد هر شیفت: <b>{fa(job[\'pay\'])}</b>\\n"\n            f"⏱ هر {dur(C.JOB_COOLDOWN_MIN * 60)} یک شیفت (۱۰٪ شانس انعام دو برابر)\\n"\n            f"📈 شیفت\u200cهای انجام\u200cشده: {fa(u.shifts)}\\n")\n'),
        ('    page = int(cb.data.split(":")[1])\n    text = HELP_PAGES[page][1]\n    tabs = [b(("• " if i == page else "") + name, f"help:{i}") for i, (name, _) in enumerate(HELP_PAGES)]\n',
         '    page = int(cb.data.split(":")[1])\n    text = speedy(HELP_PAGES[page][1])\n    tabs = [b(("• " if i == page else "") + name, f"help:{i}") for i, (name, _) in enumerate(HELP_PAGES)]\n'),
    ],
    'util.py': [
        ('    return v if v > 0 else None\n',
         '    return v if v > 0 else None\n\n\ndef speedy(text: str) -> str:\n    """عددهای زمانی ثابتِ متن\u200cهای راهنما رو با ضریب سرعت بازی (GAME_SPEED) هماهنگ می\u200cکنه."""\n    import config as C\n    if C.SPEED == 1:\n        return text\n    pairs = [\n        ("هر ۳۰ دقیقه", f"هر {dur(C.HTHEFT_COOLDOWN_SEC)}"),\n        ("ساعتی یک بار", f"هر {dur(C.HACK_COOLDOWN_SEC)} یک بار"),\n        ("(ساعتی)", f"(هر {dur(C.POCKET_COOLDOWN_H * 3600)})"),\n        ("هر ۴۸ ساعت", f"هر {dur(C.CHILD_COOLDOWN_H * 3600)}"),\n        ("هر <b>۴۸ ساعت</b>", f"هر <b>{dur(C.CHILD_COOLDOWN_H * 3600)}</b>"),\n        ("هر ۱۰ دقیقه", f"هر {dur(C.INTIMACY_COOLDOWN_MIN * 60)}"),\n        ("هر ۲ دقیقه", f"هر {dur(C.JOB_COOLDOWN_MIN * 60)}"),\n        ("درآمد هر ۶ ساعت", f"درآمد هر {dur(C.CYCLE_SECONDS)}"),\n    ]\n    for old, new in pairs:\n        text = text.replace(old, new)\n    return text\n'),
    ],
}


def main() -> int:
    bad = 0
    for fname, pairs in PATCHES.items():
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            print(f"❌ {fname} پیدا نشد (این اسکریپت باید کنار فایل‌های پروژه باشه)")
            bad += 1
            continue
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        crlf = "\r\n" in text
        if crlf:
            text = text.replace("\r\n", "\n")
        new_text, applied, skipped = text, 0, 0
        for old, new in pairs:
            if new in new_text:
                skipped += 1
            elif new_text.count(old) == 1:
                new_text = new_text.replace(old, new)
                applied += 1
            else:
                print(f"❌ {fname}: یه تیکه از فایل با نسخه‌ای که پچ براش ساخته شده فرق داره؛ این فایل دست نخورد.")
                bad += 1
                applied = -1
                break
        if applied > 0:
            shutil.copyfile(path, path + ".bak")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_text.replace("\n", "\r\n") if crlf else new_text)
            print(f"✅ {fname}: {applied} تغییر اعمال شد")
        elif applied == 0:
            print(f"➖ {fname}: قبلاً اعمال شده")
    if bad:
        print("\nبعضی فایل‌ها اعمال نشدن؛ بالا رو ببین.")
        return 1
    print("\nتمام! حالا ربات رو دوباره اجرا/دیپلوی کن. (سرعت: متغیر GAME_SPEED، پیش‌فرض ۲)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
