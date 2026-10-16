"""منطق خالص ارتش و نبرد اتحاد (بدون دیتابیس/تلگرام) تا راحت تست بشه.

سه قدرت: H انسانی ، A هوایی ، R زرهی. هر یگان یه «نوع دفاعی» (dcls) داره و یه «هدف حمله» (tgt):
برای کشتنِ یگان باید با قدرتِ هم‌نوعِ dcls بزنی؛ و قدرت ضربه‌ی خودش تو نوعِ tgt حساب می‌شه.
"""
from __future__ import annotations

import random
import re
from typing import Optional

import config as C
from util import en_digits, fa, norm

RNG = random.SystemRandom()

UNITS: dict[str, dict] = {}
_ALIAS: dict[str, str] = {}
for _k, _e, _n, _al, _kind, _cat, _d, _t, _a, _h, _p, _s in C.MIL_UNITS:
    UNITS[_k] = {"key": _k, "emoji": _e, "name": _n, "kind": _kind, "cat": _cat, "dcls": _d, "tgt": _t,
                 "atk": _a, "hp": _h, "price": _p, "sup": _s}
    for _x in [*_al, _n, _k]:
        _ALIAS[norm(_x)] = _k
MOBILE = [k for k, u in UNITS.items() if u["kind"] == "mobile"]
CATS = ("human", "armor", "air", "fort")
_STOP = {"تا", "عدد", "فروند", "دستگاه", "نفر", "تعداد", "تای"}
_PREFIX = ("تانک", "جنگنده", "هواپیما", "بمبافکن", "ماشین", "یگان")


def label(k: str, n: Optional[int] = None) -> str:
    u = UNITS[k]
    return (f"{fa(n)}× " if n is not None else "") + f"{u['emoji']} {u['name']}"


def units_text(d: dict, sep: str = "، ") -> str:
    parts = [label(k, n) for k, n in d.items() if n > 0 and k in UNITS]
    return sep.join(parts) if parts else "—"


def clean(d: dict) -> dict[str, int]:
    return {k: int(n) for k, n in (d or {}).items() if k in UNITS and int(n) > 0}


def merge(*ds: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in ds:
        for k, n in clean(d).items():
            out[k] = out.get(k, 0) + n
    return out


def value(d: dict) -> int:
    return sum(UNITS[k]["price"] * n for k, n in clean(d).items())


def supply(d: dict) -> int:
    return sum(UNITS[k]["sup"] * n for k, n in clean(d).items())


# ───────────────────────── پارس متن ─────────────────────────
_ALL_WORDS = {"همه", "all", "تمام", "هرچی"}


def _find_unit(name: str) -> Optional[str]:
    n = norm(name)
    if n in _ALIAS:
        return _ALIAS[n]
    for p in _PREFIX:
        if n.startswith(p) and n[len(p):] in _ALIAS:
            return _ALIAS[n[len(p):]]
    return None


def parse_units(text: str) -> tuple[dict[str, Optional[int]], list[str], bool]:
    """«۲۰ تانک ابرامز و ۲۰ ارپیجی زن» → ({abrams: 20, rpg: 20}, ناشناخته‌ها, آیا عددی/همه نوشته شده).
    مقدار None = «همه‌ی موجودی»."""
    t = en_digits(text or "")
    t = re.sub(r"[،,+&]", " و ", t)
    segs = [s.strip() for s in re.split(r"(?:^|\s)و(?:\s|$)", t) if s.strip()]
    out: dict[str, Optional[int]] = {}
    bad: list[str] = []
    numeric = False
    for seg in segs:
        toks = seg.split()
        cnt: Optional[int] = 1
        explicit = False
        rest: list[str] = []
        for tok in toks:
            if tok.lower() in _ALL_WORDS:
                cnt, explicit = None, True
            elif tok in _STOP:
                continue
            else:
                m = re.fullmatch(r"(\d+)(.*)", tok)
                m2 = re.fullmatch(r"(.*?)(\d+)", tok)
                if m and not re.search(r"\d", m.group(2)):      # «20تانک»
                    cnt, explicit = int(m.group(1)), True
                    if m.group(2):
                        rest.append(m.group(2))
                elif tok.isdigit():
                    cnt, explicit = int(tok), True
                else:
                    rest.append(tok)
                    _ = m2
        name = " ".join(rest)
        k = _find_unit(name) if name else None
        if k is None:
            # «ابرامز۲۰» / «x20» یا اسم‌هایی که عدد تو خودشون دارن (t72، f14)
            k = _find_unit(seg.replace(" ", ""))
            if k is not None:
                cnt, explicit = 1, False
        if k is None:
            bad.append(name or seg)
            continue
        if explicit:
            numeric = True
        if cnt is None:
            out[k] = None
        elif out.get(k, 0) is not None:
            out[k] = (out.get(k) or 0) + cnt
    return out, bad, numeric


def looks_military(text: str) -> bool:
    """آیا متنِ بعد از «حمله» یه حمله‌ی نظامیه؟ (حداقل یه یگان + عدد/همه؛ «حمله کلاشینکف» همون حمله‌ی قدیمیه)"""
    units, _bad, numeric = parse_units(text)
    return bool(units) and numeric


# ───────────────────────── قدرت ─────────────────────────
def offense(d: dict) -> dict[str, float]:
    out = {"H": 0.0, "A": 0.0, "R": 0.0}
    for k, n in clean(d).items():
        u = UNITS[k]
        for cls, share in u["tgt"].items():
            out[cls] += u["atk"] * share * n
    return out


def defense(d: dict) -> dict[str, float]:
    out = {"H": 0.0, "A": 0.0, "R": 0.0}
    for k, n in clean(d).items():
        u = UNITS[k]
        out[u["dcls"]] += u["hp"] * n
    return out


def power_line(d: dict) -> str:
    o, df = offense(d), defense(d)
    return " ".join(f"{C.MIL_CLASSES[c][0]} {fa(o[c])}/{fa(df[c])}" for c in ("H", "A", "R"))


def capacity(house: int, al_level: int) -> int:
    return C.MIL_CAP_BASE + C.MIL_CAP_PER_HOUSE * max(0, house) + C.MIL_CAP_PER_AL * max(0, al_level - 1)


def prod_seconds(key: str, n: int, al_level: int) -> int:
    speed = 1 - min(0.20, C.MIL_PROD_AL_SPEED * max(0, al_level - 1))
    return max(30, int(UNITS[key]["sup"] * n * C.MIL_PROD_SEC_PER_SUP * speed))


# ───────────────────────── شبیه‌سازی نبرد ─────────────────────────
def _stoch(x: float, rng) -> int:
    f = int(x)
    return f + (1 if rng.random() < x - f else 0)


def _losses(own: dict, enemy_off: dict, own_def: dict, rng) -> dict[str, int]:
    lost: dict[str, int] = {}
    n = C.WAR_NOISE
    for k, cnt in clean(own).items():
        cls = UNITS[k]["dcls"]
        eo, dd = enemy_off[cls], own_def[cls]
        if eo <= 0 or dd <= 0:
            continue
        frac = min(1.0, max(0.0, eo / (eo + dd) * rng.uniform(1 - n, 1 + n)))
        got = min(cnt, _stoch(cnt * frac, rng))
        if got:
            lost[k] = got
    return lost


def simulate(att: dict, dfn: dict, rng=RNG) -> dict:
    """att = یگان‌های مهاجم ، dfn = همه‌ی یگان‌های مدافع (پادگان + پدافند + مقابله‌ی دستی).
    خروجی: برنده، تلفات دو طرف (بازنده با ضریب کاهش)، ارزش خسارت‌ها و جدول قدرت هر میدان."""
    att, dfn = clean(att), clean(dfn)
    ao, ad, do, dd = offense(att), defense(att), offense(dfn), defense(dfn)
    raw_att = _losses(att, do, ad, rng)
    raw_def = _losses(dfn, ao, dd, rng)
    v_att, v_def = value(raw_att), value(raw_def)         # ارزشِ از دست‌رفته‌ی هر طرف
    if not dfn:
        att_win = True
    else:
        att_win = v_def > v_att                           # مساوی = مدافع
    tot = v_att + v_def
    margin = abs(v_def - v_att) / tot if tot else (1.0 if att_win else 0.0)
    loser = raw_def if att_win else raw_att
    soft = {k: _stoch(n * C.WAR_LOSER_LOSS_MULT, rng) for k, n in loser.items()}
    soft = {k: n for k, n in soft.items() if n > 0}
    att_lost, def_lost = (raw_att, soft) if att_win else (soft, raw_def)
    return {"att_win": att_win, "margin": margin, "att_lost": att_lost, "def_lost": def_lost,
            "v_att_lost": value(att_lost), "v_def_lost": value(def_lost),
            "arena": {c: {"a_off": ao[c], "a_def": ad[c], "d_off": do[c], "d_def": dd[c]} for c in ("H", "A", "R")}}


def split_losses(lost: dict, *sources: dict) -> list[dict[str, int]]:
    """تلفات رو بین منبع‌ها (به ترتیب) پخش می‌کنه: اول منبع اول، بعد دوم، ..."""
    left = dict(lost)
    outs: list[dict[str, int]] = []
    for src in sources:
        o: dict[str, int] = {}
        for k, n in list(left.items()):
            take = min(n, int(src.get(k, 0)))
            if take > 0:
                o[k] = take
                left[k] = n - take
        outs.append(o)
    return outs


def loot(cash: int, margin: float, loser_lost_value: int) -> int:
    pct = C.WAR_LOOT_MIN + (C.WAR_LOOT_MAX - C.WAR_LOOT_MIN) * min(1.0, max(0.0, margin))
    cap = C.WAR_LOOT_CAP_BASE + C.WAR_LOOT_CAP_VALUE * loser_lost_value
    return max(0, int(min(cash * pct, cap, cash)))
