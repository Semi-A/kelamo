# -*- coding: utf-8 -*-
"""مسیریاب پیام‌های متنی: نام نمایشی/پیشنهاد کلمه/ادمین (خصوصی) یا شروع/بازی (گروه)."""
from telegram import Update
from telegram.ext import ContextTypes
from handlers import admin, lobby, menu, suggestions, namefamily_private
from game import session as sess

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    text = update.message.text if update.message else ""

    if chat.type == "private":
        # اول: پاسخ‌های در حال ثبت اسم‌وفامیل
        if await namefamily_private.on_nf_text(update, ctx):
            return
        # سپس: دریافت نام نمایشی (آنبوردینگ/تغییر نام)
        if await menu.on_name_text(update, ctx):
            return
        # سپس: پیشنهاد کلمه
        if await suggestions.on_suggest_text(update, ctx):
            return
        # سپس: ورودی‌های ادمین
        if await admin.on_admin_text(update, ctx):
            return
        if await admin.on_words_text(update, ctx):
            return
        return

    # گروه: «شروع کلمو» / «شروع بازی» → باز کردن لابی
        # گروه
    if lobby.is_start_text(text):
        if sess.exists(chat.id):
            # بازی فعاله → مثل /newgame تکراری رفتار کن (حذف + اخطار)
            return await lobby.handle_start_during_game(update, ctx)
        return await lobby.open_lobby(update, ctx)
    await lobby.on_group_text(update, ctx)


