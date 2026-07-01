# -*- coding: utf-8 -*-
"""مودهای کلاسیک: رندوم و انتخابی. هر دو فقط با دیتابیس معتبر کار می‌کنند."""
from .base import BaseMode


class ClassicRandomMode(BaseMode):
    id = "classic_random"
    name = "کلاسیک رندوم"
    emoji = "🎯"

    def __init__(self, words, category="", ruleset=None):
        super().__init__(words, ruleset)
        self.category = category

    def tutorial(self):
        return (
            f"🎯 <b>کلاسیک رندوم</b>\n"
            f"دسته: <b>{self.category}</b>\n"
            "کلمه‌ی مرتبط بفرست."
        )

    def new_question(self):
        return {
            "prompt": f"📂 دسته: <b>{self.category}</b>\nکلمه‌ی مرتبط بگو.",
            "answers": {self.norm(w) for w in self.words},
        }

    def check_answer(self, question, text):
        if self.norm(text) not in question.get("answers", set()):
            return False, "نامعتبر"
        return True, None


class ClassicChoiceMode(ClassicRandomMode):
    id = "classic_choice"
    name = "کلاسیک انتخابی"
    emoji = "📂"

    def tutorial(self):
        return (
            f"📂 <b>کلاسیک انتخابی</b>\n"
            f"دسته: <b>{self.category}</b>\n"
            "کلمه‌ی مرتبط بفرست."
        )