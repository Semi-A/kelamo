# -*- coding: utf-8 -*-
"""پنل ادمین Kalemo (کلمو) (فقط چت خصوصی).
دکمه‌ها وضعیت ورودی چندمرحله‌ای را در ctx.user_data['await'] می‌گذارند؛
پیام بعدی ادمین به آن عمل اختصاص می‌یابد."""
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest
import config
from core import db
from features import admin_service as adm, player_service as svc
from ui import keyboards as kb

HTML = ParseMode.HTML

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if update.effective_chat.type != "private":
        return await update.message.reply_text("پنل ادمین فقط توی چت خصوصی ربات کار می‌کنه.")
    if not adm.is_admin(u.id):
        return await update.message.reply_text("⛔️ این بخش فقط مخصوص ادمین‌هاست.")
    ctx.user_data.pop("await", None)
    await update.message.reply_text(
        "🛠 <b>پنل مدیریت کلمو</b>\n━━━━━━━━━━━━━━\nیه گزینه رو انتخاب کن:",
        parse_mode=HTML, reply_markup=kb.admin_panel())

async def on_admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if not adm.is_admin(uid):
        return await q.answer("⛔️ دسترسی نداری.", show_alert=True)
    await q.answer()
    parts = q.data.split(":")
    action = parts[1] if len(parts) > 1 else "home"
    ctx.user_data.pop("await", None)

    if action in ("home",):
        return await q.message.edit_text(
            "🛠 <b>پنل مدیریت کلمو</b>\n━━━━━━━━━━━━━━\nیه گزینه رو انتخاب کن:",
            parse_mode=HTML, reply_markup=kb.admin_panel())

    if action == "close":
        return await q.message.edit_text("پنل بسته شد. هر وقت خواستی /admin بزن.")

    if action == "stats":
        return await q.message.edit_text(adm.stats_text(), parse_mode=HTML,
                                         reply_markup=kb.admin_back())

    if action == "give":
        ctx.user_data["await"] = "give"
        return await q.message.edit_text(
            "🪙 <b>دادن سکه/XP</b>\n━━━━━━━━━━━━━━\n"
            "بفرست به این شکل:\n<code>آیدی سکه xp</code>\n"
            "مثال: <code>1053046454 100 50</code>\n(xp اختیاریه)",
            parse_mode=HTML, reply_markup=kb.admin_back())

    if action == "find":
        ctx.user_data["await"] = "find"
        return await q.message.edit_text(
            "🔎 <b>پروفایل کاربر</b>\n━━━━━━━━━━━━━━\nآیدی عددی کاربر رو بفرست.",
            parse_mode=HTML, reply_markup=kb.admin_back())

    if action == "bcast":
        ctx.user_data["await"] = "bcast"
        return await q.message.edit_text(
            "📣 <b>پیام همگانی</b>\n━━━━━━━━━━━━━━\n"
            "متن پیامی که می‌خوای به همه کاربرا بره رو بفرست.",
            parse_mode=HTML, reply_markup=kb.admin_back())

    if action == "words":
        return await q.message.edit_text(
            "🗂 <b>مدیریت دسته و کلمه</b>\n━━━━━━━━━━━━━━\n"
            "دستورها رو همینجا بفرست:\n"
            "• <code>افزودن دسته اسم</code>\n"
            "• <code>حذف دسته اسم</code>\n"
            "• <code>افزودن کلمه دسته کلمه</code>\n"
            "• <code>حذف کلمه دسته کلمه</code>\n"
            "یا «لیست دسته‌ها» رو بزن.",
            parse_mode=HTML, reply_markup=kb.admin_words_menu())

    if action == "wlist":
        cats = db.list_categories()
        if not cats:
            body = "هیچ دسته‌ای نیست."
        else:
            body = "\n".join(f"📂 <b>{n}</b> — {c} کلمه" for n, c in cats)
        return await q.message.edit_text(
            "🗂 <b>دسته‌ها</b>\n━━━━━━━━━━━━━━\n" + body,
            parse_mode=HTML, reply_markup=kb.admin_words_menu())

    if action == "suggests":
        rows = db.pending_suggestions(1)

        if not rows:
            return await q.message.edit_text(
                "💡 پیشنهادی در صف بررسی نیست.",
                reply_markup=kb.admin_back()
            )

        sug = rows[0]

        text = (
            "💡 <b>پیشنهاد کلمه</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"کلمه: <b>{sug['word']}</b>\n"
            f"دسته: <b>{sug['category']}</b>\n"
            f"توضیح: {sug.get('description') or '—'}"
        )

        return await q.message.edit_text(
            text,
            parse_mode=HTML,
            reply_markup=kb.admin_suggestion_kb(sug["id"])
        )

    if action == "sapp":
        sid = int(parts[2])
        ok, msg = db.approve_suggestion(sid, uid)

        rows = db.pending_suggestions(1)

        if rows:
            sug = rows[0]
            text = (
                "💡 <b>پیشنهاد کلمه</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"کلمه: <b>{sug['word']}</b>\n"
                f"دسته: <b>{sug['category']}</b>\n"
                f"توضیح: {sug.get('description') or '—'}"
            )

            return await q.message.edit_text(
                "✅ " + msg + "\n\n" + text,
                parse_mode=HTML,
                reply_markup=kb.admin_suggestion_kb(sug["id"])
            )

        return await q.message.edit_text(
            "✅ " + msg + "\n\nدیگه پیشنهادی باقی نمونده.",
            parse_mode=HTML,
            reply_markup=kb.admin_back()
        )


    if action == "srej":
        sid = int(parts[2])

        db.reject_suggestion(sid, uid)

        rows = db.pending_suggestions(1)

        if rows:
            sug = rows[0]
            text = (
                "💡 <b>پیشنهاد کلمه</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"کلمه: <b>{sug['word']}</b>\n"
                f"دسته: <b>{sug['category']}</b>\n"
                f"توضیح: {sug.get('description') or '—'}"
            )

            return await q.message.edit_text(
                "❌ پیشنهاد رد شد.\n\n" + text,
                parse_mode=HTML,
                reply_markup=kb.admin_suggestion_kb(sug["id"])
            )

        return await q.message.edit_text(
            "❌ پیشنهاد رد شد.\n\nدیگه پیشنهادی باقی نمونده.",
            parse_mode=HTML,
            reply_markup=kb.admin_back()
        )


    if action == "admins":
        if not adm.is_owner(uid):
            return await q.message.edit_text(
                "👥 فقط ادمین اصلی می‌تونه ادمین همکار اضافه/حذف کنه.",
                reply_markup=kb.admin_back())
        ctx.user_data["await"] = "admins"
        lst = db.list_admins()
        cur = "، ".join(str(x) for x in lst) if lst else "—"
        return await q.message.edit_text(
            "👥 <b>ادمین‌های همکار</b>\n━━━━━━━━━━━━━━\n"
            f"فعلی: {cur}\n\n"
            "برای افزودن: <code>+ آیدی</code>\n"
            "برای حذف: <code>- آیدی</code>",
            parse_mode=HTML, reply_markup=kb.admin_back())

async def on_admin_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """پیام متنی ادمین در چت خصوصی را بر اساس وضعیت انتظار پردازش می‌کند.
    برمی‌گرداند True اگر پیام مصرف شد."""
    uid = update.effective_user.id
    if update.effective_chat.type != "private" or not adm.is_admin(uid):
        return False
    mode = ctx.user_data.get("await")
    if not mode:
        return False
    text = (update.message.text or "").strip()

    if mode == "give":
        parts = text.split()
        if len(parts) < 2 or not parts[0].isdigit():
            await update.message.reply_text("فرمت اشتباهه. مثال: 1053046454 100 50")
            return True
        target = int(parts[0]); coins = int(parts[1]) if parts[1].lstrip('-').isdigit() else 0
        xp = int(parts[2]) if len(parts) > 2 and parts[2].lstrip('-').isdigit() else 0
        p = adm.give(target, coins, xp)
        if not p:
            await update.message.reply_text("⛔️ کاربری با این آیدی پیدا نشد.")
        else:
            await update.message.reply_text(
                f"✅ انجام شد!\n{p['name']} → سکه: {p['coins']:,} | سطح: {p['level']} | XP: {p['xp']}")
        ctx.user_data.pop("await", None)
        return True

    if mode == "find":
        if not text.isdigit():
            await update.message.reply_text("آیدی باید عددی باشه.")
            return True
        p = db.get_player(int(text))
        if not p:
            await update.message.reply_text("⛔️ پیدا نشد.")
        else:
            await update.message.reply_text(svc.profile_view(int(text), p["name"]),
                                            parse_mode=HTML)
        ctx.user_data.pop("await", None)
        return True

    if mode == "bcast":
        ids = db.all_player_ids()
        sent = 0
        await update.message.reply_text(f"📤 در حال ارسال به {len(ids)} نفر...")
        for pid in ids:
            try:
                await ctx.bot.send_message(pid, text)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await update.message.reply_text(f"✅ به {sent} نفر ارسال شد.")
        ctx.user_data.pop("await", None)
        return True

    if mode == "admins":
        if not adm.is_owner(uid):
            ctx.user_data.pop("await", None)
            return True
        if text.startswith("+"):
            num = text[1:].strip()
            if num.isdigit():
                db.add_admin(int(num), uid)
                await update.message.reply_text(f"✅ {num} ادمین همکار شد.")
            else:
                await update.message.reply_text("آیدی نامعتبر.")
        elif text.startswith("-"):
            num = text[1:].strip()
            if num.isdigit() and db.del_admin(int(num)):
                await update.message.reply_text(f"🗑 {num} حذف شد.")
            else:
                await update.message.reply_text("پیدا نشد.")
        else:
            await update.message.reply_text("با + یا - شروع کن. مثال: + 123456")
        return True

    return False

async def on_words_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """مدیریت دسته/کلمه با دستورهای فارسی. برمی‌گرداند True اگر مصرف شد."""
    uid = update.effective_user.id
    if update.effective_chat.type != "private" or not adm.is_admin(uid):
        return False
    text = (update.message.text or "").strip()
    parts = text.split()
    if len(parts) < 3:
        return False
    act, kind = parts[0], parts[1]
    if act not in ("افزودن", "حذف") or kind not in ("دسته", "کلمه"):
        return False

    if kind == "دسته":
        name = " ".join(parts[2:])
        if act == "افزودن":
            ok = db.add_category(name)
            await update.message.reply_text("✅ دسته اضافه شد." if ok else "قبلاً هست/نامعتبر.")
        else:
            ok = db.del_category(name)
            await update.message.reply_text("🗑 حذف شد." if ok else "پیدا نشد.")
        return True

    if kind == "کلمه":
        if len(parts) < 4:
            await update.message.reply_text("فرمت: افزودن کلمه <دسته> <کلمه>")
            return True
        cat = parts[2]; word = " ".join(parts[3:])
        if act == "افزودن":
            ok = db.add_word(cat, word)
            await update.message.reply_text(f"✅ «{word}» به «{cat}» اضافه شد." if ok else "تکراری/نامعتبر.")
        else:
            ok = db.del_word(cat, word)
            await update.message.reply_text("🗑 حذف شد." if ok else "پیدا نشد.")
        return True
    return False
