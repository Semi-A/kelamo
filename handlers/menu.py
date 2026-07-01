# -*- coding: utf-8 -*-
"""هندلرهای چت خصوصی: آنبوردینگ، انتخاب نام نمایشی، منو، پروفایل،
ماموریت، جایزه، لیدربورد، تنظیمات، تغییر نام."""
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from core import db, missions as ms
from features import player_service as svc
from ui import persona, cards, keyboards as kb, onboarding

HTML = ParseMode.HTML


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if ctx.args and ctx.args[0].startswith("nf_"):
        from handlers import namefamily_private as nf

        chat_id = nf.decode_start_param(ctx.args[0])
        if chat_id is not None:
            ok = await nf.start_from_private(ctx, chat_id, u)
            if ok:
                return await update.message.reply_text("✅ فرم اسم‌وفامیل برایت ارسال شد.")
            return await update.message.reply_text("⛔️ مسابقه فعال پیدا نشد.")
    if update.effective_chat.type != "private":
        return await update.message.reply_text(
            "🎮 برای شروع بازی توی گروه «شروع کلمو» بنویس یا /newgame بزن!")
    p, is_new = svc.register(u.id, u.first_name)
    if is_new or not db.is_onboarded(u.id):
        await update.message.reply_text(
            onboarding.step_text(1), parse_mode=HTML, reply_markup=kb.onboarding(1))
    else:
        await update.message.reply_text(
            persona.say("welcome"), reply_markup=kb.main_menu())


async def on_onboarding(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    step = q.data.split(":")[1]
    if step == "name":
        await q.answer()
        ctx.user_data["await_name"] = True
        return await q.message.edit_text(
            "✏️ <b>یه نام نمایشی انتخاب کن</b>\n━━━━━━━━━━━━━━\n"
            "این همون اسمیه که تو بازی‌ها و لیدربورد دیده می‌شه.\n"
            "<i>۳ تا ۲۰ کاراکتر، و باید یکتا باشه.</i>\n\n"
            "حالا اسمتو بفرست:",
            parse_mode=HTML, reply_markup=kb.cancel_rename())
    await q.answer()
    n = int(step)
    await q.message.edit_text(onboarding.step_text(n), parse_mode=HTML,
                              reply_markup=kb.onboarding(n))


async def on_name_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دریافت نام نمایشی در چت خصوصی. برمی‌گرداند True اگر مصرف شد."""
    if update.effective_chat.type != "private":
        return False
    if not ctx.user_data.get("await_name"):
        return False
    u = update.effective_user
    name = (update.message.text or "").strip()
    ok, msg = svc.set_name(u.id, u.first_name, name)
    if not ok:
        await update.message.reply_text("⚠️ " + msg)
        return True
    ctx.user_data.pop("await_name", None)
    db.mark_onboarded(u.id)
    await update.message.reply_text(
        f"✅ سلام <b>{name}</b>! نامت ثبت شد.\nبزن بریم بازی 🔥",
        parse_mode=HTML, reply_markup=kb.main_menu())
    return True


async def on_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":")[1]
    uid = q.from_user.id
    name = svc.display_name(uid, q.from_user.first_name)

    if action == "home":
        db.mark_onboarded(uid)
        ctx.user_data.pop("await_name", None)
        return await q.message.edit_text(persona.say("welcome"),
                                         reply_markup=kb.main_menu())

    if action == "profile":
        return await q.message.edit_text(svc.profile_view(uid, name),
                                         parse_mode=HTML, reply_markup=kb.profile_menu())

    if action == "settings":
        cur = db.get_display_name(uid) or "—"
        return await q.message.edit_text(
            "⚙ <b>تنظیمات</b>\n━━━━━━━━━━━━━━\n"
            f"نام نمایشی فعلی: <b>{cur}</b>",
            parse_mode=HTML, reply_markup=kb.settings_menu())

    if action == "rename":
        left = svc.name_cooldown_left(uid)
        if left > 0 and db.get_display_name(uid):
            days = left // 86400
            hours = (left % 86400) // 3600
            when = f"{days} روز و {hours} ساعت" if days else f"{hours} ساعت"
            return await q.answer(f"تا تغییر بعدی {when} مونده.", show_alert=True)
        ctx.user_data["await_name"] = True
        return await q.message.edit_text(
            "✏️ <b>نام نمایشی جدید</b>\n━━━━━━━━━━━━━━\n"
            "<i>۳ تا ۲۰ کاراکتر و یکتا.</i>\n\nاسم جدیدتو بفرست:",
            parse_mode=HTML, reply_markup=kb.cancel_rename())

    if action == "mission":
        text, done, claimed = svc.mission_view(uid)
        head = "🎯 <b>مأموریت امروز</b>\n━━━━━━━━━━━━━━\n"
        if claimed:
            text += "\n\n<i>جایزه‌شو گرفتی! فردا یکی جدید 😉</i>"
        return await q.message.edit_text(head + text, parse_mode=HTML,
                                         reply_markup=kb.mission_claim(done and not claimed))

    if action == "daily":
        r = svc.daily_login(uid, name)
        if r["already"]:
            return await q.answer("امروز جایزه‌تو گرفتی! فردا بیا 😉", show_alert=True)
        m = r["mission"]
        card = cards.daily_card(r["coins_gained"], r["streak"], m["text"])
        if r.get("broke"):
            card = persona.say("streak_break") + "\n\n" + card
        return await q.message.edit_text(card, parse_mode=HTML, reply_markup=kb.back_menu())

    if action == "lb":
        rows = db.top_players(10)
        return await q.message.edit_text(cards.leaderboard_card("لیدربورد", rows),
                                         parse_mode=HTML, reply_markup=kb.back_menu())

    if action == "help":
        txt = ("❓ <b>راهنمای کلمو</b>\n━━━━━━━━━━━━━━\n"
               "🎮 منو رو به یه گروه اضافه کن و اونجا «شروع کلمو» بنویس یا /newgame بزن.\n"
               "🎲 پنج مود: کلاسیک، جای خالی، اسم‌وفامیل، قوانین متغیر و سرنخ.\n"
               "👤 پروفایل: سطح، سکه، نام نمایشی و رکوردهات.\n"
               "🎯 هر روز یه مأموریت تازه و جایزه‌ی ورود.\n"
               "🔥 هر روز سر بزن تا استریکت نپره!")
        return await q.message.edit_text(txt, parse_mode=HTML, reply_markup=kb.play_in_group())

    if action == "garden":
        from handlers import garden as garden_handlers
        return await garden_handlers.open_garden(update, ctx)

    if action == "suggest":
        from handlers import suggestions
        return await suggestions.start_suggest(update, ctx)

    if action == "garden":
        from handlers import garden as garden_handlers
        return await garden_handlers.open_garden(update, ctx)

    if action == "play":
        return await q.message.edit_text(
            "🎮 بازی توی گروه انجام می‌شه! منو به گروهت اضافه کن و اونجا «شروع کلمو» بنویس 🔥",
            reply_markup=kb.play_in_group())


async def on_mission_claim(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    text, done, claimed = svc.mission_view(uid)
    if not done or claimed:
        return await q.answer("هنوز کامل نشده یا قبلاً گرفتی!", show_alert=True)
    m = ms.mission_of_day(svc.today())
    granted = db.claim_mission_atomic(uid, svc.today(), m["coins"], m["xp"])
    if not granted:
        return await q.answer("قبلاً این جایزه رو گرفتی!", show_alert=True)
    await q.answer(f"🎉 +{m['coins']} سکه گرفتی!", show_alert=True)
    await q.message.edit_text(
        persona.say("mission_done", reward=f"{m['coins']} سکه + {m['xp']} XP"),
        reply_markup=kb.back_menu())