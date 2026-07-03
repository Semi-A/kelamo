# -*- coding: utf-8 -*-
"""مود زنجیره (⛓): هر کلمه باید با حرف آخرِ کلمه‌ی قبلی شروع شود
و در دسته‌ی همان بازی معتبر باشد. اولین حرف را ربات می‌دهد."""
import random
from .base import BaseMode


def _last_letter(word):
    w = (word or "").strip()
    return w[-1] if w else ""


class ChainMode(BaseMode):
    id = "chain"; name = "زنجیره"; emoji = "⛓"

    def __init__(self, words, category="", ruleset=None):
        super().__init__(words, ruleset)
        self.category = category
        self.current_letter = None   # حرفی که کلمه‌ی بعدی باید با آن شروع شود

    def tutorial(self):
        return ("⛓ <b>مود زنجیره</b>\n"
                f"دسته: <b>{self.category}</b>\n"
                "هر کلمه باید با <b>حرف آخرِ</b> کلمه‌ی قبلی شروع بشه.\n"
                "مثال: سیب ← بادام ← ماه …\nآماده باشید...")

    def _valid_words(self):
        return [w for w in self.words if (w or "").strip()]

    def new_question(self):
        pool = self._valid_words()
        if not pool:
            return {"prompt": "کلمه‌ای برای این دسته ثبت نشده 😅",
                    "answers": set(), "letter": None}
        # حرف شروع را از یک کلمه‌ی تصادفی بگیر (اولین حلقه‌ی زنجیره)
        if self.current_letter is None:
            self.current_letter = self.norm(random.choice(pool))[0]

        letter = self.current_letter
        answers = {
            self.norm(w) for w in pool
            if self.norm(w).startswith(letter)
        }
        return {
            "prompt": (f"⛓ <b>زنجیره — دسته {self.category}</b>\n\n"
                       f"کلمه‌ای بگو که با <b>«{letter}»</b> شروع بشه."),
            "answers": answers,
            "letter": letter,
        }

    def check_answer(self, question, text):
        w = self.norm(text)
        if w not in question.get("answers", set()):
            return False, "نامعتبر"
        # زنجیره را جلو ببر: حرفِ بعدی = حرف آخرِ همین کلمه
        self.current_letter = _last_letter(w)
        return True, None