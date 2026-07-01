# -*- coding: utf-8 -*-
"""مود جای خالی (Enhanced Missing Letters).
حذف هوشمند حروف با سختی پویا و جلوگیری از الگوی تکراری.
نمونه‌ها: س-ب → سیب | در-ت → درخت | ک-امی-ن → کامیون
"""
import random
from .base import BaseMode

DASH = "-"  # جای خالی نمایشی

# نگاشت سطح سختی → نسبت حروف حذف‌شده
DIFFICULTY = {
    "easy":   0.30,
    "normal": 0.45,
    "hard":   0.65,
}


class BlankMode(BaseMode):
    id = "blank"; name = "جای خالی"; emoji = "🧩"

    def __init__(self, words, ruleset=None, difficulty="normal", **kw):
        super().__init__(words, ruleset)
        self.difficulty = difficulty if difficulty in DIFFICULTY else "normal"
        self._recent = []  # الگوهای اخیر برای ضدتکرار

    def tutorial(self):
        names = {"easy": "آسان", "normal": "معمولی", "hard": "سخت"}
        return ("🧩 <b>مود جای خالی</b>\n"
                "کلمه‌ی ناقص رو کامل کن و کلمه‌ی کامل رو بفرست.\n"
                "مثال: <code>س-ب</code> ← <b>سیب</b>\n"
                f"سختی: <b>{names[self.difficulty]}</b>\nآماده باشید...")

    # ---- حذف هوشمند ----
    def _mask_word(self, word):
        word = (word or "").strip()
        n = len(word)
        if n == 0:
            return DASH
        if n <= 2:
            hide_count = 1
        else:
            ratio = DIFFICULTY[self.difficulty]
            hide_count = max(1, min(n - 1, round(n * ratio)))
        positions = random.sample(range(n), k=hide_count)
        out = []
        hidden = set(positions)
        i = 0
        while i < n:
            if i in hidden:
                while i < n and i in hidden:
                    i += 1
                out.append(DASH)
            else:
                out.append(word[i])
                i += 1
        return "".join(out)

    def new_question(self):
        if not self.words:
            pool = [w for w in self.words if (w or "").strip()]
        if not pool:
            return {"prompt": "کلمه‌ای ثبت نشده 😅", "answer": None}
        word = random.choice(pool)
        for _ in range(8):
            if word not in self._recent:
                break
            word = random.choice(pool)
        # ضدتکرار: تا چند تلاش کلمه‌ای متفاوت از اخیرها انتخاب کن
        word = random.choice(pool)
        for _ in range(8):
            if word not in self._recent:
                break
            word = random.choice(pool)
        masked = self._mask_word(word)
        # اطمینان از این‌که الگو با دفعه قبل یکی نباشد
        self._recent = (self._recent + [word])[-5:]
        return {
            "prompt": f"🧩 کلمه رو کامل کن:\n\n<code>{masked}</code>",
            "answer": word,
        }

    def check_answer(self, question, text):
        ans = question.get("answer")
        if not ans:
            return False, "نامعتبر"
        if self.norm(text) == self.norm(ans):
            return True, None
        return False, "غلط"