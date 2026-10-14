"""اتصال MongoDB (Motor + Beanie) و مدل‌های داده."""

from datetime import datetime
from typing import Any, Optional

from beanie import Document, Indexed, init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import Field, field_validator
from pymongo import ASCENDING, DESCENDING, IndexModel

import config
from util import utcnow


def _as_int(v):
    """float/str/None → int (خراب‌ها → 0)."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return 0


class User(Document):
    tg_id: Indexed(int, unique=True)  # type: ignore[valid-type]
    name: str = "Player"
    username: Optional[str] = None

    # پول: همیشه فقط با $inc تغییر می‌کند (هیچ‌وقت $set) تا با سرقت هم‌زمان قاطی نشود
    cash: int = config.START_CASH
    vault: int = 0
    invested: int = 0                      # ارزش خریدهای ماندگار (برای رتبه‌بندی)
    bet: int = 1_000

    # شغل
    shifts: int = 0
    job_ready_at: Optional[datetime] = None
    daily_at: Optional[datetime] = None
    walk_ready_at: Optional[datetime] = None       # چرخ تو شهر (زندگی روزمره)
    pocket_ready_at: Optional[datetime] = None     # «جیبی» (پول تو جیبی، ساعتی یک بار)
    streak: int = 0

    # خونه و خانواده
    house: int = 0
    kids: int = 0
    kid_names: list[str] = Field(default_factory=list)   # اسم بچه‌ها به ترتیب نام‌گذاری
    child_ready_at: Optional[datetime] = None
    fed_until: Optional[datetime] = None
    cook: bool = False
    banana_until: Optional[datetime] = None
    potion_until: Optional[datetime] = None
    spouse_id: Optional[int] = None
    love_lock_until: Optional[datetime] = None
    mahr: int = 0                                  # مهریه‌ی ازدواج فعلی (روی هر دو همسر ثبت می‌شه)
    mahr_to: Optional[int] = None                  # گیرنده‌ی مهریه (طرفی که بهش پیشنهاد شده)
    rand_marriage: bool = False                    # ازدواج رندوم: بدون قبول/رد، طلاقش هم یک‌طرفه و فوریه

    # دارایی‌ها
    forces: dict[str, int] = Field(default_factory=dict)       # g0 g1 g2 h0 … b2
    gear: dict[str, int] = Field(default_factory=dict)         # آیتم‌های فروشگاه
    items: dict[str, int] = Field(default_factory=dict)        # آیتم‌های دارک وب
    businesses: dict[str, int] = Field(default_factory=dict)   # کلید کسب‌وکار → تعداد کارگر
    income_since: datetime = Field(default_factory=utcnow)     # درآمد کاسبی از این لحظه انباشته می‌شود

    # سطح و ضدزورگیری
    xp: int = 0                                    # سطح = از روی XP حساب می‌شه (rules.level_of)
    antirob: bool = False                          # «ضدزورگیری»: قوی‌ترها (۵ سطح بالاتر) نمی‌تونن بهش دست بزنن

    # بانک و وام (بانک امنه؛ هک/غارت بهش نمی‌رسه)
    bank: int = 0
    bank_level: int = 1
    bank_at: Optional[datetime] = None             # سود تا این لحظه حساب شده
    loan_principal: int = 0                        # مبلغی که گرفته
    loan_total: int = 0                            # کل بدهی با بهره (۰ = وامی نداره)
    loan_paid: int = 0
    loan_daily: int = 0                            # قسط روزانه
    loan_start: Optional[datetime] = None
    loan_remind: int = 0                           # آخرین مرحله‌ی یادآوری که فرستاده شده

    # کارت بانکی و کارت‌به‌کارت (bankx.py)
    card_no: Optional[str] = None                  # شماره‌ی کارت ۱۶ رقمی (یکتا؛ lazy ساخته می‌شه)
    card_frozen: bool = False                      # کارت مسدوده → کارت‌به‌کارتِ خروجی بسته‌ست
    xfer_day: Optional[str] = None                 # روزِ (UTC) شمارنده‌ی سقف روزانه
    xfer_total: int = 0                            # مجموع کارت‌به‌کارتِ امروز
    bstats: dict[str, Any] = Field(default_factory=dict)       # آمار بانکی: ontime/late/repaid/seized/out_n/out_sum/in_n/in_sum

    # مبارزه
    shield_until: Optional[datetime] = None
    attack_ready_at: Optional[datetime] = None
    cursed_until: Optional[datetime] = None
    alliance_id: Optional[int] = None
    stats: dict[str, Any] = Field(default_factory=dict)       # Any: بعضی کلیدها (مثل dw_open) رشته‌ان؛ با dict[str,int] لود شدن بازیکن می‌ترکید

    # ادمین / بن
    banned: bool = False
    ban_reason: str = ""
    banned_at: Optional[datetime] = None
    is_admin: bool = False                         # ادمین اضافه‌شده با /addadmin

    created_at: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)

    # دیتای قدیمی/خراب: اگه جایی عدد اعشاری (مثل 1234.0 یا 99.5) یا رشته‌ی عددی تو دیتابیس نشسته باشه،
    # pydantic موقع لود کردن بازیکن خطا می‌داد و هر کاری روی اون بازیکن (از جمله انتقال پول) می‌ترکید.
    @field_validator("cash", "vault", "invested", "bet", "shifts", "streak", "house", "kids", "mahr",
                     "xp", "bank", "bank_level", "loan_principal", "loan_total", "loan_paid", "loan_daily", "loan_remind", "xfer_total",
                     mode="before")
    @classmethod
    def _to_int(cls, v):
        return _as_int(v)

    @field_validator("forces", "gear", "items", "businesses", mode="before")
    @classmethod
    def _dict_to_int(cls, v):
        if not isinstance(v, dict):
            return {}
        return {str(k): _as_int(x) for k, x in v.items()}

    class Settings:
        name = "users"
        indexes = [IndexModel([("username", ASCENDING)]), IndexModel([("alliance_id", ASCENDING)]),
                   IndexModel([("last_seen", DESCENDING)]),
                   IndexModel([("loan_total", ASCENDING)]),
                   IndexModel([("card_no", ASCENDING)], unique=True, partialFilterExpression={"card_no": {"$type": "string"}})]


class Alliance(Document):
    aid: Indexed(int, unique=True)  # type: ignore[valid-type]
    name: str
    name_key: Indexed(str, unique=True)  # type: ignore[valid-type]
    leader_id: int
    level: int = 1
    bank: int = 0
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "alliances"


class Counter(Document):
    key: Indexed(str, unique=True)  # type: ignore[valid-type]
    seq: int = 0

    class Settings:
        name = "counters"


class Match(Document):
    """شرط‌بندی دو نفره (دوز / تاس). مبلغ شرط هر دو نفر بعد از قبول در escrow است."""
    mid: Indexed(str, unique=True)  # type: ignore[valid-type]
    kind: str                                    # ttt | dice
    p1: int
    p2: int
    n1: str = ""
    n2: str = ""
    stake: int
    status: str = "pending"                      # pending → starting → active → done | cancelled
    board: list[str] = Field(default_factory=lambda: [""] * 9)
    turn: Optional[int] = None
    msgs: list[dict] = Field(default_factory=list)   # [{chat_id, message_id}]
    result: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "matches"
        indexes = [IndexModel([("status", ASCENDING), ("updated_at", ASCENDING)]),
                   IndexModel([("created_at", ASCENDING)], expireAfterSeconds=3 * 86400)]


class FightLog(Document):
    attacker_id: int
    defender_id: int
    a_name: str = ""
    d_name: str = ""
    win: bool
    loot: int = 0
    atk: int = 0
    dfn: int = 0
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "fight_logs"
        indexes = [IndexModel([("attacker_id", ASCENDING)]), IndexModel([("defender_id", ASCENDING)]),
                   IndexModel([("created_at", DESCENDING)], expireAfterSeconds=config.FIGHT_LOG_DAYS * 86400)]


class Ledger(Document):
    """دفتر تراکنش‌های بانکی (صورت‌حساب). amount علامت‌دار؛ bal = موجودی بانک بعد از عملیات (-1 = نامرتبط)."""
    uid: int
    kind: str                                    # dep wd interest xfer_out xfer_in fee loan inst payoff seize upgrade
    amount: int = 0
    bal: int = -1
    note: str = ""
    peer: Optional[int] = None
    ref: str = ""
    at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "bank_ledger"
        indexes = [IndexModel([("uid", ASCENDING), ("at", DESCENDING)]),
                   IndexModel([("at", ASCENDING)], expireAfterSeconds=config.LEDGER_DAYS * 86400)]


class GiftCode(Document):
    code: Indexed(str, unique=True)  # type: ignore[valid-type]
    amount: int
    max_uses: int = 1
    used_by: list[int] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "gift_codes"


async def init_db() -> AsyncIOMotorClient:
    client = AsyncIOMotorClient(config.MONGO_URI, serverSelectionTimeoutMS=8000)
    await init_beanie(database=client[config.MONGO_DB],
                      document_models=[User, Alliance, Counter, Match, FightLog, GiftCode, Ledger])
    for code, (amount, max_uses) in config.GIFT_CODES.items():      # کدهای config.py رو (فقط اگه نبودن) می‌سازه
        await GiftCode.get_motor_collection().update_one(
            {"code": code.upper()},
            {"$setOnInsert": {"amount": amount, "max_uses": max_uses, "used_by": [], "created_at": utcnow()}},
            upsert=True)
    return client
