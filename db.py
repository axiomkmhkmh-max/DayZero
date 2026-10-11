"""اتصال MongoDB (Motor + Beanie) و مدل‌های داده."""

from datetime import datetime
from typing import Optional

from beanie import Document, Indexed, init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

import config
from util import utcnow


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
    streak: int = 0

    # خونه و خانواده
    house: int = 0
    kids: int = 0
    child_ready_at: Optional[datetime] = None
    fed_until: Optional[datetime] = None
    cook: bool = False
    banana_until: Optional[datetime] = None
    potion_until: Optional[datetime] = None
    spouse_id: Optional[int] = None
    love_lock_until: Optional[datetime] = None

    # دارایی‌ها
    forces: dict[str, int] = Field(default_factory=dict)       # g0 g1 g2 h0 … b2
    gear: dict[str, int] = Field(default_factory=dict)         # آیتم‌های فروشگاه
    items: dict[str, int] = Field(default_factory=dict)        # آیتم‌های دارک وب
    businesses: dict[str, int] = Field(default_factory=dict)   # کلید کسب‌وکار → تعداد کارگر
    income_since: datetime = Field(default_factory=utcnow)     # درآمد کاسبی از این لحظه انباشته می‌شود

    # مبارزه
    shield_until: Optional[datetime] = None
    attack_ready_at: Optional[datetime] = None
    cursed_until: Optional[datetime] = None
    alliance_id: Optional[int] = None
    stats: dict[str, int] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "users"
        indexes = [IndexModel([("username", ASCENDING)]), IndexModel([("alliance_id", ASCENDING)]),
                   IndexModel([("last_seen", DESCENDING)])]


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


async def init_db() -> AsyncIOMotorClient:
    client = AsyncIOMotorClient(config.MONGO_URI, serverSelectionTimeoutMS=8000)
    await init_beanie(database=client[config.MONGO_DB],
                      document_models=[User, Alliance, Counter, Match, FightLog])
    return client
