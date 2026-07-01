# -*- coding: utf-8 -*-
"""نرمال‌سازی مرکزی کلمات فارسی — همه‌ی مودها و دیتابیس از همین استفاده می‌کنند."""
import re

_AR_FA = str.maketrans({
    "ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ؤ": "و", "ئ": "ی",
})
# اعراب و علائم کوچک عربی
_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_SEPARATORS = re.compile(r"[\s\u200c\u200d\-ـ_]+")


def normalize_word(text):
    """برای مقایسه دقیق واژه‌ها: فاصله، نیم‌فاصله، خط فاصله و کشیده نادیده گرفته می‌شوند."""
    s = (text or "").strip().translate(_AR_FA)
    s = _DIACRITICS.sub("", s)
    s = _SEPARATORS.sub("", s)
    return s.lower()