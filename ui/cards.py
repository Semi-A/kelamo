# -*- coding: utf-8 -*-
"""کارت‌های گرافیکی متنی (HTML) برای تلگرام."""
DIV = "━━━━━━━━━━━━━━"

def _bar(value, maximum, width=10):
    if maximum <= 0:
        maximum = 1
    filled = int(round(width * min(value, maximum) / maximum))
    return "▰" * filled + "▱" * (width - filled)

def profile_card(p):
    pct_bar = _bar(p["xp"], p["xp_needed"])
    winrate = int(100 * p["wins"] / p["games"]) if p["games"] else 0
    return (
        f"<b>🪪 پروفایل {p['name']}</b>\n{DIV}\n"
        f"🏅 <b>سطح {p['level']}</b>\n"
        f"⚡️ XP: {pct_bar}  <code>{p['xp']}/{p['xp_needed']}</code>\n{DIV}\n"
        f"🪙 سکه: <b>{p['coins']:,}</b>\n"
        f"🔥 استریک: <b>{p['streak']} روز</b>\n{DIV}\n"
        f"🎮 بازی‌ها: <b>{p['games']}</b>\n"
        f"🏆 بردها: <b>{p['wins']}</b>  (<b>{winrate}%</b>)\n"
        f"⭐️ بهترین امتیاز: <b>{p['best']}</b>"
    )

def levelup_card(level, reward_coins):
    return (
        f"<b>🎚 لِوِل آپ!</b>\n{DIV}\n"
        f"رسیدی به <b>سطح {level}</b> 🎉\n"
        f"جایزه: <b>+{reward_coins} سکه</b> 🪙\n{DIV}\n"
        f"<i>یه پله بالاتر، یه ذره خفن‌تر 🔝</i>"
    )

def game_over_card(title, rows, ad=None):
    lines = "\n".join(f"{r[0]} <b>{r[1]}</b> — {r[2]} امتیاز" for r in rows)
    card = f"<b>🏁 {title}</b>\n{DIV}\n{lines}\n{DIV}"
    if ad:
        card += f"\n\n📣 <i>{ad}</i>"
    return card

def leaderboard_card(title, rows):
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, score) in enumerate(rows):
        badge = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{badge} <b>{name}</b> — {score}")
    return f"<b>🏆 {title}</b>\n{DIV}\n" + ("\n".join(lines) if lines else "هنوز کسی نیست!")

def daily_card(coins, streak, mission_text):
    return (
        f"<b>🎁 جایزه روزانه</b>\n{DIV}\n"
        f"🪙 <b>+{coins} سکه</b>\n"
        f"🔥 استریک: <b>{streak} روز</b>\n{DIV}\n"
        f"<b>🎯 مأموریت امروز:</b>\n{mission_text}"
    )
