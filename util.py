"""کمک‌تابع‌ها: زمان، فرمت اعداد فارسی، پارس مبلغ، متن."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Optional

_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def utcnow() -> datetime:
    """UTC ساده (naive) — مطابق چیزی که Mongo برمی‌گرداند."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fa(n: float | int) -> str:
    """عدد با جداکننده‌ی هزارگان و ارقام فارسی."""
    return f"{int(n):,}".replace(",", "٬").translate(_TO_FA)


def en_digits(s: str) -> str:
    return s.translate(_TO_EN)


def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


def mention(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{esc(name)}</a>'


def dur(seconds: float) -> str:
    s = max(1, int(seconds))
    if s >= 86400:
        d, h = divmod(s, 86400)
        h //= 3600
        return f"{fa(d)} روز" + (f" و {fa(h)} ساعت" if h else "")
    if s >= 3600:
        h, m = divmod(s, 3600)
        m //= 60
        return f"{fa(h)} ساعت" + (f" و {fa(m)} دقیقه" if m else "")
    if s >= 60:
        return f"{fa(s // 60)} دقیقه"
    return f"{fa(s)} ثانیه"


def left(until: Optional[datetime], now: Optional[datetime] = None) -> float:
    """ثانیه‌ی باقی‌مانده تا یک زمان (۰ اگر گذشته/نیست)."""
    if until is None:
        return 0.0
    return max(0.0, (until - (now or utcnow())).total_seconds())


def norm(s: str) -> str:
    """نرمال‌سازی برای تطبیق نام‌ها: ارقام انگلیسی، ی/ک فارسی، حذف فاصله/نیم‌فاصله/علائم."""
    s = en_digits(str(s)).lower().replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"[\u200c\u200f\u200e\s\-_.،,+/]", "", s)


_MULT = {"k": 1_000, "هزار": 1_000, "m": 1_000_000, "میلیون": 1_000_000, "b": 1_000_000_000, "میلیارد": 1_000_000_000}
_AMOUNT_RE = re.compile(r"^(\d+(?:\.\d+)?)(k|m|b|هزار|میلیون|میلیارد)?$")


def parse_amount(token: str, available: Optional[int] = None) -> Optional[int]:
    """«5000»، «۵۰۰۰»، «5k»، «2m»، «همه» → عدد صحیح مثبت یا None."""
    t = en_digits(token.strip().lower()).replace(",", "").replace("٬", "")
    if t in ("all", "همه"):
        return available if available and available > 0 else None
    m = _AMOUNT_RE.match(t)
    if not m:
        return None
    v = int(float(m.group(1)) * _MULT.get(m.group(2) or "", 1))
    return v if v > 0 else None
