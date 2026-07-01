# -*- coding: utf-8 -*-
"""رابط پایه مودها. هر مود یک کلاس مستقل است."""
from core.normalize import normalize_word


class BaseMode:
    id = "base"
    name = "پایه"
    emoji = "🎮"

    def __init__(self, words, ruleset=None):
        self.words = list(words)
        self.ruleset = ruleset

    @staticmethod
    def norm(text):
        return normalize_word(text)

    def tutorial(self):
        return f"{self.emoji} مود {self.name}\nآماده باشید..."

    def new_question(self):
        raise NotImplementedError

    def check_answer(self, question, text):
        raise NotImplementedError