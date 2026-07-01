# -*- coding: utf-8 -*-
"""منطق پیشرفت بازیکن: XP، Level، استریک. خالص و قابل تست (بدون دیتابیس)."""

def xp_needed(level):
    return 100 + (level - 1) * 50

def add_xp(level, xp, gained):
    gained = max(0, int(gained or 0))      # هیچ‌وقت منفی نشود
    xp = max(0, int(xp or 0))
    level = max(1, int(level or 1))
    xp += gained
    gained_levels = 0
    while xp >= xp_needed(level):
        xp -= xp_needed(level)
        level += 1
        gained_levels += 1
    return level, xp, gained_levels

def levelup_reward(level):
    return 25 + level * 5

def streak_reward(streak_days):
    base = 20
    bonus = min(streak_days, 7) * 10
    return base + bonus

def update_streak(last_day, today, current_streak):
    import datetime
    if not last_day:
        return 1, False
    if last_day == today:
        return current_streak, False
    d_last = datetime.date.fromisoformat(last_day)
    d_today = datetime.date.fromisoformat(today)
    diff = (d_today - d_last).days
    if diff == 1:
        return current_streak + 1, False
    return 1, True
