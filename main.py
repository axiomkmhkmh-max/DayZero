"""Day Zero — نقطه‌ی شروع ربات."""
from __future__ import annotations

# ═══════════ پچ خودکار (فقط همین فایل رو جایگزین کن) ═══════════
# موقع هر بار اجرا، قبل از import بقیه‌ی ماژول‌ها تغییرها رو توی فایل‌های پروژه اعمال می‌کنه.
# اگه قبلاً اعمال شده باشه (یا اون قسمت کد دستی عوض شده باشه) رد می‌شه و چیزی خراب نمی‌کنه.
import os as _os, sys as _sys

_PATCHES = [('actions.py',
  '\n'
  '\n'
  '# ───────────────────────── وقت عاشقانه (بکنش / بزن توش) ─────────────────────────\n'
  'async def intimacy(bot, uid: int, group: bool = False) -> str:\n'
  '    """جایزه\u200cی زوج\u200cها: هر ۱۰ دقیقه یک بار (کولداون مشترک)، هر کدوم ۵۰۰ تا ۳٬۰۰۰ دلار شانسی.\n'
  '    متن اضافه\u200cای برمی\u200cگردونه که به پیام «بزن توش» چسبونده می\u200cشه (\'\' = چیزی نشون نده)."""\n',
  '\n'
  '\n'
  '# ───────────────────────── وقت عاشقانه (بکنش / بزن توش) ─────────────────────────\n'
  'async def intimacy_wait(uid: int) -> int:\n'
  '    """ثانیه\u200cی باقی\u200cمانده\u200cی کولداون مشترک زوج (۰ = آزاد یا مجرد)."""\n'
  '    u = await eco.get_user(uid)\n'
  '    if u is None or not u.spouse_id:\n'
  '        return 0\n'
  '    sp = await eco.get_user(u.spouse_id)\n'
  '    if sp is None or sp.spouse_id != uid:\n'
  '        return 0\n'
  '    ttl = await redis_service.r.ttl(f"intim:{_pair(uid, sp.tg_id)}")\n'
  '    return max(0, ttl)\n'
  '\n'
  '\n'
  'async def intimacy(bot, uid: int, group: bool = False) -> str:\n'
  '    """جایزه\u200cی زوج\u200cها: هر ۱۰ دقیقه یک بار (کولداون مشترک)، هر کدوم ۵۰۰ تا ۳٬۰۰۰ دلار شانسی.\n'
  '    متن اضافه\u200cای برمی\u200cگردونه که به پیام «بزن توش» چسبونده می\u200cشه (\'\' = چیزی نشون نده)."""\n'),
 ('combat.py',
  '\n'
  '\n'
  'def protected_reason(target: User, now) -> Optional[str]:\n'
  '    if target.shield_until and target.shield_until > now:\n'
  '        return f"🛡 {esc(target.name)} تا {dur(left(target.shield_until, now))} دیگه محافظت\u200cشده است (تازه\u200cوارد یا تازه غارت شده)."\n'
  '    return None\n'
  '\n'
  '\n'
  'def same_alliance(a: User, b: User) -> bool:\n',
  '\n'
  '\n'
  'def protected_reason(target: User, now) -> Optional[str]:\n'
  '    return None          # تایمر محافظت (تازه\u200cوارد / تازه غارت\u200cشده) حذف شده\n'
  '\n'
  '\n'
  'def same_alliance(a: User, b: User) -> bool:\n'),
 ('combat.py',
  '\n'
  '    if target.cash < C.MIN_VICTIM_CASH:\n'
  '        return f"🕳 جیب {esc(target.name)} خالیه (پولش رو تو گاوصندوق گذاشته)؛ ارزش حمله نداره."\n'
  '    if not await redis_service.r.set(f"pair:{attacker.tg_id}:{target.tg_id}", "1", nx=True, ex=C.PAIR_COOLDOWN_SEC):\n'
  '        return "⏳ همین الان بهش حمله کردی؛ بذار نفس بکشه!"\n'
  '\n'
  '    atk, chosen = rules.attack_power(attacker.gear, attacker.forces, weapons)\n'
  '    dfn, shield, sec = rules.defense_power(target.gear, target.forces)\n',
  '\n'
  '    if target.cash < C.MIN_VICTIM_CASH:\n'
  '        return f"🕳 جیب {esc(target.name)} خالیه (پولش رو تو گاوصندوق گذاشته)؛ ارزش حمله نداره."\n'
  '\n'
  '    atk, chosen = rules.attack_power(attacker.gear, attacker.forces, weapons)\n'
  '    dfn, shield, sec = rules.defense_power(target.gear, target.forces)\n'),
 ('combat.py',
  '            leak = 0\n'
  '        if loot <= 0 and leak <= 0:\n'
  '            return head + "💨 تا رسیدی، طرف پولش رو جابجا کرده بود؛ دست\u200cخالی برگشتی!"\n'
  '        await eco.update_user(target.tg_id, set_={"shield_until": now + timedelta(minutes=C.VICTIM_SHIELD_MIN)})\n'
  '        await eco.update_user(attacker.tg_id, inc={"stats.wins": 1, "stats.loot": loot + leak})\n'
  '        await eco.update_user(target.tg_id, inc={"stats.lost": loot + leak, "stats.hit": 1})\n'
  '        await FightLog(attacker_id=attacker.tg_id, defender_id=target.tg_id, a_name=attacker.name, d_name=target.name,\n',
  '            leak = 0\n'
  '        if loot <= 0 and leak <= 0:\n'
  '            return head + "💨 تا رسیدی، طرف پولش رو جابجا کرده بود؛ دست\u200cخالی برگشتی!"\n'
  '        await eco.update_user(attacker.tg_id, inc={"stats.wins": 1, "stats.loot": loot + leak})\n'
  '        await eco.update_user(target.tg_id, inc={"stats.lost": loot + leak, "stats.hit": 1})\n'
  '        await FightLog(attacker_id=attacker.tg_id, defender_id=target.tg_id, a_name=attacker.name, d_name=target.name,\n'),
 ('combat.py',
  '        text = head + f"💥 <b>حمله موفق!</b> {an} مبلغ <b>{fa(loot)}</b> دلار از جیب {tn} کش رفت!"\n'
  '        if leak:\n'
  '            text += f"\\n🔐 و <b>{fa(leak)}</b> دلار هم از گاوصندوقش نشت کرد!"\n'
  '        victim_dm = f"💥 {esc(attacker.name)} بهت حمله کرد و <b>{fa(loot + leak)}</b> دلار ازت برد!\\n🛡 {C.VICTIM_SHIELD_MIN} دقیقه محافظت\u200cشده\u200cای. سپر/نیروی قوی\u200cتر بخر!"\n'
  '    else:\n'
  '        fresh_att = await eco.get_user(attacker.tg_id)\n'
  '        pen = rules.repel_penalty(fresh_att.cash, dfn)\n',
  '        text = head + f"💥 <b>حمله موفق!</b> {an} مبلغ <b>{fa(loot)}</b> دلار از جیب {tn} کش رفت!"\n'
  '        if leak:\n'
  '            text += f"\\n🔐 و <b>{fa(leak)}</b> دلار هم از گاوصندوقش نشت کرد!"\n'
  '        victim_dm = f"💥 {esc(attacker.name)} بهت حمله کرد و <b>{fa(loot + leak)}</b> دلار ازت برد!\\nسپر/نیروی قوی\u200cتر بخر!"\n'
  '    else:\n'
  '        fresh_att = await eco.get_user(attacker.tg_id)\n'
  '        pen = rules.repel_penalty(fresh_att.cash, dfn)\n'),
 ('config.py',
  '\n'
  '# ───────────────────────── شروع و زمان\u200cها ─────────────────────────\n'
  'START_CASH = 30_000\n'
  'NEWBIE_SHIELD_HOURS = 12          # تازه\u200cوارد تا این مدت قابل حمله/طلسم نیست (اگر خودش حمله کنه از بین می\u200cره)\n'
  'INCOME_CAP_HOURS = 0              # سقف ذخیره\u200cی درآمد کاسبی (ساعت)؛ ۰ = بدون سقف، تا هر وقت خواستن برداشت کنن\n'
  'CYCLE_SECONDS = 6 * 3600          # «درآمد هر ۶ ساعت»\n'
  'DAILY_BASE, DAILY_STEP, DAILY_MAX_STREAK = 10_000, 2_000, 7\n',
  '\n'
  '# ───────────────────────── شروع و زمان\u200cها ─────────────────────────\n'
  'START_CASH = 30_000\n'
  'NEWBIE_SHIELD_HOURS = 0           # تازه\u200cوارد تا این مدت قابل حمله/طلسم نیست (اگر خودش حمله کنه از بین می\u200cره)\n'
  'INCOME_CAP_HOURS = 0              # سقف ذخیره\u200cی درآمد کاسبی (ساعت)؛ ۰ = بدون سقف، تا هر وقت خواستن برداشت کنن\n'
  'CYCLE_SECONDS = 6 * 3600          # «درآمد هر ۶ ساعت»\n'
  'DAILY_BASE, DAILY_STEP, DAILY_MAX_STREAK = 10_000, 2_000, 7\n'),
 ('config.py',
  '\n'
  '# ───────────────────────── خونه ─────────────────────────\n'
  'HOUSES = {  # سطح: (ظرفیت بچه، قیمت)\n'
  '    1: (4, 20_000),\n'
  '    2: (5, 5_000_000),\n'
  '    3: (6, 50_000_000),\n'
  '    4: (7, 450_000_000),\n'
  '    5: (8, 900_000_000),\n'
  '    6: (9, 1_800_000_000),\n'
  '    7: (10, 3_600_000_000),\n'
  '    8: (11, 7_200_000_000),\n'
  '}\n'
  'MAX_HOUSE = max(HOUSES)\n'
  'FORCE_CAP_PER_LEVEL = 5            # سقف نیرو = ۵ × (سطح خونه + ۱)\n',
  '\n'
  '# ───────────────────────── خونه ─────────────────────────\n'
  'HOUSES = {  # سطح: (ظرفیت بچه، قیمت)\n'
  '    1: (4, 14_000),\n'
  '    2: (5, 3_500_000),\n'
  '    3: (6, 35_000_000),\n'
  '    4: (7, 315_000_000),\n'
  '    5: (8, 630_000_000),\n'
  '    6: (9, 1_260_000_000),\n'
  '    7: (10, 2_520_000_000),\n'
  '    8: (11, 5_040_000_000),\n'
  '}\n'
  'MAX_HOUSE = max(HOUSES)\n'
  'FORCE_CAP_PER_LEVEL = 5            # سقف نیرو = ۵ × (سطح خونه + ۱)\n'),
 ('config.py',
  'FIGHT_NOISE = 0.25                 # شانسِ تاس: هر طرف ×(۱±این). قدرت برابر = دقیقاً ۵۰/۵۰؛ قوی\u200cتر معمولاً می\u200cبره. (برای نبرد، دزدی و هک یکسانه)\n'
  'ATTACK_COOLDOWN_SEC = 600\n'
  'PAIR_COOLDOWN_SEC = 3600           # حمله\u200cی دوباره به همون نفر\n'
  'VICTIM_SHIELD_MIN = 30             # بعد از غارت شدن\n'
  'MIN_VICTIM_CASH = 2_000\n'
  'LOOT_MIN, LOOT_BONUS = 0.12, 0.20  # ۱۲٪ تا ۳۲٪ جیب طرف\n'
  'LOOT_CAP_BASE, LOOT_CAP_PER_ATK = 50_000, 400\n',
  'FIGHT_NOISE = 0.25                 # شانسِ تاس: هر طرف ×(۱±این). قدرت برابر = دقیقاً ۵۰/۵۰؛ قوی\u200cتر معمولاً می\u200cبره. (برای نبرد، دزدی و هک یکسانه)\n'
  'ATTACK_COOLDOWN_SEC = 600\n'
  'PAIR_COOLDOWN_SEC = 3600           # حمله\u200cی دوباره به همون نفر\n'
  'VICTIM_SHIELD_MIN = 0              # بعد از غارت شدن\n'
  'MIN_VICTIM_CASH = 2_000\n'
  'LOOT_MIN, LOOT_BONUS = 0.12, 0.20  # ۱۲٪ تا ۳۲٪ جیب طرف\n'
  'LOOT_CAP_BASE, LOOT_CAP_PER_ATK = 50_000, 400\n'),
 ('h_biz.py',
  'import eco\nimport rules\nfrom db import User\nfrom ui import b, back, edit, kb, pairs\nfrom util import fa, utcnow\n\nrouter = Router()\n',
  'import eco\nimport rules\nfrom db import User\nfrom ui import b, back, edit, kb, pairs, receipt\nfrom util import fa, utcnow\n\nrouter = Router()\n'),
 ('h_biz.py',
  '    if not await eco.spend(user.tg_id, bz["price"], set_={f"businesses.{bz[\'key\']}": 0},\n'
  '                           flt={f"businesses.{bz[\'key\']}": {"$exists": False}}, invest=bz["price"]):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer()\n'
  '    await _show(cb, user.tg_id, render_biz, note=f"🎉 {bz[\'emoji\']} {bz[\'name\']} راه افتاد!")\n'
  '\n'
  '\n'
  'async def render_view(u: User, key: str):\n',
  '    if not await eco.spend(user.tg_id, bz["price"], set_={f"businesses.{bz[\'key\']}": 0},\n'
  '                           flt={f"businesses.{bz[\'key\']}": {"$exists": False}}, invest=bz["price"]):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer("✅ خرید انجام شد!")\n'
  '    await receipt(cb, f"{bz[\'emoji\']} {bz[\'name\']} — {fa(bz[\'price\'])} دلار")\n'
  '    await _show(cb, user.tg_id, render_biz, note=f"✅ <b>خرید انجام شد</b> — 🎉 {bz[\'emoji\']} {bz[\'name\']} راه افتاد!")\n'
  '\n'
  '\n'
  'async def render_view(u: User, key: str):\n'),
 ('h_biz.py',
  '    if not await eco.spend(user.tg_id, bz["worker"], inc={f"businesses.{key}": 1},\n'
  '                           flt={f"businesses.{key}": {"$lt": C.MAX_WORKERS}}, invest=bz["worker"]):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer()\n'
  '    await _show(cb, user.tg_id, render_view, key, note="👷 کارگر جدید استخدام شد!")\n',
  '    if not await eco.spend(user.tg_id, bz["worker"], inc={f"businesses.{key}": 1},\n'
  '                           flt={f"businesses.{key}": {"$lt": C.MAX_WORKERS}}, invest=bz["worker"]):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer("✅ خرید انجام شد!")\n'
  '    await receipt(cb, f"👷 کارگر جدید — {fa(bz[\'worker\'])} دلار")\n'
  '    await _show(cb, user.tg_id, render_view, key, note="✅ <b>خرید انجام شد</b> — 👷 کارگر جدید استخدام شد!")\n'),
 ('h_fight.py',
  '            "طرف لازم نیست کاری کنه؛ سپرش خودکار دفاع می\u200cکنه!")\n'
  '    if wait > 0:\n'
  '        text += f"\\n\\n😮\u200d💨 حمله\u200cی بعدی: {dur(wait)} دیگه"\n'
  '    if left(u.shield_until) > 0:\n'
  '        text += f"\\n🛡 محافظت: {dur(left(u.shield_until))} (اگه حمله کنی از بین می\u200cره)"\n'
  '    return text, kb([b("🎲 حمله به هدف تصادفی", "fight:random")], [b("🎯 حمله به @یوزر", "fight:target")],\n'
  '                    [b("💻 هک", "fight:hack"), b("🥷 دزدی از خانه", "fight:steal")],\n'
  '                    [b("📜 آخرین نبردها", "fight:log"), b("🛒 اسلحه و سپر", "mk:shop")], back("menu", "◀️ منو"))\n',
  '            "طرف لازم نیست کاری کنه؛ سپرش خودکار دفاع می\u200cکنه!")\n'
  '    if wait > 0:\n'
  '        text += f"\\n\\n😮\u200d💨 حمله\u200cی بعدی: {dur(wait)} دیگه"\n'
  '    return text, kb([b("🎲 حمله به هدف تصادفی", "fight:random")], [b("🎯 حمله به @یوزر", "fight:target")],\n'
  '                    [b("💻 هک", "fight:hack"), b("🥷 دزدی از خانه", "fight:steal")],\n'
  '                    [b("📜 آخرین نبردها", "fight:log"), b("🛒 اسلحه و سپر", "mk:shop")], back("menu", "◀️ منو"))\n'),
 ('h_fight.py',
  '    now = utcnow()\n'
  '    from datetime import timedelta\n'
  '    flt = {"tg_id": {"$ne": user.tg_id}, "cash": {"$gte": C.MIN_VICTIM_CASH},\n'
  '           "last_seen": {"$gte": now - timedelta(days=7)},\n'
  '           "$or": [{"shield_until": None}, {"shield_until": {"$lte": now}}]}\n'
  '    if user.alliance_id:\n'
  '        flt["alliance_id"] = {"$ne": user.alliance_id}\n'
  '    got = await eco.col().aggregate([{"$match": flt}, {"$sample": {"size": 1}}]).to_list(1)\n'
  '    if not got:\n'
  '        return await cb.answer("🤷 الان هدف مناسبی پیدا نشد (همه یا خالی\u200cان یا محافظت\u200cشده).", show_alert=True)\n'
  '    target = await eco.get_user(got[0]["tg_id"])\n'
  '    await cb.answer("⚔️")\n'
  '    text = await combat.do_attack(cb.bot, await eco.get_user(user.tg_id), target)\n',
  '    now = utcnow()\n'
  '    from datetime import timedelta\n'
  '    flt = {"tg_id": {"$ne": user.tg_id}, "cash": {"$gte": C.MIN_VICTIM_CASH},\n'
  '           "last_seen": {"$gte": now - timedelta(days=7)}}\n'
  '    if user.alliance_id:\n'
  '        flt["alliance_id"] = {"$ne": user.alliance_id}\n'
  '    got = await eco.col().aggregate([{"$match": flt}, {"$sample": {"size": 1}}]).to_list(1)\n'
  '    if not got:\n'
  '        return await cb.answer("🤷 الان هدف مناسبی پیدا نشد (همه یا خالی\u200cان یا غیرفعال).", show_alert=True)\n'
  '    target = await eco.get_user(got[0]["tg_id"])\n'
  '    await cb.answer("⚔️")\n'
  '    text = await combat.do_attack(cb.bot, await eco.get_user(user.tg_id), target)\n'),
 ('h_group.py',
  '                return\n'
  '        return await h_casino.text_play(m, actor, cmd, rest, in_group)\n'
  '    if cmd == "baby":\n'
  '        ok, text = await actions.baby(actor.tg_id, group=in_group)\n'
  '        text += await actions.intimacy(m.bot, actor.tg_id, group=in_group)\n'
  '        sent = await reply(text)\n',
  '                return\n'
  '        return await h_casino.text_play(m, actor, cmd, rest, in_group)\n'
  '    if cmd == "baby":\n'
  '        wait = await actions.intimacy_wait(actor.tg_id)\n'
  '        if wait > 0:        # تو ۱۰ دقیقه\u200cی کولداون زوج: نه جمله\u200cی «خوش گذشت»، نه بچه، نه جایزه\n'
  '            return await reply(f"🥵 تازه سکس کردین! باید <b>{dur(wait)}</b> دیگه صبر کنی.")\n'
  '        ok, text = await actions.baby(actor.tg_id, group=in_group)\n'
  '        text += await actions.intimacy(m.bot, actor.tg_id, group=in_group)\n'
  '        sent = await reply(text)\n'),
 ('h_home.py',
  'import rules\nfrom db import User\nfrom h_target import ask_target\nfrom ui import b, back, edit, kb\nfrom util import dur, esc, fa, left, utcnow\n\nrouter = Router()\n',
  'import rules\nfrom db import User\nfrom h_target import ask_target\nfrom ui import b, back, edit, kb, receipt\nfrom util import dur, esc, fa, left, utcnow\n\nrouter = Router()\n'),
 ('h_home.py',
  '    until = max(now, u.fed_until or now) + timedelta(hours=f["hours"])\n'
  '    if not await eco.spend(user.tg_id, f["price"], set_={"fed_until": until}):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer()\n'
  '    await _show(cb, user.tg_id, render_feed, note=f"✅ {f[\'emoji\']} {f[\'name\']} خوردن؛ {fa(f[\'hours\'])} ساعت کار!")\n'
  '\n'
  '\n'
  '@router.callback_query(F.data == "fam:cook")\n',
  '    until = max(now, u.fed_until or now) + timedelta(hours=f["hours"])\n'
  '    if not await eco.spend(user.tg_id, f["price"], set_={"fed_until": until}):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer("✅ خرید انجام شد!")\n'
  '    await receipt(cb, f"{f[\'emoji\']} {f[\'name\']} — {fa(f[\'price\'])} دلار")\n'
  '    await _show(cb, user.tg_id, render_feed, note=f"✅ <b>خرید انجام شد</b> — {f[\'emoji\']} {f[\'name\']} خوردن؛ {fa(f[\'hours\'])} ساعت کار!")\n'
  '\n'
  '\n'
  '@router.callback_query(F.data == "fam:cook")\n'),
 ('h_home.py',
  '    await eco.settle(user.tg_id)\n'
  '    if not await eco.spend(user.tg_id, C.COOK_PRICE, set_={"cook": True}, invest=C.COOK_PRICE):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer()\n'
  '    await _show(cb, user.tg_id, render_feed, note="✅ آشپز استخدام شد؛ از این به بعد بچه\u200cها همیشه سیرن!")\n'
  '\n'
  '\n'
  '# ───────────────────────── قوای جنسی / بچه ─────────────────────────\n',
  '    await eco.settle(user.tg_id)\n'
  '    if not await eco.spend(user.tg_id, C.COOK_PRICE, set_={"cook": True}, invest=C.COOK_PRICE):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer("✅ خرید انجام شد!")\n'
  '    await receipt(cb, f"👨\u200d🍳 آشپز — {fa(C.COOK_PRICE)} دلار")\n'
  '    await _show(cb, user.tg_id, render_feed, note="✅ <b>خرید انجام شد</b> — 👨\u200d🍳 آشپز استخدام شد؛ از این به بعد بچه\u200cها همیشه سیرن!")\n'
  '\n'
  '\n'
  '# ───────────────────────── قوای جنسی / بچه ─────────────────────────\n'),
 ('h_home.py',
  '        set_["child_ready_at"] = None\n'
  '    if not await eco.spend(user.tg_id, cfg["price"], set_=set_):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer()\n'
  '    await _show(cb, user.tg_id, render_power, note="🍌 خوردی! انرژی گرفتی." if banana else "🧪 معجون رو سر کشیدی! 🔥")\n'
  '\n'
  '\n'
  '# ───────────────────────── همسر ─────────────────────────\n',
  '        set_["child_ready_at"] = None\n'
  '    if not await eco.spend(user.tg_id, cfg["price"], set_=set_):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    name = "🍌 موز" if banana else "🧪 معجون"\n'
  '    await cb.answer("✅ خرید انجام شد!")\n'
  '    await receipt(cb, f"{name} — {fa(cfg[\'price\'])} دلار")\n'
  '    await _show(cb, user.tg_id, render_power,\n'
  '                note=f"✅ <b>خرید انجام شد</b> — {name} — {fa(cfg[\'price\'])} دلار\\n"\n'
  '                     + ("🍌 خوردی! انرژی گرفتی." if banana else "🧪 معجون رو سر کشیدی! 🔥 تایمر بچه صفر شد."))\n'
  '\n'
  '\n'
  '# ───────────────────────── همسر ─────────────────────────\n'),
 ('h_home.py',
  '        return await cb.answer("🏠 ظرفیت نیرو پره! خونه رو ارتقا بده.", show_alert=True)\n'
  '    if not await eco.spend(user.tg_id, f["price"], inc={f"forces.{code}": 1}, invest=f["price"]):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer()\n'
  '    await _show(cb, user.tg_id, render_force_cat, f["cat"], note=f"✅ {f[\'name\']} استخدام شد!")\n'
  '\n'
  '\n'
  '# ───────────────────────── ارتقای خونه ─────────────────────────\n',
  '        return await cb.answer("🏠 ظرفیت نیرو پره! خونه رو ارتقا بده.", show_alert=True)\n'
  '    if not await eco.spend(user.tg_id, f["price"], inc={f"forces.{code}": 1}, invest=f["price"]):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer("✅ خرید انجام شد!")\n'
  '    await receipt(cb, f"{f[\'name\']} — {fa(f[\'price\'])} دلار")\n'
  '    await _show(cb, user.tg_id, render_force_cat, f["cat"], note=f"✅ <b>خرید انجام شد</b> — {f[\'name\']} استخدام شد!")\n'
  '\n'
  '\n'
  '# ───────────────────────── ارتقای خونه ─────────────────────────\n'),
 ('h_home.py',
  '    price = C.HOUSES[nxt][1]\n'
  '    if not await eco.spend(user.tg_id, price, set_={"house": nxt}, flt={"house": user.house}, invest=price):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer()\n'
  '    await _show(cb, user.tg_id, render_house, note=f"🏠 خونه\u200cات شد سطح {fa(nxt)}!")\n',
  '    price = C.HOUSES[nxt][1]\n'
  '    if not await eco.spend(user.tg_id, price, set_={"house": nxt}, flt={"house": user.house}, invest=price):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer("✅ خرید انجام شد!")\n'
  '    await receipt(cb, f"🏠 خونه سطح {fa(nxt)} — {fa(price)} دلار")\n'
  '    await _show(cb, user.tg_id, render_house, note=f"✅ <b>خرید انجام شد</b> — 🏠 خونه\u200cات شد سطح {fa(nxt)}!")\n'),
 ('h_market.py',
  'import eco\nimport rules\nfrom db import User\nfrom ui import b, back, edit, kb\nfrom util import fa\n\nrouter = Router()\n',
  'import eco\nimport rules\nfrom db import User\nfrom ui import b, back, edit, kb, receipt\nfrom util import fa\n\nrouter = Router()\n'),
 ('h_market.py',
  '    if not await eco.spend(user.tg_id, it["price"], set_={f"gear.{it[\'key\']}": 1},\n'
  '                           flt={f"gear.{it[\'key\']}": {"$exists": False}}, invest=it["price"]):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer()\n'
  '    await _show(cb, user.tg_id, render_cat, it["cat"], note=f"✅ {it[\'emoji\']} {it[\'name\']} رفت تو کمدت!")\n'
  '\n'
  '\n'
  '# ───────────────────────── دارک وب ─────────────────────────\n',
  '    if not await eco.spend(user.tg_id, it["price"], set_={f"gear.{it[\'key\']}": 1},\n'
  '                           flt={f"gear.{it[\'key\']}": {"$exists": False}}, invest=it["price"]):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer("✅ خرید انجام شد!")\n'
  '    await receipt(cb, f"{it[\'emoji\']} {it[\'name\']} — {fa(it[\'price\'])} دلار")\n'
  '    await _show(cb, user.tg_id, render_cat, it["cat"], note=f"✅ <b>خرید انجام شد</b> — {it[\'emoji\']} {it[\'name\']} رفت تو کمدت!")\n'
  '\n'
  '\n'
  '# ───────────────────────── دارک وب ─────────────────────────\n'),
 ('h_market.py',
  '    qty = C.JINNGIR_CHARGES if d["key"] == "jinngir" else 1\n'
  '    if not await eco.spend(user.tg_id, d["price"], inc={f"items.{d[\'key\']}": qty}):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer()\n'
  '    await _show(cb, user.tg_id, render_dark, note=f"✅ {d[\'emoji\']} {d[\'name\']} خریدی (کمدت رو ببین)")\n',
  '    qty = C.JINNGIR_CHARGES if d["key"] == "jinngir" else 1\n'
  '    if not await eco.spend(user.tg_id, d["price"], inc={f"items.{d[\'key\']}": qty}):\n'
  '        return await cb.answer("💸 پول کافی نداری!", show_alert=True)\n'
  '    await cb.answer("✅ خرید انجام شد!")\n'
  '    await receipt(cb, f"{d[\'emoji\']} {d[\'name\']} — {fa(d[\'price\'])} دلار")\n'
  '    await _show(cb, user.tg_id, render_dark, note=f"✅ <b>خرید انجام شد</b> — {d[\'emoji\']} {d[\'name\']} خریدی (کمدت رو ببین)")\n'),
 ('h_menu.py',
  '    h = (utcnow() + timedelta(hours=3, minutes=30)).hour      # وقت تهران (بدون ساعت تابستانی)\n'
  '    if 5 <= h < 12:\n'
  '        return f"☀️ صبح بخیر، {name}!"\n'
  '    if 12 <= h < 17:\n'
  '        return f"🌤 ظهر بخیر، {name}!"\n'
  '    if 17 <= h < 21:\n'
  '        return f"🌇 عصر بخیر، {name}!"\n'
  '    return f"🌙 شب بخیر، {name}! بیداری؟"\n'
  '\n',
  '    h = (utcnow() + timedelta(hours=3, minutes=30)).hour      # وقت تهران (بدون ساعت تابستانی)\n'
  '    if 5 <= h < 12:\n'
  '        return f"☀️ صبح بخیر، {name}!"\n'
  '    if 12 <= h < 16:\n'
  '        return f"🌤 ظهر بخیر، {name}!"\n'
  '    if 16 <= h < 20:\n'
  '        return f"🌇 عصر بخیر، {name}!"\n'
  '    return f"🌙 شب بخیر، {name}! بیداری؟"\n'
  '\n'),
 ('h_menu.py',
  '\n'
  'یه شهر، یه جیب تقریباً خالی و یه زندگی آزاد.\n'
  'با <b>{cash}</b> دلار شروع کردی. کار کن، کاسبی راه بنداز، خونه بخر و خانواده بساز، قمار کن،\n'
  'و هر وقت دلت خواست با اسلحه و سپرت بیفتی به جون بقیه. 😈\n'
  '\n'
  '🛡 تا <b>{hours} ساعت</b> تازه\u200cواردی و کسی بهت حمله نمی\u200cکنه (اگه خودت حمله کنی این محافظت از بین می\u200cره)."""\n'
  '\n'
  '\n'
  'async def open_menu(m: Message, actor: User, is_new: bool = False) -> None:\n',
  '\n'
  'یه شهر، یه جیب تقریباً خالی و یه زندگی آزاد.\n'
  'با <b>{cash}</b> دلار شروع کردی. کار کن، کاسبی راه بنداز، خونه بخر و خانواده بساز، قمار کن،\n'
  'و هر وقت دلت خواست با اسلحه و سپرت بیفتی به جون بقیه. 😈"""\n'
  '\n'
  '\n'
  'async def open_menu(m: Message, actor: User, is_new: bool = False) -> None:\n'),
 ('ui.py',
  '        return True\n    r = m.reply_to_message\n    return bool(r and r.from_user and r.from_user.is_bot)\n',
  '        return True\n'
  '    r = m.reply_to_message\n'
  '    return bool(r and r.from_user and r.from_user.is_bot)\n'
  '\n'
  '\n'
  'async def receipt(cb: CallbackQuery, text: str) -> None:\n'
  '    """بعد از هر خرید: یه پیام جدا تو چت می\u200cفرسته که «خرید انجام شد» (خطای ارسال نباید خرید رو خراب کنه)."""\n'
  '    try:\n'
  '        await cb.message.answer(f"✅ <b>خرید انجام شد!</b>\\n{text}")\n'
  '    except Exception:\n'
  '        pass\n')]


def _apply_patches():
    base = _os.path.dirname(_os.path.abspath(__file__))
    cache = {}
    for fname, old, new in _PATCHES:
        path = _os.path.join(base, fname)
        if fname not in cache:
            with open(path, encoding="utf-8") as fh:
                cache[fname] = fh.read()
        text = cache[fname]
        if new in text:
            continue                                   # قبلاً اعمال شده
        if text.count(old) == 1:
            cache[fname] = text.replace(old, new)
        else:
            print(f"[patch] جا نخورد: {fname} -> {old[:50]!r}", file=_sys.stderr)
    for fname, text in cache.items():
        path = _os.path.join(base, fname)
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                continue
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as e:
            print(f"[patch] نتونستم {fname} رو بنویسم: {e}", file=_sys.stderr)


_apply_patches()
# ═══════════════════════════════════════════════════════════════

import asyncio
from datetime import datetime, timezone

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import ErrorEvent
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, InlineKeyboardMarkup

import bank
import config
import duel
import gamelog                      # لاگ چنل تلگرام
from db import init_db
from logs import setup_logging
from middleware import GameMiddleware
from redis_service import LockTimeout, redis_service

import h_admin
import h_alliance
import h_bank
import h_bounty
import h_bankx
import h_biz
import h_casino
import h_fight
import h_auction
import h_group
import h_heist
import h_home
import h_league
import h_life
import h_market
import h_menu
import h_sell
import h_social
import h_spectate
import h_target

try:                                 # اعلان آپدیت به بازیکن‌ها (اگه announce.py کنار main.py باشه)
    import announce  # noqa: F401
except ImportError:
    pass

log = structlog.get_logger("main")

# ───────────────────────── اعداد انگلیسی ─────────────────────────
# همه‌ی متن‌هایی که ربات می‌فرسته (پیام، کپشن، دکمه، هشدار) قبل از ارسال به ارقام انگلیسی تبدیل می‌شن
_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬٪٫", "01234567890123456789,%.")


def _en(s: str) -> str:
    return s.translate(_EN)


def _btn(s: str) -> str:
    """متن دکمه: ارقام انگلیسی + «◀️ منو» → «◀️ داشبورد»."""
    return _en(s).replace("◀️ منو", "◀️ داشبورد")


def _en_markup(mk):
    if isinstance(mk, InlineKeyboardMarkup):
        rows = [[btn.model_copy(update={"text": _btn(btn.text)}) for btn in row] for row in mk.inline_keyboard]
        return mk.model_copy(update={"inline_keyboard": rows})
    return mk


async def _english_digits(make_request, bot, method):
    for field in ("text", "caption"):
        v = getattr(method, field, None)
        if isinstance(v, str):
            setattr(method, field, _en(v))
    mk = getattr(method, "reply_markup", None)
    if mk is not None:
        setattr(method, "reply_markup", _en_markup(mk))
    return await make_request(bot, method)

async def _retry_after(make_request, bot, method):
    """لیمیت تلگرام (۲۰ پیام در دقیقه برای هر گروه): به‌جای گم شدن جواب، صبر می‌کنه و دوباره می‌فرسته."""
    for _ in range(3):
        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as e:
            if e.retry_after > 20:
                raise
            await asyncio.sleep(e.retry_after + 0.5)
    return await make_request(bot, method)


COMMANDS = [
    BotCommand(command="market", description="🛒 بازار"),
    BotCommand(command="home", description="🏠 خونه"),
    BotCommand(command="biz", description="🏪 کاسبی"),
    BotCommand(command="bank", description="🏦 بانک و وام"),
    BotCommand(command="menu", description="🌅 داشبورد"),
    BotCommand(command="games", description="🎰 بازی‌ها (قمارخونه)"),
    BotCommand(command="slot", description="🎰 اسلات (مثال: /slot 5000)"),
    BotCommand(command="wheel", description="🎡 گردونه (مثال: /wheel 5000)"),
    BotCommand(command="coin", description="🪙 شیر یا خط (مثال: /coin 5000 شیر)"),
    BotCommand(command="me", description="💵 موجودی و قدرت"),
    BotCommand(command="attack", description="⚔️ حمله (ریپلای + اسلحه)"),
    BotCommand(command="ttt", description="❌⭕ دوز با شرط (ریپلای + مبلغ)"),
    BotCommand(command="dice", description="🎲 تاس با شرط (ریپلای + مبلغ)"),
    BotCommand(command="marry", description="💍 ثبت ازدواج (ریپلای + مهریه اختیاری)"),
    BotCommand(command="divorce", description="💔 درخواست طلاق / مطالبه‌ی مهریه"),
    BotCommand(command="baby", description="👶 بزن توش / بکنش (جایزه‌ی زوجی هر ۲ دقیقه)"),
    BotCommand(command="pocket", description="💸 جیبی (ساعتی یک بار)"),
    BotCommand(command="steal", description="🥷 دزدی از خانه (ریپلای)"),
    BotCommand(command="hack", description="💻 هکش کن (ریپلای)"),
    BotCommand(command="transfer", description="💸 انتقال پول نقدی (ریپلای + مبلغ)"),
    BotCommand(command="c2c", description="🔁 کارت به کارت از بانک (ریپلای + مبلغ)"),
    BotCommand(command="card", description="💳 کارت من / شماره کارت"),
    BotCommand(command="statement", description="🧾 صورت‌حساب بانکی"),
]


async def _migrate_shifts() -> None:
    """شیفت‌ها از «هر ۲ دقیقه» شدن «هر ۱۵ دقیقه» (۷٫۵ برابر دستمزد، ۷٫۵ برابر کم‌تر شیفت لازم برای ارتقا).
    شمارنده‌ی شیفتِ بازیکن‌های قدیمی فقط یک بار تقسیم بر ۷٫۵ می‌شه تا یهویی مدیرعامل نشن (با یه پرچم تو Mongo)."""
    import eco
    meta = eco.col().database["_meta"]
    r = await meta.update_one({"_id": "shifts_x7_5"}, {"$setOnInsert": {"at": datetime.now(timezone.utc)}}, upsert=True)
    if r.upserted_id is not None:
        await eco.col().update_many({"shifts": {"$gt": 0}},
                                    [{"$set": {"shifts": {"$toInt": {"$floor": {"$divide": ["$shifts", 7.5]}}}}}])
        log.info("shifts_migrated")


async def main() -> None:
    config.validate()
    setup_logging(config.LOG_LEVEL)
    await redis_service.connect(config.REDIS_URL)
    mongo = await init_db()
    await _migrate_shifts()                             # یک‌بار: شمارنده‌ی شیفت به مقیاس جدید (۱۵ دقیقه‌ای)
    import bank as _bank
    log.info("bank_fields_migrated", users=await _bank.migrate_fields())   # فیلدهای بانک/وام/سطح برای بازیکن‌های قدیمی
    n_bans = await h_admin.sync_bans()                  # بن‌شده‌ها از Mongo → Redis
    n_admins = await h_admin.load_admins()              # ادمین‌های اصلی + اضافه‌شده
    if n_admins == 0:
        log.warning("هیچ ادمینی تعریف نشده! آیدی خودت رو با /myid بگیر و تو ADMIN_IDS بذار.")

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(_english_digits)
    bot.session.middleware(_retry_after)
    dp = Dispatcher(storage=RedisStorage(redis_service.r, state_ttl=600, data_ttl=600))   # state گیرکرده بعد ۱۰ دقیقه خودکار پاک می‌شه

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        """هر خطای هندلر لاگ می‌شه (با آیدی و متن)؛ قبلاً بی‌صدا گم می‌شد و بازیکن جواب نمی‌گرفت."""
        import traceback
        exc = event.exception
        msg = event.update.message or (event.update.callback_query.message if event.update.callback_query else None)
        uid = (event.update.message.from_user.id if event.update.message and event.update.message.from_user else None)
        log.error("handler_error", error=repr(exc), uid=uid, chat=getattr(getattr(msg, "chat", None), "id", None),
                  text=(event.update.message.text[:80] if event.update.message and event.update.message.text else None),
                  tb="".join(traceback.format_exception(exc))[-1500:])
        try:
            if isinstance(exc, LockTimeout) and event.update.message:
                await event.update.message.reply("⏳ دستور قبلیت هنوز داره اجرا می‌شه؛ چند ثانیه بعد دوباره بزن.")
            elif event.update.message and event.update.message.chat.type != "private":
                await event.update.message.reply("⚠️ یه خطای موقت پیش اومد؛ دوباره امتحان کن.")
        except Exception:  # noqa: BLE001
            pass
        return True

    ban_mw = h_admin.BanMiddleware()                    # باید قبل از GameMiddleware باشه
    dp.message.outer_middleware(ban_mw)
    dp.callback_query.outer_middleware(ban_mw)

    dp.message.outer_middleware(h_league.Tracker())     # ثبت اعضای فعال گروه‌ها برای لیگ هفتگی

    mw = GameMiddleware()
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)

    # ترتیب مهمه: حالت‌های FSM (گرفتن هدف/اسم) قبل از دستورهای متنی
    for r in (h_admin.router, h_target.router, h_alliance.router, h_menu.router, h_life.router, h_home.router, h_bank.router, h_bankx.router, h_sell.router, h_biz.router, h_market.router,
              h_casino.router, h_fight.router, h_social.router, duel.router,
              h_bounty.router, h_heist.router, h_auction.router, h_league.router, h_spectate.router, h_group.router):
        dp.include_router(r)

    duel.set_bot(bot)
    gamelog.setup(bot)
    sweeper = asyncio.create_task(duel.sweeper(bot))
    logger_task = asyncio.create_task(gamelog.worker())
    loan_task = asyncio.create_task(bank.worker(bot))            # یادآوری قسط وام + ضبط دارایی
    event_tasks = [asyncio.create_task(x(bot)) for x in (h_heist.sweeper, h_auction.sweeper, h_league.sweeper, h_spectate.sweeper, h_bounty.sweeper)]
    try:
        await bot.set_my_commands(COMMANDS)
        await h_admin.publish_commands(bot, COMMANDS)    # منوی دستورهای ادمین (فقط برای خود ادمین‌ها)
        me = await bot.get_me()
        log.info("started", username=me.username, admins=n_admins, bans=n_bans)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        sweeper.cancel()
        logger_task.cancel()
        loan_task.cancel()
        for t in event_tasks:
            t.cancel()
        await bot.session.close()
        await redis_service.close()
        mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
