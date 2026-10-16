"""مهاجرت یک‌باره: سیستم لول/شعبه‌ی کسب‌وکار حذف شد؛ پولی که بازیکن‌ها قبلاً خرجش کردن برمی‌گرده.

برای هر کسی که فیلد biz_level داره (هم ارتقا هم شعبه‌ی جدید اونجا ثبت می‌شد):
  • هزینه‌ی ارتقاها + قیمت شعبه‌های اضافه + کارگرهای استخدام‌شده تو شعبه‌های اضافه → به جیبش برمی‌گرده
  • شعبه‌های اضافه (کلیدهایی مثل factory@2) و کارگراشون و خود فیلد biz_level پاک می‌شه
بعد از اجرا فیلد biz_level دیگه وجود نداره، پس دوباره‌اجرا شدن بی‌خطره (کاری نمی‌کنه).
"""
from __future__ import annotations

import structlog

import config as C
import eco
import rules

log = structlog.get_logger("migrate_levels")

# فرمول‌های قدیمی (ثابت نگه داشته شدن تا بازگشت پول دقیق باشه)
_UPGRADE_BASE, _UPGRADE_GROWTH = 0.8, 1.5
_BRANCH_MULT = [1.0, 1.5, 2.25]


def _refund(doc: dict) -> tuple[int, list[str], list[str]]:
    """(مبلغ بازگشتی, کلیدهای businesses برای حذف, کلیدهای staff برای حذف)"""
    total = 0
    drop_b: list[str] = []
    drop_s: list[str] = []
    businesses = doc.get("businesses") or {}
    staff = doc.get("staff") or {}
    levels = doc.get("biz_level") or {}
    for inst, lvl in levels.items():
        base = inst.split("@")[0]
        bz = rules.BIZ_INDEX.get(base)
        if not bz:
            continue
        try:
            lvl = int(lvl)
        except (TypeError, ValueError):
            lvl = 1
        total += sum(int(bz["price"] * _UPGRADE_BASE * _UPGRADE_GROWTH ** (l - 1)) for l in range(1, max(1, lvl)))
        if "@" in inst:                       # شعبه‌ی اضافه: قیمت خودش + کارگرهاش
            try:
                n = int(inst.split("@")[1])
            except (IndexError, ValueError):
                n = 2
            total += int(bz["price"] * _BRANCH_MULT[min(max(n - 1, 0), len(_BRANCH_MULT) - 1)])
            drop_b.append(inst)
            for i, w in enumerate(bz["workers"]):
                if i == 0:
                    total += int(businesses.get(inst, 0)) * w["price"]
                else:
                    sk = f"{inst}_{i}"
                    total += int(staff.get(sk, 0)) * w["price"]
                    drop_s.append(sk)
    # شعبه‌هایی که شاید توی businesses باشن ولی تو biz_level نیومده باشن
    for k in businesses:
        if "@" in k and k not in drop_b:
            drop_b.append(k)
    return total, drop_b, drop_s


async def run() -> int:
    n = 0
    col = eco.col()
    async for doc in col.find({"biz_level": {"$exists": True}}):
        refund, drop_b, drop_s = _refund(doc)
        upd: dict = {"$unset": {"biz_level": ""}}
        for k in drop_b:
            upd["$unset"][f"businesses.{k}"] = ""
        for k in drop_s:
            upd["$unset"][f"staff.{k}"] = ""
        if refund > 0:
            upd["$inc"] = {"cash": refund, "invested": -refund}
        await col.update_one({"tg_id": doc["tg_id"]}, upd)
        if refund > 0:
            await col.update_one({"tg_id": doc["tg_id"], "invested": {"$lt": 0}}, {"$set": {"invested": 0}})
        n += 1
        if refund > 0:
            log.info("levels_refund", uid=doc["tg_id"], refund=refund)
    if n:
        log.info("levels_migration_done", users=n)
    return n
