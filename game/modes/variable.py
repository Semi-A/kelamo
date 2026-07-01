# -*- coding: utf-8 -*-
"""مود قوانین متغیر: هر دور چند قانون تصادفی فعال می‌شود؛
کلمه باید هم در دسته باشد و هم همه قوانین آن دور را رعایت کند.
نکته: قوانین طوری انتخاب می‌شوند که حداقل یک کلمه‌ی دسته آن‌ها را رعایت کند
(تا دور غیرقابل‌بردن نشود)."""
import random
from .base import BaseMode
from game.rules import RuleSet

class VariableMode(BaseMode):
    id = "variable"; name = "قوانین متغیر"; emoji = "🎲"

    def tutorial(self):
        return ("🎲 مود قوانین متغیر\n"
                "هر دور چند قانون تصادفی فعال می‌شه (مثلاً «شروع با م» + «حداقل ۵ حرف»).\n"
                "کلمه‌ای بگو که هم تو دسته باشه هم قوانین رو رعایت کنه.\nآماده باشید...")

    def _solvable_ruleset(self, attempts=25):
        """یک RuleSet می‌سازد که حداقل یک کلمه‌ی دسته آن را رعایت کند."""
        for _ in range(attempts):
            rs = RuleSet().randomize_for_round(n=2)
            if any(rs.validate(w)[0] for w in self.words):
                return rs
        # اگر با ۲ قانون نشد، با یک قانون
        for _ in range(attempts):
            rs = RuleSet().randomize_for_round(n=1)
            if any(rs.validate(w)[0] for w in self.words):
                return rs
        return RuleSet()  # بدون قانون (همیشه حل‌شدنی)

    def new_question(self):
        if not [w for w in self.words if (w or "").strip()]:
            return {"prompt": "کلمه‌ای برای این دسته ثبت نشده 😅",
                    "ruleset": None, "answers": set()}
        rs = self._solvable_ruleset()
        return {"prompt": f"قوانین این دور:\n{rs.describe()}\n\nیه کلمه‌ی مناسب بگو!",
                "ruleset": rs, "answers": {self.norm(w) for w in self.words}}

    def check_answer(self, question, text):
        if not question.get("ruleset"):
            return False, "نامعتبر"
        w = self.norm(text)
        if w not in question["answers"]:
            return False, "نامرتبط"
        # قوانین روی متن اصلی کاربر اعمال می‌شوند (طول/حروف)
        ok, failed = question["ruleset"].validate(text.strip())
        if not ok:
            return False, f"قانون رعایت نشد: {failed}"
        return True, None