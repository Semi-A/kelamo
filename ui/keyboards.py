# -*- coding: utf-8 -*-
"""کیبوردهای اینلاین یکپارچه Kalemo."""
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
import config


def main_menu():
    return M([
        [B("🎮 ایجاد بازی", callback_data="m:play")],
        [B("🌳 باغچه", callback_data="m:garden"),
         B("💡 پیشنهاد کلمه", callback_data="m:suggest")],
        [B("👤 پروفایل", callback_data="m:profile"),
         B("🎯 ماموریت روزانه", callback_data="m:mission")],
        [B("🏆 لیدربورد", callback_data="m:lb"),
         B("🎁 جایزه روزانه", callback_data="m:daily")],
        [B("⚙ تنظیمات", callback_data="m:settings"),
         B("❓ راهنما", callback_data="m:help")],
    ])

def back_menu():
    return M([[B("🔙 منوی اصلی", callback_data="m:home")]])


def settings_menu():
    return M([
        [B("✏️ تغییر نام نمایشی", callback_data="m:rename")],
        [B("🔙 منوی اصلی", callback_data="m:home")],
    ])


def profile_menu():
    return M([
        [B("✏️ تغییر نام نمایشی", callback_data="m:rename")],
        [B("🔙 منوی اصلی", callback_data="m:home")],
    ])


def onboarding(step):
    if step < 3:
        return M([[B("بعدی ➡️", callback_data=f"ob:{step+1}")]])
    return M([[B("🎮 انتخاب نام و شروع", callback_data="ob:name")]])


def cancel_rename():
    return M([[B("🔙 بی‌خیال", callback_data="m:home")]])


def mission_claim(can_claim):
    rows = []
    if can_claim:
        rows.append([B("🎁 دریافت جایزه", callback_data="mission:claim")])
    rows.append([B("🔙 منوی اصلی", callback_data="m:home")])
    return M(rows)


def play_in_group():
    url = f"https://t.me/{config.BOT_USERNAME}?startgroup=true"
    return M([[B("➕ افزودن کلمو به گروه", url=url)],
             [B("🔙 منوی اصلی", callback_data="m:home")]])


# ---- پنل ادمین ----
def admin_panel():
    return M([
        [B("📊 آمار کلی", callback_data="a:stats")],
        [B("🪙 دادن سکه/XP", callback_data="a:give"),
         B("🔎 پروفایل کاربر", callback_data="a:find")],
        [B("📣 پیام همگانی", callback_data="a:bcast")],
        [B("🗂 مدیریت دسته/کلمه", callback_data="a:words")],
        [B("👥 ادمین‌های همکار", callback_data="a:admins")],
        [B("❌ بستن", callback_data="a:close")],
    ])


def admin_back():
    return M([[B("🔙 پنل ادمین", callback_data="a:home")]])


def admin_words_menu():
    return M([
        [B("📋 لیست دسته‌ها", callback_data="a:wlist")],
        [B("💡 پیشنهادها", callback_data="a:suggests")],
        [B("🔙 پنل ادمین", callback_data="a:home")],
    ])
def suggest_menu():
    return M([
        [B("💡 ثبت پیشنهاد", callback_data="m:suggest")],
        [B("🔙 منوی اصلی", callback_data="m:home")],
    ])
def admin_suggestion_kb(sid):
    return M([
        [
            B("✅ تایید", callback_data=f"a:sapp:{sid}"),
            B("❌ رد", callback_data=f"a:srej:{sid}")
        ],
        [
            B("✏️ ویرایش", callback_data=f"a:sedit:{sid}"),
            B("📂 تغییر دسته", callback_data=f"a:scat:{sid}")
        ],
        [
            B("➡ بعدی", callback_data="a:suggests"),
            B("🔙 پنل ادمین", callback_data="a:home")
        ],
    ])