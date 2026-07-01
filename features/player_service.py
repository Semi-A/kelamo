# -*- coding: utf-8 -*-
"""سرویس بازیکن: منطق پیشرفت/استریک/ماموریت + مدیریت نام نمایشی."""
import datetime
import time
import config
from core import db, progression as pr, missions as ms
from ui import cards


def today():
    return datetime.date.today().isoformat()


def register(uid, name):
    existed = db.get_player(uid) is not None
    p = db.ensure_player(uid, name)
    return p, (not existed)


def display_name(uid, fallback=""):
    return db.get_display_name(uid) or fallback


# ---- نام نمایشی ----
def validate_name(name):
    """قوانین: ۳ تا ۲۰ کاراکتر. برمی‌گرداند (ok, msg)."""
    name = (name or "").strip()
    if len(name) < 3:
        return False, "نام باید حداقل ۳ کاراکتر باشه."
    if len(name) > 20:
        return False, "نام باید حداکثر ۲۰ کاراکتر باشه."
    return True, None


def name_cooldown_left(uid):
    """ثانیه‌های باقی‌مانده تا اجازه‌ی تغییر نام (۰ یعنی آزاد)."""
    p = db.get_player(uid)
    if not p:
        return 0
    last = p.get("name_changed_at", 0)
    if last == 0:
        return 0
    elapsed = int(time.time()) - last
    left = config.NAME_CHANGE_COOLDOWN - elapsed
    return max(0, left)


def set_name(uid, fallback, name):
    """تلاش برای ثبت نام نمایشی. برمی‌گرداند (ok, msg)."""
    db.ensure_player(uid, fallback)
    ok, msg = validate_name(name)
    if not ok:
        return False, msg
    name = name.strip()
    # اگر همین نام فعلی است
    current = db.get_display_name(uid)
    if current and current != name:
        left = name_cooldown_left(uid)
        if left > 0:
            days = left // 86400
            hours = (left % 86400) // 3600
            when = f"{days} روز و {hours} ساعت" if days else f"{hours} ساعت"
            return False, f"تا تغییر بعدی باید {when} صبر کنی."
    if db.is_display_name_taken(name, exclude_uid=uid):
        return False, "این نام قبلاً گرفته شده، یکی دیگه انتخاب کن."
    db.set_display_name(uid, name)
    return True, None


def daily_login(uid, name):
    p = db.ensure_player(uid, name)
    if p["last_login"] == today():
        return {"already": True, "player": p}
    streak, broke = pr.update_streak(p["last_login"], today(), p["streak"])
    reward = pr.streak_reward(streak)
    db.save_player(uid, streak=streak, last_login=today(), coins=p["coins"] + reward)
    db.bump_mission(uid, today(), 0)
    p = db.get_player(uid)
    return {"already": False, "broke": broke, "coins_gained": reward,
            "streak": streak, "player": p, "mission": ms.mission_of_day(today())}


def record_game(uid, name, won, score, correct_answers=0):
    p = db.ensure_player(uid, name)
    xp_gain = 30 + score // 5 + (40 if won else 0)
    new_level, new_xp, levels_gained = pr.add_xp(p["level"], p["xp"], xp_gain)
    is_record = score > p["best_score"]
    coins_gain = sum(pr.levelup_reward(p["level"] + i + 1) for i in range(levels_gained))
    db.save_player(uid,
                   games=p["games"] + 1,
                   wins=p["wins"] + (1 if won else 0),
                   best_score=max(p["best_score"], score),
                   level=new_level, xp=new_xp,
                   coins=p["coins"] + coins_gain)
    db.bump_mission(uid, today(), 1)
    return {"levels_gained": levels_gained, "new_level": new_level,
            "is_record": is_record, "xp_gain": xp_gain, "coins_gain": coins_gain}


def profile_view(uid, name):
    p = db.ensure_player(uid, name)
    shown = (p["display_name"] or "").strip() or p["name"]
    data = dict(name=shown, level=p["level"], xp=p["xp"],
                xp_needed=pr.xp_needed(p["level"]), coins=p["coins"],
                streak=p["streak"], wins=p["wins"], games=p["games"],
                best=p["best_score"])
    card = cards.profile_card(data)
    st = db.suggestion_stats_for_user(uid)
    card += f"\n\n💡 کلمات تاییدشده: <b>{st['approved']}</b>"
    return card


def mission_view(uid):
    m = ms.mission_of_day(today())
    mp = db.get_mission_progress(uid, today())
    done = mp["progress"] >= m["goal"]
    status = "✅ کامل!" if done else f"{mp['progress']}/{m['goal']}"
    text = f"{m['text']}  ({status})\n🎁 جایزه: {m['coins']} سکه + {m['xp']} XP"
    return text, done, mp["claimed"]
