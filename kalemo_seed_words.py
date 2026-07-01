# -*- coding: utf-8 -*-
"""Seed اختصاصی اسم‌وفامیل کلمو.

فقط این دسته‌ها را نگه می‌دارد و کامل می‌کند:
غذا، رنگ، میوه، حیوان، اشیا، عضو بدن، شهر، کشور، شغل
"""

from core import db


def seed_kalemo_words():
    db.init()
    db.seed_namefamily_words(clean_extra_categories=True)
    total = sum(count for _, count in db.list_categories())
    return total


if __name__ == "__main__":
    total = seed_kalemo_words()
    print(f"✅ NameFamily database cleaned and seeded. total words: {total}")
