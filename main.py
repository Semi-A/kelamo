# -*- coding: utf-8 -*-
"""نقطه‌ی ورود ربات کلمو (Kalemo).
ساخته‌شده با python-telegram-bot v21+ — کاملاً async، Application Builder،
CallbackQueryHandler برای همه‌ی دکمه‌ها، و Menu Button تلگرام.

اجرا:
    export KALEMO_BOT_TOKEN="123:ABC"
    export KALEMO_BOT_USERNAME="KalemoBot"
    export KALEMO_ADMINS="1053046454"
    python main.py
"""
import logging

from telegram import (
    Update, BotCommand, BotCommandScopeAllPrivateChats,
    MenuButtonCommands,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, Defaults, filters,
)

import config
from core import db
from handlers import menu, admin, lobby, router, namefamily_private, garden

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kalemo")


# ---------- Menu Button + Commands ----------
async def _post_init(app: Application):
    """ثبت دستورها و فعال‌سازی Menu Button تلگرام (UI بدون یادگیری دستور)."""
    private_cmds = [
        BotCommand("start", "🎮 ایجاد بازی / منوی اصلی"),
        BotCommand("play", "▶ ادامه/شروع بازی"),
        BotCommand("profile", "👤 پروفایل"),
        BotCommand("garden", "🌳 باغچه"),
        BotCommand("leaderboard", "🏆 لیدربورد"),
        BotCommand("settings", "⚙ تنظیمات"),
        BotCommand("help", "❓ راهنما"),
    ]
    await app.bot.set_my_commands(
        private_cmds, scope=BotCommandScopeAllPrivateChats())
    # دکمه‌ی منوی تلگرام → فهرست دستورها
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    log.info("Menu button & commands registered.")


# ---------- میان‌برهای منو از طریق دستور ----------
async def cmd_menu_shortcut(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دستورهای منو در چت خصوصی را به همان نمای دکمه‌ای می‌رساند."""
    if update.effective_chat.type != "private":
        return await update.message.reply_text(
            "🎮 برای بازی تو گروه «شروع کلمو» بنویس یا /newgame بزن!")
    from features import player_service as svc
    from ui import keyboards as kb, persona, cards
    uid = update.effective_user.id
    name = svc.display_name(uid, update.effective_user.first_name)
    svc.register(uid, update.effective_user.first_name)
    cmd = (update.message.text or "/").split()[0].lstrip("/").split("@")[0]

    if cmd == "profile":
        return await update.message.reply_text(
            svc.profile_view(uid, name), parse_mode=ParseMode.HTML,
            reply_markup=kb.profile_menu())
    if cmd == "leaderboard":
        rows = db.top_players(10)
        return await update.message.reply_text(
            cards.leaderboard_card("لیدربورد", rows), parse_mode=ParseMode.HTML,
            reply_markup=kb.back_menu())
    if cmd == "settings":
        cur = db.get_display_name(uid) or "—"
        return await update.message.reply_text(
            "⚙ <b>تنظیمات</b>\n━━━━━━━━━━━━━━\nنام نمایشی فعلی: <b>%s</b>" % cur,
            parse_mode=ParseMode.HTML, reply_markup=kb.settings_menu())
    if cmd == "help":
        return await update.message.reply_text(
            "❓ منو رو به گروه اضافه کن و «شروع کلمو» بنویس 🔥",
            reply_markup=kb.play_in_group())
    # play / default
    return await update.message.reply_text(
        persona.say("welcome"), reply_markup=kb.main_menu())


def build_app() -> Application:
    db.init()
    defaults = Defaults(parse_mode=None)
    app = (ApplicationBuilder()
           .token(config.BOT_TOKEN)
           .defaults(defaults)
           .post_init(_post_init)
           .build())

    # دستورهای پایه
    app.add_handler(CommandHandler("start", menu.cmd_start))
    app.add_handler(CommandHandler(["play", "profile", "leaderboard", "settings", "help"],
                                   cmd_menu_shortcut))
    app.add_handler(CommandHandler("admin", admin.cmd_admin))
    app.add_handler(CommandHandler("garden", garden.cmd_garden))

    # بازی گروهی
    app.add_handler(CommandHandler("newgame", lobby.cmd_newgame))
    app.add_handler(CommandHandler("endgame", lobby.cmd_endgame))
    app.add_handler(CommandHandler("gsettings", lobby.cmd_settings))

    # CallbackQueryها — همه دکمه‌محور
    app.add_handler(CallbackQueryHandler(menu.on_onboarding, pattern=r"^ob:"))
    app.add_handler(CallbackQueryHandler(menu.on_mission_claim, pattern=r"^mission:"))
    app.add_handler(CallbackQueryHandler(namefamily_private.on_nf_cb, pattern=r"^nf:"))
    app.add_handler(CallbackQueryHandler(garden.on_cb, pattern=r"^g:"))
    app.add_handler(CallbackQueryHandler(menu.on_menu, pattern=r"^m:"))
    app.add_handler(CallbackQueryHandler(admin.on_admin_cb, pattern=r"^a:"))
    app.add_handler(CallbackQueryHandler(lobby.on_cb, pattern=r"^k:"))

    # پیام‌های متنی → مسیریاب
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router.on_text))

    return app


def main():
    if not config.BOT_TOKEN or config.BOT_TOKEN.startswith("PUT-YOUR"):
        raise SystemExit("⛔️ KALEMO_BOT_TOKEN ست نشده. متغیر محیطی رو تنظیم کن.")
    app = build_app()
    log.info("Kalemo is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
