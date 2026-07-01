# -*- coding: utf-8 -*-
"""تعریف ماموریت‌های روزانه."""
import datetime

MISSIONS = [
    {"key": "play3",   "text": "۳ بازی انجام بده",            "goal": 3, "type": "games",   "coins": 50, "xp": 30},
    {"key": "win1",    "text": "۱ بازی ببر",                  "goal": 1, "type": "wins",    "coins": 60, "xp": 40},
    {"key": "answer10","text": "۱۰ جواب درست بده",            "goal": 10,"type": "answers", "coins": 70, "xp": 50},
    {"key": "suggest1","text": "۱ کلمه جدید پیشنهاد بده",     "goal": 1, "type": "suggest", "coins": 40, "xp": 25},
    {"key": "streak",  "text": "امروز هم سر بزن (ورود روزانه)","goal": 1, "type": "login",   "coins": 30, "xp": 15},
]

def mission_of_day(day_str=None):
    if day_str is None:
        day_str = datetime.date.today().isoformat()
    idx = sum(ord(c) for c in day_str) % len(MISSIONS)
    return MISSIONS[idx]
