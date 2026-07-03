# -*- coding: utf-8 -*-
"""🎁 جعبه شانس بعد از پایان مسابقه.

Phase 1: احتمال ظاهر شدن به‌طور محسوس کاهش یافت (۰٫۲۵ → ۰٫۱۰) و نام در همه‌جا
از «Lucky Box» به «🎁 جعبه شانس» تغییر کرد.
"""

import logging
import random
from core import db, progression as pr

logger = logging.getLogger("kalemo.luckybox")

# نام نمایشی واحد برای استفاده در UI/پیام‌ها/لاگ‌ها.
BOX_NAME = "🎁 جعبه شانس"

# احتمال ظاهر شدن جعبه شانس بعد از هر مسابقه (به‌ازای هر بازیکن).
DROP_CHANCE = 0.05

ITEMS = [
    {"type": "coin", "value": 30, "rarity": "common", "weight": 40},
    {"type": "coin", "value": 80, "rarity": "common", "weight": 25},
    {"type": "xp", "value": 40, "rarity": "common", "weight": 25},
    {"type": "xp", "value": 100, "rarity": "rare", "weight": 8},
    {"type": "title", "value": "کلمه‌باز", "rarity": "rare", "weight": 4},
    {"type": "badge", "value": "برق ذهن", "rarity": "rare", "weight": 3},
    {"type": "profile_frame", "value": "طلایی", "rarity": "epic", "weight": 2},
    {"type": "seed", "value": 1, "rarity": "future", "weight": 5},
]


def _pick_item():
    weights = [i["weight"] for i in ITEMS]
    return random.choices(ITEMS, weights=weights, k=1)[0]


def try_grant(uid, match_id=None):
    """با احتمال DROP_CHANCE یک جعبه شانس به بازیکن می‌دهد.

    خطاها لاگ می‌شوند و None برمی‌گردد تا هرگز جریان پایان بازی را نشکنند.
    """
    try:
        if random.random() > DROP_CHANCE:
            return None

        item = _pick_item()
        p = db.get_player(uid)
        if not p:
            return None

        if item["type"] == "coin":
            db.save_player(uid, coins=p["coins"] + int(item["value"]))
        elif item["type"] == "xp":
            lvl, xp, _ = pr.add_xp(p["level"], p["xp"], int(item["value"]))
            db.save_player(uid, level=lvl, xp=xp)

        # title/badge/profile_frame/seed فعلاً فقط در جدول ذخیره می‌شوند.
        db.add_lucky_box(
            user_id=uid,
            match_id=match_id,
            item_type=item["type"],
            item_value=item["value"],
            rarity=item["rarity"],
        )
        logger.info("جعبه شانس به کاربر %s داده شد: %s", uid, item)
        return item
    except Exception:
        logger.exception("خطا در اعطای جعبه شانس به کاربر %s", uid)
        return None


def item_text(item):
    if item["type"] == "coin":
        return f"🪙 {item['value']} سکه"
    if item["type"] == "xp":
        return f"⚡️ {item['value']} XP"
    if item["type"] == "title":
        return f"🏷 عنوان: {item['value']}"
    if item["type"] == "badge":
        return f"🏅 نشان: {item['value']}"
    if item["type"] == "profile_frame":
        return f"🖼 قاب پروفایل: {item['value']}"
    if item["type"] == "seed":
        return f"🌱 بذر: {item['value']}"
    return str(item["value"])
