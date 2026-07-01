# -*- coding: utf-8 -*-
"""هندلر بازی گروهی کلمو: شروع با متن یا دستور، لابی، مود، قوانین، زمان،
عضویت، شمارش معکوس، آموزش مود، اجرا، نمایش زنده، اتمام خودکار، حالت تمرکز.
همه با EditMessage.

اسم‌وفامیل: پاسخ‌ها فقط در PV ثبت می‌شوند (handlers/namefamily_private.py).
پایان مسابقه: گزارش مسابقه ثبت می‌شود و احتمال Lucky Box برای بازیکنان بررسی می‌شود.
"""
import asyncio
import re
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from core import db
from features import player_service as svc
from game import session as sess
from ui import panels, persona
import html
import time

HTML = ParseMode.HTML

import time
from datetime import timedelta
from telegram import ChatPermissions

MAX_WARNS = 3                 # سقف اخطار قبل از سکوت
MUTE_SECONDS = 5 * 60         # مدت سکوت: ۵ دقیقه
FOCUS_WORD_LIMIT = 3          # بیش از این تعداد کلمه = پیام جمله‌ای

def _arg(parts, i):
    try:
        return parts[i]
    except IndexError:
        return None

# عبارت‌های متنی که بازی را شروع می‌کنند (بدون اسلش)
START_PATTERNS = [r"^شروع\s+کلمو$", r"^شروع\s+بازی$", r"^کلمو$"]
_start_re = re.compile("|".join(START_PATTERNS))


def is_start_text(text):
    return bool(_start_re.match((text or "").strip()))


def _name(uid, fallback):
    """نام نمایشی انتخابی کاربر را برمی‌گرداند، وگرنه نام تلگرام."""
    return db.get_display_name(uid) or fallback


# ---------- ساخت/نمایش پنل ----------
async def open_lobby(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        return await update.message.reply_text(
            "🎮 شروع مسابقه فقط توی گروهه! منو به یه گروه اضافه کن و «شروع کلمو» بنویس.")
    if sess.exists(chat.id):
        return await update.message.reply_text("⚠️ یه مسابقه فعاله! اول «🏁 پایان» یا /endgame.")
    u = update.effective_user
    host_name = _name(u.id, u.first_name)
    s = sess.create(chat.id, u.id, host_name)
    cat = db.random_category()
    if not cat:
        sess.remove(chat.id)
        return await update.message.reply_text(
            "😅 هیچ دسته‌ای ثبت نشده. ادمین با /admin دسته و کلمه اضافه کنه.")
    s.category = cat
    s.words = db.list_words(cat) or []
    # سازنده خودکار عضو می‌شود
    s.join(u.id, host_name)
    svc.register(u.id, u.first_name)
    msg = await update.message.reply_text(
        panels.lobby_text(s), parse_mode=HTML, reply_markup=panels.lobby_kb(s))
    s.panel_msg_id = msg.message_id


async def cmd_newgame(update, ctx):
    chat = update.effective_chat
    u = update.effective_user
    # اگر بازی فعالی هست: پیام را پاک کن، اخطار بده (مگر معاف)
    if chat.type in ("group", "supergroup") and sess.exists(chat.id):
        s = sess.get(chat.id)
        if await _is_privileged(ctx, s, chat.id, u.id):
            return await update.message.reply_text(
                "⚠️ یه مسابقه فعاله! اول «🏁 پایان» یا /endgame.")
        await _safe_delete(update.message)
        return await _warn_and_maybe_mute(
            ctx, s, chat.id, u, "وقتی بازی فعاله نمی‌تونی بازی جدید بزنی")
    return await open_lobby(update, ctx)


async def cmd_endgame(update, ctx):
    chat = update.effective_chat
    if not sess.exists(chat.id):
        return await update.message.reply_text("الان مسابقه‌ای در جریان نیست.")
    s = sess.get(chat.id)
    u = update.effective_user
    if not await _is_privileged(ctx, s, chat.id, u.id):
        return await update.message.reply_text(
            "⛔️ فقط سازنده‌ی بازی یا ادمین‌های گروه می‌تونن بازی رو تموم کنن.")
    await _finish(ctx, chat.id)

async def cmd_settings(update, ctx):
    chat = update.effective_chat
    if chat.type == "private":
        return await update.message.reply_text("این تنظیمات مخصوص گروهه.")
    s = sess.get(chat.id)
    if not s:
        return await update.message.reply_text(
            "اول یه مسابقه بساز («شروع کلمو») تا بتونی حالت تمرکز رو تنظیم کنی.")
    await update.message.reply_text(panels.settings_text(s), parse_mode=HTML,
                                    reply_markup=panels.settings_kb(s))


# ---------- بررسی ادمین بودن در گروه تلگرام ----------
async def _is_group_admin(ctx, chat_id, uid):
    try:
        m = await ctx.bot.get_chat_member(chat_id, uid)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


async def _is_privileged(ctx, s, chat_id, uid):
    """سازنده‌ی بازی یا ادمین گروه؟ (این‌ها از همه‌ی قوانین معاف‌اند)"""
    if s and uid == s.host_id:
        return True
    return await _is_group_admin(ctx, chat_id, uid)


async def _safe_delete(msg):
    try:
        await msg.delete()
    except Exception:
        pass


async def _warn_and_maybe_mute(ctx, s, chat_id, u, reason_text):
    """
    یک اخطار ثبت می‌کند؛ در اخطار سوم کاربر را ۵ دقیقه سکوت می‌کند.
    یک پیام کوتاه (خوداتخریب) در گروه می‌فرستد.
    """
    n = s.add_warn(u.id)
    name = u.first_name or "کاربر"

    if n >= MAX_WARNS:
        muted = False
        try:
            await ctx.bot.restrict_chat_member(
                chat_id, u.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + MUTE_SECONDS,
            )
            muted = True
        except Exception:
            muted = False
        # اخطارها را برای شروع دوباره صفر کن تا بعد از سکوت از نو شمرده شود
        s.warns[u.id] = 0
        if muted:
            txt = (f"🔇 <a href=\"tg://user?id={u.id}\">{name}</a> "
                   f"به‌خاطر تکرار تخلف ۵ دقیقه سکوت شد.")
        else:
            txt = (f"⚠️ <a href=\"tg://user?id={u.id}\">{name}</a> به سقف اخطار رسید، "
                   f"ولی ربات دسترسی «محدودکردن اعضا» ندارد.")
    else:
        left = MAX_WARNS - n
        txt = (f"⚠️ <a href=\"tg://user?id={u.id}\">{name}</a> {reason_text} "
               f"(اخطار {n}/{MAX_WARNS} — {left} اخطار تا سکوت)")

    try:
        note = await ctx.bot.send_message(chat_id, txt, parse_mode=HTML)
        # پیام هشدار را بعد از ۵ ثانیه پاک کن تا گروه شلوغ نشود
        ctx.job_queue.run_once(
            lambda c: c.bot.delete_message(chat_id, note.message_id),
            5, name=f"delwarn:{chat_id}:{note.message_id}")
    except Exception:
        pass

# ---------- بررسی دسترسی سازنده ----------
def _host_only(s, uid):
    return uid == s.host_id



HOST_ACTIONS = {"mode", "setmode", "rules", "toggle", "time", "settime",
                "diff", "setdiff", "cat", "catpage", "setcat",
                "start", "cancel", "focus", "end"}
# ---------- callbackها ----------
async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat_id = q.message.chat.id
    u = q.from_user
    s = sess.get(chat_id)
    if not s:
        await q.answer("این مسابقه دیگه فعال نیست.", show_alert=True)
        try:
            await q.message.edit_reply_markup(None)
        except Exception:
            pass
        return

    parts = q.data.split(":")
    action = _arg(parts, 1) or ""

    # عضویت برای همه آزاد است
    if action == "join":
        nm = _name(u.id, u.first_name)
        ok = s.join(u.id, nm)
        if ok:
            svc.register(u.id, u.first_name)
            await q.answer("عضو شدی! 🎮")
            return await _refresh_lobby(q, s)
        return await q.answer("قبلاً عضوی یا بازی شروع شده.", show_alert=True)

    # بقیه‌ی اکشن‌های لابی فقط برای سازنده
    if action in HOST_ACTIONS and not _host_only(s, u.id):
        return await q.answer("فقط سازنده‌ی لابی این اجازه رو داره.", show_alert=True)

    if action == "mode":
        await q.answer()
        return await q.message.edit_text(panels.mode_text(), parse_mode=HTML,
                                         reply_markup=panels.mode_kb(s.mode_id))

    if action == "setmode":
        mid = _arg(parts, 2)
        if not s.set_mode(mid):
            return await q.answer("مود نامعتبر است.", show_alert=True)
        await q.answer(f"مود شد: {s.mode_name()}")
        return await _refresh_lobby(q, s)
    
    if action == "cat":
        cats = db.list_categories()
        await q.answer()
        return await q.message.edit_text(
            panels.category_text(),
            parse_mode=HTML,
            reply_markup=panels.category_kb(cats, s.category),
        )

    if action == "catpage":
        page = int(_arg(parts, 2) or 0)
        cats = db.list_categories()
        await q.answer()
        return await q.message.edit_text(
            panels.category_text(),
            parse_mode=HTML,
            reply_markup=panels.category_kb(cats, s.category, page=page),
        )    

    if action == "setcat":
        page = int(_arg(parts, 2) or 0)
        cat = ":".join(parts[3:]).strip()

        if not cat or not db.get_category(cat):
            return await q.answer("دسته نامعتبر است.", show_alert=True)

        s.category = cat
        s.words = db.list_words(cat) or []

        await q.answer("دسته انتخاب شد.")
        return await _refresh_lobby(q, s)

    if action == "time":
        await q.answer()
        return await q.message.edit_text(panels.time_text(s), parse_mode=HTML,
                                         reply_markup=panels.time_kb(s))

    if action == "settime":
        raw = _arg(parts, 2)
        valid = {str(sec) for sec, _ in sess.TIME_OPTIONS}
        if raw not in valid:
            return await q.answer("زمان نامعتبر است.", show_alert=True)
        s.time_limit = int(raw)
        await q.answer(f"زمان شد: {sess.time_label(s.time_limit)}")
        return await _refresh_lobby(q, s)

    if action == "diff":
        await q.answer()
        return await q.message.edit_text(
            "🎚 <b>سختی جای خالی</b>\n" + panels.DIV + "\nچقدر سخت باشه؟",
            parse_mode=HTML, reply_markup=panels.difficulty_kb(s))

    if action == "setdiff":
        raw = _arg(parts, 2)
        valid = {k for k, _ in sess.DIFFICULTY_OPTIONS}
        if raw not in valid:
            return await q.answer("سختی نامعتبر است.", show_alert=True)
        s.difficulty = raw
        await q.answer(f"سختی: {sess.difficulty_label(s.difficulty)}")
        return await _refresh_lobby(q, s)

    if action == "rules":
        await q.answer()
        return await q.message.edit_text(panels.rules_text(), parse_mode=HTML,
                                         reply_markup=panels.rules_kb(s))

    if action == "toggle":
        rid = _arg(parts, 2)
        if rid:
            s.ruleset.toggle(rid)
        await q.answer()
        return await q.message.edit_text(panels.rules_text(), parse_mode=HTML,
                                        reply_markup=panels.rules_kb(s))

    if action == "focus":
        s.focus_mode = not s.focus_mode
        await q.answer("حالت تمرکز " + ("روشن شد 🧹" if s.focus_mode else "خاموش شد"))
        return await q.message.edit_text(panels.settings_text(s), parse_mode=HTML,
                                         reply_markup=panels.settings_kb(s))

    if action == "back":
        await q.answer()
        return await _refresh_lobby(q, s)

    if action == "cancel":
        sess.remove(chat_id)
        await q.answer("لغو شد.")
        return await q.message.edit_text("❌ مسابقه لغو شد.")

    if action == "start":
        if s.count() < 2:
            return await q.answer("حداقل دو بازیکن برای شروع لازم است.", show_alert=True)
        if not _load_category_for_session(s):
            return await q.answer(
                "برای این مود دسته/کلمه کافی نیست.",
                show_alert=True,
            )
        await q.answer()
        return await _start_countdown(q, ctx, s)

    if action == "end":
        if not await _is_privileged(ctx, s, chat_id, u.id):
            return await q.answer(
                "فقط سازنده یا ادمین گروه می‌تونه پایان بده.", show_alert=True)
        await q.answer()
        return await _finish(ctx, chat_id)

async def _refresh_lobby(q, s):
    try:
        await q.message.edit_text(panels.lobby_text(s), parse_mode=HTML,
                                  reply_markup=panels.lobby_kb(s))
    except Exception:
        pass


# ---------- شمارش معکوس + آموزش + شروع ----------
async def _start_countdown(q, ctx, s):
    s.state = "countdown"
    msg = q.message
    mentions = _player_mentions(s)

    if mentions:
        try:
            await ctx.bot.send_message(
                s.chat_id,
                "شروع مسابقه:\n" + mentions,
                parse_mode=HTML,
            )
        except Exception:
            pass
    s.live_msg_id = msg.message_id
    for n in (3, 2, 1):
        try:
            await msg.edit_text(
                f"⏳ <b>مسابقه تا چند لحظه دیگر آغاز می‌شود…</b>\n\n<b>{n}</b>",
                parse_mode=HTML)
        except Exception:
            pass
        await asyncio.sleep(1)

    s.build_mode()
    s.state = "tutorial"
    try:
        await msg.edit_text("🧩 <b>آموزش مود</b>\n" + panels.DIV + "\n" +
                            s.mode.tutorial(), parse_mode=HTML)
    except Exception:
        pass
    await asyncio.sleep(5)

    # شروع واقعی
    s.state = "running"
    s.start_timer()
    s.next_question()
    try:
        await msg.edit_text(panels.live_text(s), parse_mode=HTML,
                            reply_markup=panels.running_kb(s))
    except Exception:
        pass

    if s.mode_id == "namefamily":
        from handlers import namefamily_private as nf
        await nf.start_group_namefamily(ctx, s)

    # زمان‌بند اتمام خودکار + رفرش زنده
    s.timer_task = asyncio.create_task(_run_loop(ctx, s))


async def _run_loop(ctx, s):
    """تا پایان زمان، پیام زنده را به‌روزرسانی می‌کند و سپس بازی را تمام می‌کند.

    برای اسم‌وفامیل هم تایمر قانون بازی ملاک است؛ کامل‌کردن فرم فقط یک شمارش
    معکوس کوتاه‌تر ایجاد می‌کند، اما پایان بازی وابسته به تکمیل همه پاسخ‌ها نیست.
    """
    try:
        while True:
            await asyncio.sleep(1)
            cur = sess.get(s.chat_id)
            if not cur or cur is not s or s.state != "running":
                return
            rem = s.remaining()
            if rem is not None and rem <= 0:
                await _finish(ctx, s.chat_id, reason="time")
                return
            if rem is None or int(rem) % 10 == 0:
                await _update_live(ctx, s)
    except asyncio.CancelledError:
        return
    except Exception:
        return


async def _update_live(ctx, s):
    if not s.live_msg_id:
        return
    try:
        await ctx.bot.edit_message_text(
            chat_id=s.chat_id, message_id=s.live_msg_id,
            text=panels.live_text(s), parse_mode=HTML,
            reply_markup=panels.running_kb(s))
    except Exception:
        pass


# ---------- پایان ----------
async def _finish(ctx, chat_id, reason=None):
    s = sess.remove(chat_id)
    if not s:
        return

    from features import lucky_box

    # ---- مود اسم‌وفامیل ----
    if s.mode_id == "namefamily" and s.mode:
        s.mode.lock()

        evaluated = s.mode.evaluate(s.players)

        # پاسخ‌های نامعتبر را وارد صف پیشنهاد کن
        for uid, data in evaluated.items():
            for cell in data["cells"]:
                if cell["status"] == "❌" and cell["answer"] != "—":
                    db.add_suggestion(
                        user_id=uid,
                        user_name=data["name"],
                        word=cell["answer"],
                        category=cell["cat"],
                        description="پیشنهاد خودکار از پاسخ نامعتبر اسم‌وفامیل",
                        source="namefamily"
                    )

        for uid, data in evaluated.items():
            if uid in s.players:
                s.players[uid]["score"] = data["total"]

        ranking = s.ranking()
        winner = ranking[0][0] if ranking and ranking[0][1]["score"] > 0 else None

        for uid, info in ranking:
            svc.record_game(
                uid,
                info["name"],
                won=(uid == winner),
                score=info["score"]
            )

        match_id = db.add_match_report(
            chat_id=chat_id,
            mode=s.mode_name(),
            winner_id=winner,
            players_count=len(ranking)
        )

        box_lines = []
        for uid, info in ranking:
            item = lucky_box.try_grant(uid, match_id=match_id)
            if item:
                box_lines.append(
                    f"🎁 <b>{info['name']}</b> یک Lucky Box گرفت: {lucky_box.item_text(item)}"
                )

        text = s.mode.result_text(s.players)
        if box_lines:
            text += "\n\n🎁 <b>Lucky Box</b>\n" + "\n".join(box_lines)

        # طبق درخواست: پیام جدید ارسال می‌شود، پیام اصلی بازی Edit نمی‌شود.
        await ctx.bot.send_message(chat_id, text, parse_mode=HTML)
        return

    # ---- بقیه مودها ----
    ranking = s.ranking()
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, info) in enumerate(ranking):
        badge = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{badge} <b>{info['name']}</b> — {info['score']} امتیاز")
    winner = ranking[0][0] if ranking and ranking[0][1]["score"] > 0 else None
    for uid, info in ranking:
        svc.record_game(uid, info["name"], won=(uid == winner), score=info["score"])

    match_id = db.add_match_report(
        chat_id=chat_id,
        mode=s.mode_name(),
        winner_id=winner,
        players_count=len(ranking)
    )

    box_lines = []
    for uid, info in ranking:
        item = lucky_box.try_grant(uid, match_id=match_id)
        if item:
            box_lines.append(
                f"🎁 <b>{info['name']}</b> یک Lucky Box گرفت: {lucky_box.item_text(item)}"
            )

    text = panels.finish_text(s, reason=reason) 
    if box_lines:
        text += "\n\n🎁 <b>Lucky Box</b>\n" + "\n".join(box_lines)

    await ctx.bot.send_message(chat_id, text, parse_mode=HTML)


# ---------- پیام‌های گروه هنگام بازی ----------
async def on_group_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """پاسخ بازی + حالت تمرکز. برمی‌گرداند True اگر پیام مصرف شد."""
    chat = update.effective_chat
    msg = update.message
    if not msg or not msg.text:
        return False
    s = sess.get(chat.id)
    if not s or s.state != "running":
        return False
    text = msg.text.strip()
    u = update.effective_user
    nm = _name(u.id, u.first_name)

    # --- مود اسم‌وفامیل: پاسخ‌ها فقط در PV ثبت می‌شوند ---
    if s.is_round_based():
        return False

    if _suggestion_hint(text):
        return await _handle_group_suggestion(update, ctx, s, text)

    # --- مودهای سوال‌محور ---
    res = s.submit(u.id, nm, text)
    if res and res["ok"]:
        found, total = s.progress()

        await msg.reply_text(
            panels.answer_ok_text(
                res["score"],
                found,
                total,
            )
        )

        if s.is_completed():
            await _finish(ctx, chat.id, reason="completed")
            return True

        s.next_question()
        await _update_live(ctx, s)


        return True
    return await _maybe_focus(ctx, s, msg, text, u, is_answer=res is not None)

async def _maybe_focus(ctx, s, msg, text, u, is_answer):
    if not s.focus_mode:
        return False
    too_long = len(text.split()) > FOCUS_WORD_LIMIT
    if is_answer or not too_long:
        return False
    # سازنده و ادمین‌های گروه معاف‌اند
    if await _is_privileged(ctx, s, s.chat_id, u.id):
        return False
    await _safe_delete(msg)
    await _warn_and_maybe_mute(
        ctx, s, s.chat_id, u, "حین بازی فقط جواب بده، نه جمله")
    return True


async def handle_start_during_game(update, ctx):
    chat = update.effective_chat
    u = update.effective_user
    s = sess.get(chat.id)
    if not s:
        return await open_lobby(update, ctx)
    if await _is_privileged(ctx, s, chat.id, u.id):
        return await update.message.reply_text(
            "⚠️ یه مسابقه فعاله! اول «🏁 پایان» یا /endgame.")
    await _safe_delete(update.message)
    return await _warn_and_maybe_mute(
        ctx, s, chat.id, u, "وقتی بازی فعاله نمی‌تونی بازی جدید شروع کنی")

def _load_category_for_session(s):
    if s.mode_id == "classic_choice" and not s.category:
        return False
    if not s.category:
        cat = db.random_category()
        if not cat:
            return False
        s.category = cat
    s.words = db.list_words(s.category) or []
    return bool(s.words) or s.is_round_based()

def _player_mentions(s):
    return " ".join(
        f'<a href="tg://user?id={uid}">{html.escape(info.get("name") or "بازیکن")}</a>'
        for uid, info in s.players.items()
    )

def _suggestion_hint(text):
    t = (text or "").strip()
    return (
        t.startswith("+کلمه")
        or t.startswith("پیشنهاد کلمه")
        or t.startswith("/suggest")
    )

async def _handle_group_suggestion(update, ctx, s, text):
    from features import suggestion_service as ss

    raw = text.strip()

    for prefix in ("+کلمه", "پیشنهاد کلمه", "/suggest"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break

    parts = [p.strip() for p in raw.split("|")]

    if len(parts) < 2:
        return await update.message.reply_text(
            "فرمت: +کلمه کلمه | دسته | توضیح اختیاری"
        )

    u = update.effective_user

    ok, msg = ss.create(
        u.id,
        u.first_name,
        parts[0],
        parts[1],
        parts[2] if len(parts) > 2 else "",
        source="game",
    )

    await update.message.reply_text(("✅ " if ok else "⚠️ ") + msg)
    return True