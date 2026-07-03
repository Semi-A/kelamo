# -*- coding: utf-8 -*-
"""پنل‌های متنی + کیبوردهای بازی گروهی کلمو (همه با EditMessage)."""
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
from game.modes import MODE_ORDER, mode_meta
from game.rules import REGISTRY
from game.session import TIME_OPTIONS, DIFFICULTY_OPTIONS, time_label, difficulty_label
from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M
from core import db
DIV = "────────────────"


# ---- پنل اصلی لابی ----
def lobby_text(s):
    meta = mode_meta(s.mode_id)
    ready = "آماده‌ی شروع ✅" if s.count() >= 2 else "منتظر بازیکن… (حداقل ۲ نفر)"
    return (
        "🎮 <b>ایجاد مسابقه کلمو</b>\n"
        f"{DIV}\n"
        f"🎲 مود: <b>{meta['emoji']} {meta['name']}</b>\n"
        f"<i>{meta['desc']}</i>\n\n"
        f"⏱ زمان: <b>{time_label(s.time_limit)}</b>\n"
        f"👑 سازنده: <b>{s.host_name}</b>\n\n"
        f"👥 بازیکنان: <b>{s.count()} نفر</b>\n"
        f"{s.player_lines()}\n\n"
        f"📜 قوانین:\n{s.ruleset.describe()}\n"
        f"{DIV}\n"
        f"وضعیت: <b>{ready}</b>"
    )


def lobby_kb(s):
    rows = [
        [B("🎲 انتخاب مود", callback_data="k:mode"),
         B("⏱ زمان", callback_data="k:time")],
    ]
    if s.mode_id == "classic_choice":
        rows.append([B("📂 انتخاب دسته", callback_data="k:cat")])
    rows += [
        [B("⚙ قوانین", callback_data="k:rules")],
        [B(f"👥 عضویت ({s.count()})", callback_data="k:join")],
        [B("▶ شروع بازی", callback_data="k:start"),
         B("❌ لغو", callback_data="k:cancel")],
    ]
    return M(rows)

# ---- انتخاب مود ----
def mode_text():
    return "🎲 <b>انتخاب مود بازی</b>\n" + DIV + "\nیکی رو انتخاب کن:"


def mode_kb(current):
    rows = []
    for mid in MODE_ORDER:
        meta = mode_meta(mid)
        mark = "◉" if mid == current else "◯"
        rows.append([B(f"{mark} {meta['emoji']} {meta['name']}",
                       callback_data=f"k:setmode:{mid}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- انتخاب زمان ----
def time_text(s):
    return ("⏱ <b>زمان مسابقه</b>\n" + DIV +
            "\nمسابقه بعد از پایان این زمان خودکار تموم می‌شه.")


def time_kb(s):
    rows = []
    for sec, lbl in TIME_OPTIONS:
        mark = "◉" if sec == s.time_limit else "◯"
        rows.append([B(f"{mark} {lbl}", callback_data=f"k:settime:{sec}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- سختی (جای خالی) ----
def difficulty_kb(s):
    rows = []
    for key, lbl in DIFFICULTY_OPTIONS:
        mark = "◉" if key == s.difficulty else "◯"
        rows.append([B(f"{mark} {lbl}", callback_data=f"k:setdiff:{key}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- قوانین ----
TOGGLEABLE = ["min_len", "max_len", "exact_len", "starts_with", "ends_with",
              "must_contain", "must_not_contain", "time_limit", "bonus", "no_disturb"]

def rules_text():
    return ("⚙ <b>قوانین بازی</b>\n" + DIV +
            "\nقوانین دلخواه رو روشن/خاموش کن.\n"
            "<i>(در مود «قوانین متغیر» قوانین هر دور خودکار انتخاب می‌شن.)</i>")


def rules_kb(s):
    rows = []
    for rid in TOGGLEABLE:
        cls = REGISTRY[rid]
        on = s.ruleset.is_active(rid)
        mark = "✅" if on else "▫️"
        rows.append([B(f"{mark} {cls.label}", callback_data=f"k:toggle:{rid}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- نمایش زنده‌ی بازی ----
def live_text(s):
    meta = mode_meta(s.mode_id)
    leader = s.leader()
    leader_line = (f"🥇 صدرنشین: <b>{leader[0]}</b> — {leader[1]} امتیاز"
                   if leader else "🥇 صدرنشین: —")
    cat_line = (
        f"📂 دسته: <b>{s.category}</b>\n"
        if s.category and s.mode_id in ("classic_random", "classic_choice", "variable")
        else ""
    )

    rules_line = ""
    if s.ruleset.rules and s.mode_id in (
        "classic_random",
        "classic_choice",
        "blank",
    ):

        rules_line = f"📜 قوانین:\n{s.ruleset.describe()}\n"
    return (
        f"🎮 <b>مسابقه‌ی کلمو — {meta['emoji']} {meta['name']}</b>\n"
        f"{DIV}\n"
        f"{cat_line}{rules_line}"
        f"👥 بازیکنان: <b>{s.count()}</b>\n"
        f"⏱ باقی‌مانده: <b>{s.remaining_label()}</b>\n"
        f"{leader_line}\n"
        f"{DIV}\n"
        f"<b>{prompt_of(s)}</b>"
    )


def prompt_of(s):
    if s.question and "prompt" in s.question:
        return s.question["prompt"]
    return "در حال آماده‌سازی…"


def running_kb(s=None):
    return M([[B("🏁 پایان مسابقه", callback_data="k:end")]])


# ---- تنظیمات گروه (حالت تمرکز) ----
def settings_text(s):
    state = "روشن ✅" if s.focus_mode else "خاموش ▫️"
    return ("🧹 <b>تنظیمات مسابقه</b>\n" + DIV +
            f"\nحالت تمرکز: <b>{state}</b>\n"
            "<i>وقتی روشنه، پیام‌های نامرتبط هنگام مسابقه پاک می‌شن.</i>")


def settings_kb(s):
    label = "🧹 خاموش‌کردن حالت تمرکز" if s.focus_mode else "🧹 روشن‌کردن حالت تمرکز"
    return M([[B(label, callback_data="k:focus")],
             [B("🔙 برگشت", callback_data="k:back")]])


def category_text():
    return (
        "📂 <b>انتخاب دسته</b>\n"
        + DIV +
        "\nیک دسته برای مسابقه انتخاب کن."
    )

def category_kb(categories, current=None, page=0, per_page=8):
    total = len(categories)
    start = page * per_page
    shown = categories[start:start + per_page]

    rows = []

    for cat, cnt in shown:
        mark = "◉" if cat == current else "◯"
        rows.append([
            B(
                f"{mark} {cat} ({cnt})",
                callback_data=f"k:setcat:{page}:{cat}"
            )
        ])

    nav = []

    if page > 0:
        nav.append(
            B("⬅ قبلی", callback_data=f"k:catpage:{page-1}")
        )

    if start + per_page < total:
        nav.append(
            B("بعدی ➡", callback_data=f"k:catpage:{page+1}")
        )

    if nav:
        rows.append(nav)

    rows.append([
        B("🔙 برگشت", callback_data="k:back")
    ])

    return M(rows)

def answer_ok_text(score, found, total):
    total = max(1, int(total or 0))
    found = max(0, min(int(found or 0), total))

    pct = round(found * 100 / total)

    filled = round(pct / 100 * 16)

    bar = (
        "█" * filled +
        "░" * (16 - filled)
    )

    return (
        f"✅ درست\n"
        f"⭐ امتیاز: {score}\n\n"
        f"{bar} {pct}%\n\n"
        f"{found} / {total}"
    )


def finish_text(s, reason=None):
    import html
    import time

    ranking = s.ranking()

    duration = 0
    if s.started_at:
        duration = max(
            0,
            int(time.time() - s.started_at)
        )

    m, sec = divmod(duration, 60)

    lines = [
        "🏆 <b>رتبه‌بندی</b>",
        DIV,
    ]

    if not ranking:
        lines.append("امتیازی ثبت نشد.")

    for i, (uid, info) in enumerate(ranking, 1):
        name = html.escape(
            info.get("name", "بازیکن")
        )

        score = int(info.get("score", 0))
        ok = int(s.correct_by_user.get(uid, 0))
        bad = int(s.wrong_by_user.get(uid, 0))

        lines.append(f"{i}. <b>{name}</b>")
        lines.append(f"⭐ امتیاز: {score}")
        lines.append(f"📊 درست: {ok}   ❌ اشتباه: {bad}")

    lines += [
        DIV,
        f"⏱ مدت مسابقه: <b>{m:02d}:{sec:02d}</b>",
        f"📂 دسته مسابقه: <b>{html.escape(s.category or '—')}</b>",
    ]

    return "\n".join(lines)

def namefamily_category_text():
    return (
        "✍️ <b>انتخاب دسته‌های اسم‌وفامیل</b>\n"
        + DIV +
        "\n\n"
        "هر تعداد دسته خواستی انتخاب کن.\n"
        "اگر هیچ دسته‌ای انتخاب نکنی، ربات ۶ دسته تصادفی انتخاب می‌کند."
    )

def namefamily_category_kb(s):
    cats = db.list_categories()
    rows = []
    for cat, cnt in cats:
        mark = "✅" if cat in s.namefamily_categories else "⬜"
        rows.append([
            B(f"{mark} {cat}", callback_data=f"k:nftoggle:{cat}")
        ])

    rows.append([B("🎲 انتخاب تصادفی", callback_data="k:nfrandom")])
    rows.append([B("✅ تایید", callback_data="k:nfdone")])
    rows.append([B("◀ بازگشت", callback_data="k:back")])

    return M(rows)