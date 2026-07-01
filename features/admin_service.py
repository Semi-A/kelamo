# -*- coding: utf-8 -*-
"""سرویس ادمین: تشخیص دسترسی + عملیات مدیریتی."""
import config
from core import db, progression as pr

def is_admin(uid):
    return uid in config.ADMIN_IDS or db.is_db_admin(uid)

def is_owner(uid):
    """فقط ادمین‌های اصلی config می‌توانند ادمین همکار اضافه/حذف کنند."""
    return uid in config.ADMIN_IDS

def stats_text():
    s = db.stats()
    cats = db.list_categories()
    nwords = sum(c for _, c in cats)
    return (
        "📊 <b>آمار کلی کلمو</b>\n━━━━━━━━━━━━━━\n"
        f"👤 بازیکن‌ها: <b>{s['players']}</b>\n"
        f"🔥 فعال (استریک‌دار): <b>{s['active']}</b>\n"
        f"🎮 کل بازی‌ها: <b>{s['games']}</b>\n"
        f"🏆 کل بردها: <b>{s['wins']}</b>\n"
        f"🪙 سکه در گردش: <b>{s['coins']:,}</b>\n"
        f"🗂 دسته‌ها: <b>{len(cats)}</b> | کلمات: <b>{nwords}</b>"
    )

def give(target_uid, coins=0, xp=0):
    p = db.get_player(target_uid)
    if not p:
        return None
    fields = {}
    if coins:
        fields["coins"] = p["coins"] + coins
    if xp:
        lvl, newxp, _ = pr.add_xp(p["level"], p["xp"], xp)
        fields["level"] = lvl
        fields["xp"] = newxp
    if fields:
        db.save_player(target_uid, **fields)
    return db.get_player(target_uid)
