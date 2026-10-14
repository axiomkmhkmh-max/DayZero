"""منطق خالص بازی (بدون دیتابیس/تلگرام) تا راحت تست شود."""
from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from typing import Optional

import config as C
from util import norm

RNG = random.SystemRandom()

# ───────────────────────── ایندکس آیتم‌ها ─────────────────────────
SHOP_INDEX: dict[str, dict] = {}
for _cat, _c in C.SHOP.items():
    for _it in _c["items"]:
        SHOP_INDEX[_it["key"]] = {**_it, "cat": _cat}

FORCE_INDEX: dict[str, dict] = {}
for _cat, _c in C.FORCES.items():
    for _i, (_n, _p, _pr) in enumerate(_c["tiers"]):
        FORCE_INDEX[f"{_c['code']}{_i}"] = {"cat": _cat, "tier": _i, "name": _n, "power": _p, "price": _pr,
                                           "emoji": _c["emoji"], "atk": _c["atk"], "def": _c["def"]}

BIZ_INDEX = {b[0]: {"key": b[0], "emoji": b[1], "name": b[2], "price": b[3], "income": b[4], "worker": b[5]}
             for b in C.BUSINESSES}
FOOD_INDEX = {f[0]: {"key": f[0], "emoji": f[1], "name": f[2], "price": f[3], "hours": f[4]} for f in C.FOODS}
DARK_INDEX = {d[0]: {"key": d[0], "emoji": d[1], "name": d[2], "price": d[3], "desc": d[4]} for d in C.DARK}


# ───────────────────────── سطح (XP) ─────────────────────────
def level_of(xp: int) -> int:
    """سطح بازیکن از روی XP: int(sqrt(xp / پایه)) + 1."""
    return int((max(0, int(xp or 0)) / C.LEVEL_XP_BASE) ** 0.5) + 1


def xp_for_level(level: int) -> int:
    """حداقل XP لازم برای رسیدن به یک سطح."""
    return max(0, level - 1) ** 2 * C.LEVEL_XP_BASE


def is_weak_vs(target_xp: int, attacker_xp: int) -> bool:
    """ضعیف = ۵ سطح (یا بیشتر) پایین‌تر از مهاجم."""
    return level_of(attacker_xp) - level_of(target_xp) >= C.WEAK_GAP


# ───────────────────────── شغل ─────────────────────────
def job_index(shifts: int) -> int:
    """ایندکس شغل (تو C.JOBS) از روی تعداد شیفت‌ها."""
    idx = 0
    for i, j in enumerate(C.JOBS):
        if shifts >= j["need"]:
            idx = i
    return idx


def job_loan_limit(shifts: int) -> int:
    """سقف وام بر اساس شغل: دستمزد یک شیفت × LOAN_PAY_MULT."""
    return C.JOBS[job_index(shifts)]["pay"] * C.LOAN_PAY_MULT


# ───────────────────────── قدرت ─────────────────────────
def owned(gear: dict, cat: str) -> list[dict]:
    return sorted((SHOP_INDEX[k] for k in gear if k in SHOP_INDEX and SHOP_INDEX[k]["cat"] == cat),
                  key=lambda x: -x["power"])


def best(gear: dict, cat: str) -> Optional[dict]:
    o = owned(gear, cat)
    return o[0] if o else None


def force_count(forces: dict) -> int:
    return sum(v for k, v in forces.items() if k in FORCE_INDEX and v > 0)


def force_cap(house: int) -> int:
    return C.FORCE_CAP_PER_LEVEL * (house + 1)


def force_sums(forces: dict) -> tuple[float, float]:
    """(قدرت حمله‌ی نیروها, قدرت دفاع نیروها)"""
    a = d = 0.0
    for k, n in forces.items():
        f = FORCE_INDEX.get(k)
        if f and n > 0:
            a += f["power"] * f["atk"] * n
            d += f["power"] * f["def"] * n
    return a, d


def attack_power(gear: dict, forces: dict, weapons: Optional[list[str]] = None) -> tuple[int, list[dict]]:
    """قدرت حمله = اسلحه(های) انتخابی (+۱۵٪ ترکیبی) + بهترین ابزار + هکرها + نصف بادیگاردها."""
    ws = owned(gear, "weapon")
    chosen = ws[:2] if weapons is None else [SHOP_INDEX[k] for k in weapons]
    wp = sum(w["power"] for w in chosen)
    if len(chosen) >= 2:
        wp = int(wp * (1 + C.COMBO_BONUS))
    if not chosen:
        wp = C.BARE_HANDS
    tool = best(gear, "tool")
    fa_, _ = force_sums(forces)
    return int(wp + (tool["power"] if tool else 0) + fa_), chosen


def defense_power(gear: dict, forces: dict) -> tuple[int, Optional[dict], Optional[dict]]:
    """دفاع = (دو سپر قوی‌تر [ترکیبی +۱۵٪ مثل اسلحه‌ها] + بهترین تجهیزات امنیتی + نگهبان‌ها + نصف بادیگاردها) × DEFENSE_MULT.
    (قبلاً فقط یک سپر حساب می‌شد و حمله با ۲ اسلحه + ابزار همیشه برنده بود.)"""
    shields = owned(gear, "shield")[:2]
    sp = sum(s["power"] for s in shields)
    if len(shields) >= 2:
        sp = int(sp * (1 + C.COMBO_BONUS))
    se = best(gear, "security")
    _, fd = force_sums(forces)
    total = (sp + (se["power"] if se else 0) + fd) * C.DEFENSE_MULT
    return int(total), (shields[0] if shields else None), se


# ───────────────────────── پارس اسلحه از متن ─────────────────────────
_WEAPON_ALIASES: dict[str, str] = {}
for _it in C.SHOP["weapon"]["items"]:
    _WEAPON_ALIASES[norm(_it["name"])] = _it["key"]
_WEAPON_ALIASES.update({
    "کلاش": "ak", "ak": "ak", "ak47": "ak", "g3": "g3", "uzi": "uzi", "sniper": "sniper",
    "مسلسل": "mg", "تیربار": "mg", "افسانهای": "legend", "افسانه": "legend", "sword": "sword", "knife": "knife",
    "axe": "axe", "colt": "colt", "هفتتیر": "revolver", "شاتگان": "shotgun", "تفنگ": "colt",
})
_WEAPON_ALIASES = {norm(k): v for k, v in _WEAPON_ALIASES.items()}
_ALIAS_ORDER = sorted(_WEAPON_ALIASES, key=len, reverse=True)


def parse_weapons(text: str) -> tuple[list[str], str]:
    """متن → (کلید اسلحه‌ها به ترتیب ظهور، باقی‌مانده‌ی ناشناخته)."""
    t = norm(text)
    found: list[tuple[int, str]] = []
    for alias in _ALIAS_ORDER:
        while True:
            i = t.find(alias)
            if i < 0:
                break
            found.append((i, _WEAPON_ALIASES[alias]))
            t = t[:i] + "\0" * len(alias) + t[i + len(alias):]
    keys: list[str] = []
    for _, k in sorted(found):
        if k not in keys:
            keys.append(k)
    rest = t.replace("\0", "")
    rest = re.sub(r"(و|با)", "", rest)
    return keys, rest


# ───────────────────────── پول تو جیبی ─────────────────────────
def roll_pocket(rng=RNG) -> int:
    """۱٬۰۰۰ تا ۱۰٬۰۰۰؛ بیشترِ وقت‌ها ۲ تا ۴ هزار (گرد به ۱۰۰)."""
    _, lo, hi = rng.choices(C.POCKET_TIERS, weights=[t[0] for t in C.POCKET_TIERS])[0]
    return max(1_000, min(10_000, round(rng.randint(lo, hi) / 100) * 100))


def roll_intimacy(rng=RNG) -> int:
    """جایزه‌ی وقت عاشقانه‌ی زوج‌ها: ۵۰۰ تا ۳٬۰۰۰ (گرد به ۵۰)."""
    _, lo, hi = rng.choices(C.INTIMACY_TIERS, weights=[t[0] for t in C.INTIMACY_TIERS])[0]
    return max(500, min(3_000, round(rng.randint(lo, hi) / 50) * 50))


# ───────────────────────── مبارزه ─────────────────────────
def roll_fight(atk: int, dfn: int, rng=RNG) -> tuple[bool, float, float]:
    """رقابت مشترک نبرد / دزدی از خانه / هک. قدرت برابر → ۵۰/۵۰ ؛ قوی‌تر شانس بیشتری داره."""
    n = C.FIGHT_NOISE
    a = (atk + C.FIGHT_BASE) * rng.uniform(1 - n, 1 + n)
    d = (dfn + C.FIGHT_BASE) * rng.uniform(1 - n, 1 + n)
    return a > d, a, d


# ───────────────────────── دزدی از خانه ─────────────────────────
def theft_power(gear: dict) -> int:
    """قدرت دزد = بهترین ابزار دزدی."""
    t = best(gear, "tool")
    return t["power"] if t else 0


def home_defense(gear: dict, forces: dict) -> tuple[int, Optional[dict]]:
    """دفاع از کمد = بهترین تجهیزات امنیتی خونه + نگهبان‌ها (+ نصف بادیگاردها)."""
    se = best(gear, "security")
    _, fd = force_sums(forces)
    return int((se["power"] if se else 0) + fd), se


def pick_closet_item(gear: dict, items: dict, rng=RNG) -> Optional[tuple[str, str]]:
    """یه وسیله‌ی تصادفی از کمد: ('gear', key) یا ('items', key)."""
    pool = [("gear", k) for k in gear if k in SHOP_INDEX]
    pool += [("items", k) for k, n in items.items() if n > 0 and k in DARK_INDEX]
    return rng.choice(pool) if pool else None


# ───────────────────────── هک ─────────────────────────
BODYGUARD_HACK_RATIO = 0.5          # بادیگاردها (جان ویک و ...) تو هک و ضدهک نصف قدرتشون حساب می‌شه


def hack_power(forces: dict) -> int:
    """قدرت هک (و ضدهک) = مجموع قدرت هکرها + نصف قدرت بادیگاردها."""
    tot = 0.0
    for k, f in FORCE_INDEX.items():
        n = forces.get(k, 0)
        if n <= 0:
            continue
        if f["cat"] == "hacker":
            tot += f["power"] * n
        elif f["cat"] == "bodyguard":
            tot += f["power"] * n * BODYGUARD_HACK_RATIO
    return int(tot)


def hack_percent(a: float, d: float) -> float:
    """۵٪ تا ۵۰٪؛ هر چی هکر نسبت به مدافع قوی‌تر باشه بیشتر."""
    margin = min(1.0, max(0.0, (a - d) / a)) if a > 0 else 0.0
    return C.HACK_MIN + (C.HACK_MAX - C.HACK_MIN) * margin


def loot_amount(target_cash: int, atk: int, a: float, d: float) -> int:
    pct = C.LOOT_MIN + C.LOOT_BONUS * min(1.0, max(0.0, (a - d) / a))
    cap = C.LOOT_CAP_BASE + C.LOOT_CAP_PER_ATK * atk
    return max(0, min(int(target_cash * pct), cap, target_cash))


def repel_penalty(attacker_cash: int, dfn: int) -> int:
    cap = C.REPEL_CAP_BASE + C.REPEL_CAP_PER_DEF * dfn
    return max(0, min(int(attacker_cash * C.REPEL_PENALTY), cap))


# ───────────────────────── درآمد کاسبی ─────────────────────────
def _ov(t0: datetime, t1: datetime, until: Optional[datetime]) -> float:
    """ثانیه‌هایی از [t0,t1] که قبل از until هستند."""
    if until is None or until <= t0:
        return 0.0
    return (min(t1, until) - t0).total_seconds()


def base_income(businesses: dict) -> int:
    """مجموع درآمد هر ۶ ساعت (پایه × (۱ + کارگر))."""
    return sum(BIZ_INDEX[k]["income"] * (1 + w) for k, w in businesses.items() if k in BIZ_INDEX)


def calc_income(base_total: int, since: datetime, now: datetime, *, kids: int, fed_until, cook: bool,
                banana_until, potion_until, cursed_until, base_mult: float) -> int:
    """درآمد انباشته از since تا now (با سقف ذخیره، جن‌زدگی، سیر بودن بچه‌ها و تقویتی‌ها)."""
    t0 = max(since, now - timedelta(hours=C.INCOME_CAP_HOURS)) if C.INCOME_CAP_HOURS else since   # ۰ = بدون سقف
    if cursed_until and cursed_until > t0:
        t0 = min(cursed_until, now)          # دوره‌ی جن‌زدگی درآمد ندارد
    el = (now - t0).total_seconds()
    if el <= 0 or base_total <= 0:
        return 0
    fed = el if cook else min(el, _ov(t0, now, fed_until))
    fed = fed if kids > 0 else 0.0
    ban = min(el, _ov(t0, now, banana_until))
    pot = min(el, _ov(t0, now, potion_until))
    weighted = el * base_mult + C.KID_BONUS * kids * fed + C.BANANA["boost"] * ban + C.POTION["boost"] * pot
    return int(base_total * weighted / C.CYCLE_SECONDS)


# ───────────────────────── بازی‌ها ─────────────────────────
def wheel_spin(rng=RNG) -> float:
    mults, weights = zip(*C.WHEEL)
    return rng.choices(mults, weights=weights, k=1)[0]


SLOT_SYMS = ["🍫", "🍇", "🍋", "7️⃣"]      # ایندکس ۰ = BAR


def slot_decode(v: int) -> list[int]:
    """مقدار دایس 🎰 تلگرام (۱..۶۴) → سه قرقره."""
    n = v - 1
    return [n % 4, (n // 4) % 4, (n // 16) % 4]


def slot_mult(reels: list[int]) -> float:
    if len(set(reels)) == 1:
        return float(C.SLOT_TRIPLE[reels[0]])
    if len(set(reels)) == 2:
        return 1.0
    return 0.0


_LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def ttt_result(board: list[str]) -> Optional[str]:
    """'X' | 'O' | 'draw' | None"""
    for a, b, c in _LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else None
