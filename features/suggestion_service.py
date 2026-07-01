# -*- coding: utf-8 -*-
"""سرویس پیشنهاد کلمات."""

import datetime
from core import db


def create(uid, user_name, word, category, description="", source="menu"):
    word = (word or "").strip()
    category = (category or "").strip()

    if len(word) < 2:
        return False, "کلمه خیلی کوتاه است."

    if len(category) < 2:
        return False, "دسته‌بندی نامعتبر است."

    if db.word_exists(category, word):
        return False, "این کلمه از قبل در دیتابیس وجود دارد."

    ok = db.add_suggestion(
        user_id=uid,
        user_name=user_name,
        word=word,
        category=category,
        description=description,
        source=source
    )

    if not ok:
        return False, "ثبت پیشنهاد انجام نشد."

    db.bump_mission(uid, datetime.date.today().isoformat(), 1)

    return True, "پیشنهاد ثبت شد و وارد صف بررسی مدیران شد."
