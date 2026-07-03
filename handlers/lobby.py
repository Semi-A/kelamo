# -*- coding: utf-8 -*-
"""هندلر بازی گروهی کلمو: شروع با متن یا دستور، لابی، مود، قوانین، زمان،
عضویت، شمارش معکوس، آموزش مود، اجرا، نمایش زنده، اتمام خودکار، حالت تمرکز.
همه با EditMessage.

اسم‌وفامیل: پاسخ‌ها فقط در PV ثبت می‌شوند (handlers/namefamily_private.py).
پایان مسابقه: گزارش مسابقه ثبت می‌شود و احتمال «🎁 جعبه شانس» بررسی می‌شود.

نسخه‌ی Phase 1 (Beta) — رفع باگ‌ها:
- تایمر روی صفر فریز نمی‌شود (remaining هرگز منفی نیست) و در پایان زمان،
  بازی به‌طور قطعی تمام می‌شود.
- auto-skip: در مودهای سوال‌محور (به‌جز کلاسیک و اسم‌وفامیل) اگر ۲۰ ثانیه
  هیچ پاسخ درستی نیاید، سوال به‌طور خودکار رد و سوال بعدی نمایش داده می‌شود.
- پیام وضعیت دوره‌ای هر ۳۰ ثانیه (status_task) برای زنده نگه‌داشتن مسابقه.
- خطاهای تسک‌های پس‌زمینه دیگر خاموش نیستند و لاگ می‌شوند.
- قفل پایان (s.finishing) از اجرای دوباره‌ی پایان (race condition) جلوگیری می‌کند.
- سازگاری با session.submit جدید (کلیدهای points/found/total) و پاسخ تکراری.
- «Lucky Box» → «🎁 جعبه شانس».
"""
import random
import asyncio
import html
import logging
import re
import time
from datetime import timedelta
from telegram.error import RetryAfter
from telegram import Update, ChatPermissions
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from core import db
from features import player_service as svc
from game import session as sess
from ui import panels, persona

HTML = ParseMode.HTML
log = logging.getLogger(__name__)

MAX_WARNS = 3                 # سقف اخطار قبل از سکوت
MUTE_SECONDS = 5 * 60         # مدت سکوت: ۵ دقیقه
FOCUS_WORD_LIMIT = 3          # بیش از این تعداد کلمه = پیام جمله‌ای
STATUS_INTERVAL = 30          # فاصله‌ی پیام وضعیت دوره‌ای (ثانیه)
LIVE_REFRESH = 10            # فاصله‌ی رفرش پیام زنده (ثانیه)


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
    """یک اخطار ثبت می‌کند؛ در اخطار سوم کاربر را ۵ دقیقه سکوت می‌کند."""
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
        ctx.job_queue.run_once(
            lambda c: c.bot.delete_message(chat_id, note.message_id),
            5, name=f"delwarn:{chat_id}:{note.message_id}")
    except Exception:
        log.exception("ارسال/زمان‌بندی حذف پیام اخطار شکست خورد")


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

        # اگر اسم‌وفامیل انتخاب شد، برو به انتخاب دسته‌ها
        if mid == "namefamily":
            await q.answer()
            return await q.message.edit_text(
                panels.namefamily_category_text(),
                parse_mode=HTML,
                reply_markup=panels.namefamily_category_kb(s)
            )

        await q.answer(f"مود شد: {s.mode_name()}")
        return await _refresh_lobby(q, s)
    
    if action == "nftoggle":

        cat = ":".join(parts[2:])

        if cat in s.namefamily_categories:
            s.namefamily_categories.remove(cat)
        else:
            s.namefamily_categories.append(cat)

        await q.answer()

        return await q.message.edit_text(
            panels.namefamily_category_text(),
            parse_mode=HTML,
            reply_markup=panels.namefamily_category_kb(s)
        )

    if action == "nfrandom":

        cats = [c for c, _ in db.list_categories()]

        random.shuffle(cats)

        s.namefamily_categories = cats[:6]

        await q.answer("۶ دسته انتخاب شد.")

        return await q.message.edit_text(
            panels.namefamily_category_text(),
            parse_mode=HTML,
            reply_markup=panels.namefamily_category_kb(s)
        )    

    if action == "nfdone":

        if not s.namefamily_categories:

            cats = [c for c, _ in db.list_categories()]

            random.shuffle(cats)

            s.namefamily_categories = cats[:6]

        await q.answer("ثبت شد.")

        return await _refresh_lobby(q, s)

    if action == "cat":
        cats = db.list_categories()
        await q.answer()
        return await q.message.edit_text(
            panels.category_text(), parse_mode=HTML,
            reply_markup=panels.category_kb(cats, s.category))

    if action == "catpage":
        page = int(_arg(parts, 2) or 0)
        cats = db.list_categories()
        await q.answer()
        return await q.message.edit_text(
            panels.category_text(), parse_mode=HTML,
            reply_markup=panels.category_kb(cats, s.category, page=page))

    if action == "setcat":
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
            return await q.answer("برای این مود دسته/کلمه کافی نیست.", show_alert=True)
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
                s.chat_id, "شروع مسابقه:\n" + mentions, parse_mode=HTML)
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

    # زمان‌بند اتمام خودکار/auto-skip + پیام وضعیت دوره‌ای
    s.timer_task = asyncio.create_task(_run_loop(ctx, s))
    s.status_task = asyncio.create_task(_status_loop(ctx, s))


async def _run_loop(ctx, s):
    """حلقه‌ی اصلی بازی: هر ثانیه بررسی می‌کند.

    - اگر زمان تمام شد → بازی را تمام می‌کند (تایمر روی صفر فریز نمی‌شود).
    - auto-skip: اگر مود واجد شرایط باشد و ۲۰ ثانیه هیچ پاسخ درستی نیاید،
      سوال رد و سوال بعدی نمایش داده می‌شود.
    - پیام زنده هر LIVE_REFRESH ثانیه به‌روزرسانی می‌شود.
    """
    try:
        while True:
            await asyncio.sleep(1)
            cur = sess.get(s.chat_id)
            if not cur or cur is not s or s.state != "running":
                return

            rem = s.remaining()
            if rem is not None and rem <= 0:
                asyncio.create_task(_finish(ctx, s.chat_id, reason="time"))
                return
            # auto-skip برای مودهای سوال‌محورِ واجد شرایط
            if s.autoskip_enabled() and not s.is_round_based():
                last = s.last_answer_at or s.started_at or time.time()
                if time.time() - last >= sess.AUTOSKIP_SECONDS:
                    await _autoskip(ctx, s)

            # رفرش پیام زنده
            if rem is None or int(rem) % LIVE_REFRESH == 0:
                await _update_live(ctx, s)
    except asyncio.CancelledError:
        return
    except Exception as e:
        print("RUN_LOOP ERROR:", e)
        raise

async def _autoskip(ctx, s):
    """سوال فعلی را رد می‌کند و سوال بعدی را نمایش می‌دهد."""
    try:
        prev = s.question.get("prompt") if isinstance(s.question, dict) else None
        # مود سرنخ: قبل از رد کردن، جواب سرنخ قبلی را لو بده
        if s.mode_id == "clue" and isinstance(s.question, dict):
            reveal = s.question.get("reveal")
            if reveal:
                try:
                    await ctx.bot.send_message(
                        s.chat_id,
                        f"⏰ کسی جواب نداد!\nجواب درست: <b>{reveal}</b>",
                        parse_mode=HTML)
                except Exception:
                    pass

        s.next_question()
        s.last_answer_at = time.time()

        # مود سرنخ: سرنخ بعدی را در پیام جدید بفرست
        if s.mode_id == "clue" and isinstance(s.question, dict):
            try:
                await ctx.bot.send_message(
                    s.chat_id, s.question["prompt"], parse_mode=HTML)
            except Exception:
                pass

        cur = s.question.get("prompt") if isinstance(s.question, dict) else None
        if cur != prev and s.mode_id != "clue":
            try:
                await ctx.bot.send_message(
                    s.chat_id, "⏭ کسی جواب نداد؛ سوال بعدی!", parse_mode=HTML)
            except Exception:
                pass
        await _update_live(ctx, s)
    except Exception:
        log.exception("خطا در auto-skip (chat_id=%s)", s.chat_id)

async def _status_loop(ctx, s):
    """هر STATUS_INTERVAL ثانیه یک پیام وضعیت کوتاه در گروه می‌فرستد تا مسابقه
    زنده و قابل‌دنبال‌کردن بماند (به‌ویژه در گروه‌های شلوغ)."""
    try:
        while True:
            await asyncio.sleep(STATUS_INTERVAL)
            cur = sess.get(s.chat_id)
            if not cur or cur is not s or s.state != "running":
                return
            leader = s.leader()
            leader_line = (f"🥇 صدرنشین: <b>{leader[0]}</b> — {leader[1]} امتیاز"
                           if leader else "🥇 هنوز کسی امتیاز نگرفته")
            try:
                await ctx.bot.send_message(
                    s.chat_id,
                    f"⏱ باقی‌مانده: <b>{s.remaining_label()}</b>\n{leader_line}",
                    parse_mode=HTML)
            except Exception:
                pass
    except asyncio.CancelledError:
        return
    except Exception:
        log.exception("خطای غیرمنتظره در حلقه‌ی وضعیت (chat_id=%s)", s.chat_id)


MIN_EDIT_INTERVAL = 1.0   # حداکثر یک ویرایش در ثانیه برای هر مسابقه


async def _update_live(ctx, s):
    """آپدیت پیام زنده با محدودیت نرخ: حداکثر ۱ ویرایش/ثانیه، فقط آخرین وضعیت."""
    if not s.live_msg_id:
        return
    if s.live_lock is None:
        s.live_lock = asyncio.Lock()

    s.live_dirty = True
    # اگر فلشری در حال کار است، همان آخرین وضعیت را می‌فرستد؛ نیازی به task جدید نیست.
    if s.live_flusher and not s.live_flusher.done():
        return
    s.live_flusher = asyncio.create_task(_flush_live(ctx, s))


async def _flush_live(ctx, s):
    async with s.live_lock:
        while s.live_dirty:
            wait = MIN_EDIT_INTERVAL - (time.time() - s.live_last_edit)
            if wait > 0:
                await asyncio.sleep(wait)
            # آخرین وضعیت را برمی‌داریم (coalescing)
            s.live_dirty = False
            cur = sess.get(s.chat_id)
            if not cur or cur is not s or not s.live_msg_id:
                return
            try:
                await ctx.bot.edit_message_text(
                    chat_id=s.chat_id, message_id=s.live_msg_id,
                    text=panels.live_text(s), parse_mode=HTML,
                    reply_markup=panels.running_kb(s))
                s.live_last_edit = time.time()
            except RetryAfter as e:
                await asyncio.sleep(float(getattr(e, "retry_after", 1)) + 0.5)
                s.live_dirty = True   # دوباره تلاش کن
            except Exception:
                # مثل «message is not modified» — نادیده بگیر.
                pass

# ---------- پایان ----------
async def _finish(ctx, chat_id, reason=None):
    # قفل پایان: از اجرای هم‌زمان/دوباره جلوگیری می‌کند (race condition).
    s = sess.get(chat_id)
    if not s or getattr(s, "finishing", False):
        return
    s.finishing = True

    s = sess.remove(chat_id)
    if not s:
        return

    from features import lucky_box

    # ---- مود اسم‌وفامیل ----
    if s.mode_id == "namefamily" and s.mode:
        s.mode.lock()
        evaluated = s.mode.evaluate(s.players)

        for uid, data in evaluated.items():
            for cell in data["cells"]:
                if cell["status"] == "❌" and cell["answer"] != "—":
                    db.add_suggestion(
                        user_id=uid, user_name=data["name"],
                        word=cell["answer"], category=cell["cat"],
                        description="پیشنهاد خودکار از پاسخ نامعتبر اسم‌وفامیل",
                        source="namefamily")

        for uid, data in evaluated.items():
            if uid in s.players:
                s.players[uid]["score"] = data["total"]

        ranking = s.ranking()
        winner = ranking[0][0] if ranking and ranking[0][1]["score"] > 0 else None
        for uid, info in ranking:
            svc.record_game(uid, info["name"], won=(uid == winner), score=info["score"])

        match_id = db.add_match_report(
            chat_id=chat_id, mode=s.mode_name(),
            winner_id=winner, players_count=len(ranking))

        box_lines = _grant_boxes(lucky_box, ranking, match_id)
        text = s.mode.result_text(s.players)
        if box_lines:
            text += "\n\n🎁 <b>جعبه شانس</b>\n" + "\n".join(box_lines)
        await ctx.bot.send_message(chat_id, text, parse_mode=HTML)
        return

    # ---- بقیه مودها ----
    ranking = s.ranking()
    winner = ranking[0][0] if ranking and ranking[0][1]["score"] > 0 else None
    for uid, info in ranking:
        svc.record_game(uid, info["name"], won=(uid == winner), score=info["score"])

    match_id = db.add_match_report(
        chat_id=chat_id, mode=s.mode_name(),
        winner_id=winner, players_count=len(ranking))

    box_lines = _grant_boxes(lucky_box, ranking, match_id)
    text = panels.finish_text(s, reason=reason)
    if box_lines:
        text += "\n\n🎁 <b>جعبه شانس</b>\n" + "\n".join(box_lines)
    await ctx.bot.send_message(chat_id, text, parse_mode=HTML)


def _grant_boxes(lucky_box, ranking, match_id):
    """برای هر بازیکن شانس «🎁 جعبه شانس» را بررسی می‌کند و خطوط نمایش می‌سازد."""
    box_lines = []
    for uid, info in ranking:
        try:
            item = lucky_box.try_grant(uid, match_id=match_id)
        except Exception:
            log.exception("خطا در اعطای جعبه شانس به کاربر %s", uid)
            continue
        if item:
            box_lines.append(
                f"🎁 <b>{info['name']}</b> یک جعبه شانس گرفت: {lucky_box.item_text(item)}")
    return box_lines


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

    if res and res.get("ok"):
        found = res.get("found")
        total = res.get("total")
        if found is None or total is None:
            found, total = s.progress()

        await msg.reply_text(panels.answer_ok_text(res["score"], found, total))

        if s.is_completed():
            await _finish(ctx, chat.id, reason="completed")
            return True

        s.next_question()
        # مود سرنخ: سرنخ جدید را در یک پیام جدید هم اعلام کن
        if s.mode_id == "clue" and isinstance(s.question, dict):
            try:
                await ctx.bot.send_message(
                    chat.id, s.question["prompt"], parse_mode=HTML)
            except Exception:
                pass
        await _update_live(ctx, s)
        return True
    # پاسخ تکراری: فقط یک تذکر کوتاه، بدون کسر امتیاز.
    if res and res.get("reason") == "duplicate":
        try:
            await msg.reply_text("♻️ این کلمه قبلاً گفته شده!")
        except Exception:
            pass
        return True

    # پاسخ اشتباه یا نامرتبط → حالت تمرکز را بررسی کن.
    return await _maybe_focus(ctx, s, msg, text, u, is_answer=res is not None)


async def _maybe_focus(ctx, s, msg, text, u, is_answer):
    active = s.focus_mode or s.ruleset.is_active("no_disturb")
    if not active:
        return False
    too_long = len(text.split()) > FOCUS_WORD_LIMIT
    if is_answer or not too_long:
        return False
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
        for uid, info in s.players.items())


def _suggestion_hint(text):
    t = (text or "").strip()
    return (t.startswith("+کلمه") or t.startswith("پیشنهاد کلمه")
            or t.startswith("/suggest"))


async def _handle_group_suggestion(update, ctx, s, text):
    from features import suggestion_service as ss
    raw = text.strip()
    for prefix in ("+کلمه", "پیشنهاد کلمه", "/suggest"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2:
        return await update.message.reply_text("فرمت: +کلمه کلمه | دسته | توضیح اختیاری")
    u = update.effective_user
    ok, msg = ss.create(
        u.id, u.first_name, parts[0], parts[1],
        parts[2] if len(parts) > 2 else "", source="game")
    await update.message.reply_text(("✅ " if ok else "⚠️ ") + msg)
    return True
