"""سرویس پروفایل: نام نمایشی یکتا، اعتبارسنجی، محدودیت تغییر نام (۷ روز)."""
import re, time
from core import db, progression as pr
from ui import cards

NAME_MIN, NAME_MAX = 3, 20
RENAME_COOLDOWN = 7 * 24 * 3600   # ۷ روز
_valid = re.compile(r"^[\w\u0600-\u06FF\u200c ]{3,20}$", re.UNICODE)

def validate_name(name):
    """برمی‌گرداند (ok, normalized_or_error)."""
    n = (name or "").strip()
    if len(n) < NAME_MIN:
        return False, f"نام باید حداقل {NAME_MIN} کاراکتر باشد."
    if len(n) > NAME_MAX:
        return False, f"نام باید حداکثر {NAME_MAX} کاراکتر باشد."
    if not _valid.match(n):
        return False, "فقط حروف، عدد، فاصله و نیم‌فاصله مجاز است."
    if db.name_taken(n):
        return False, "این نام قبلاً گرفته شده. یکی دیگه انتخاب کن."
    return True, n

def has_name(uid):
    p = db.get_profile(uid)
    return bool(p and p["display_name"])

def get_name(uid, fallback=""):
    return db.display_name(uid, fallback)

def can_rename(uid):
    """برمی‌گرداند (ok, seconds_left)."""
    p = db.get_profile(uid)
    if not p or not p["name_changed_at"]:
        return True, 0
    elapsed = int(time.time()) - p["name_changed_at"]
    left = RENAME_COOLDOWN - elapsed
    return (left <= 0), max(0, left)

def set_name(uid, name):
    ok, res = validate_name(name)
    if not ok:
        return False, res
    db.set_display_name(uid, res)
    return True, res

def profile_view(uid, fallback=""):
    p = db.ensure_player(uid, fallback)
    name = get_name(uid, fallback)
    data = dict(name=name, level=p["level"], xp=p["xp"],
                xp_needed=pr.xp_needed(p["level"]), coins=p["coins"],
                streak=p["streak"], wins=p["wins"], games=p["games"],
                best=p["best_score"])
    return cards.profile_card(data)