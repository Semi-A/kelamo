# -*- coding: utf-8 -*-
"""مود سرنخ (Clue Mode).
ربات یک سرنخ می‌دهد، بازیکن جواب درست را حدس می‌زند.
سلطان جنگل → شیر | برج ایفل → فرانسه | میوه زرد → موز
"""
import random
from .base import BaseMode

# بانک سرنخ‌ها (clue → set of acceptable answers)
CLUES = [
    ("سلطان جنگل", {"شیر"}),
    ("برج ایفل", {"فرانسه", "پاریس"}),
    ("میوه‌ی زرد و خمیده", {"موز"}),
    ("سیاره‌ی سرخ", {"مریخ"}),
    ("پایتخت ایران", {"تهران"}),
    ("بزرگ‌ترین اقیانوس", {"آرام"}),
    ("حیوانی با خرطوم", {"فیل"}),
    ("فلز زرد و گران‌بها", {"طلا"}),
    ("ستاره‌ی مرکز منظومه‌ی شمسی", {"خورشید"}),
    ("سریع‌ترین حیوان خشکی", {"یوزپلنگ", "یوز"}),
    ("نوشیدنی داغ صبحگاهی", {"چای", "قهوه"}),
    ("پرنده‌ای که نمی‌پرد و در قطب است", {"پنگوئن"}),
    ("شهر عاشقان و کلیسای کلوسئوم", {"رم"}),
    ("میوه‌ی قرمز با هسته‌های ریز روی پوست", {"توت‌فرنگی"}),
    ("فصل ریزش برگ‌ها", {"پاییز"}),
    ("بزرگ‌ترین قاره", {"آسیا"}),
    ("نویسنده‌ی شاهنامه", {"فردوسی"}),
    ("کوهی آتش‌فشانی در ژاپن", {"فوجی"}),
    ("حیوانی که عسل می‌سازد", {"زنبور"}),
    ("سیاه و سفید و اهل چین", {"پاندا"}),
]


class ClueMode(BaseMode):
    id = "clue"; name = "سرنخ"; emoji = "🕵️"

    def __init__(self, words, ruleset=None, **kw):
        super().__init__(words, ruleset)
        self._pool = None      # کش تنبل کلمات دارای سرنخ معتبر
        self._used = set()      # کلماتی که همین بازی به‌عنوان سوال آمده‌اند

    def _load_pool(self):
        from core import db
        # فقط کلماتی با clue معتبر و غیرخالی، مستقیم از دیتابیس
        rows = [r for r in db.clue_pool() if (r.get("clue") or "").strip()]
        random.shuffle(rows)   # هر بازی کاملاً تصادفی
        self._pool = rows

    def tutorial(self):
        return ("🕵️ <b>مود سرنخ</b>\n"
                "من یه سرنخ می‌دم، تو جواب درست رو حدس بزن!\n"
                "مثال: <code>سلطان جنگل</code> ← <b>شیر</b>\nآماده باشید...")

    def new_question(self):
        if self._pool is None:
            self._load_pool()

        # کلمه‌ای انتخاب کن که هنوز به‌عنوان سوال استفاده نشده
        candidates = [r for r in self._pool
                      if r["word"] not in self._used]
        if not candidates:
            # همه سرنخ‌ها مصرف شده‌اند → دوره جدید
            self._used.clear()
            candidates = list(self._pool)
        if not candidates:
            return {"prompt": "سرنخی برای این بازی ثبت نشده 😅", "answers": set()}

        row = random.choice(candidates)
        self._used.add(row["word"])
        return {
            "prompt": (f"🕵️ <b>سرنخ:</b>\n\n<b>{row['clue']}</b>\n\n"
                       f"<i>جواب رو حدس بزن!</i>"),
            "answers": {self.norm(row["word"])},
        }

    def check_answer(self, question, text):
        if self.norm(text) in question.get("answers", set()):
            return True, None
        return False, "نادرست"