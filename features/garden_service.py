# -*- coding: utf-8 -*-
"""رویدادهای سبک باغچه کلمو.

این فایل عمداً کوچک نگه داشته شده تا هر بخش ربات بتواند بدون وابستگی UI
به رشد درخت، بذر و پاداش‌های باغچه وصل شود.
"""

import random
from core import db


def on_correct_answer(uid):
    """پاسخ صحیح در مودهای سوالی: رشد کوچک اما فوری."""
    db.garden_add_growth(uid, 3, source="correct_answer", detail="پاسخ صحیح")


def on_match_played(uid):
    """پایان مسابقه برای همه شرکت‌کننده‌ها."""
    db.garden_add_growth(uid, 4, source="match_played", detail="حضور در مسابقه")
    if random.random() < 0.12:
        db.garden_add_seed(uid, source="match_seed")


def on_match_win(uid):
    """برد مسابقه: رشد بیشتر و شانس بذر."""
    db.garden_add_growth(uid, 12, source="match_win", detail="برد مسابقه")
    if random.random() < 0.35:
        db.garden_add_seed(uid, source="win_seed")


def on_lucky_box(uid, item=None):
    """باز شدن/گرفتن Lucky Box باعث رشد و گاهی بذر می‌شود."""
    db.garden_add_growth(uid, 7, source="lucky_box", detail="Lucky Box")
    if item and item.get("type") == "seed":
        seed_type = db.garden_random_seed_type()
        db.garden_add_seed(uid, seed_type, 1, source="lucky_box_seed")
        return seed_type
    if random.random() < 0.18:
        return db.garden_add_seed(uid, source="lucky_box_bonus_seed")
    return None


def on_daily_garden_visit(uid):
    return db.garden_daily_visit(uid)
