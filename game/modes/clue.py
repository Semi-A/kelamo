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
        self._recent = []

    def tutorial(self):
        return ("🕵️ <b>مود سرنخ</b>\n"
                "من یه سرنخ می‌دم، تو جواب درست رو حدس بزن!\n"
                "مثال: <code>سلطان جنگل</code> ← <b>شیر</b>\nآماده باشید...")

    def new_question(self):
        clue, answers = random.choice(CLUES)
        for _ in range(8):
            if clue not in self._recent:
                break
            clue, answers = random.choice(CLUES)
        self._recent = (self._recent + [clue])[-6:]
        return {
            "prompt": f"🕵️ <b>سرنخ:</b>\n\n<b>{clue}</b>\n\n<i>جواب رو حدس بزن!</i>",
            "answers": {self.norm(a) for a in answers},
        }

    def check_answer(self, question, text):
        if self.norm(text) in question["answers"]:
            return True, None
        return False, "نادرست"