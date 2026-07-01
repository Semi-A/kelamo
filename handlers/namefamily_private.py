# -*- coding: utf-8 -*-
"""ثبت پاسخ‌های اسم‌وفامیل در PV."""

import asyncio
import config

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

from game import session as sess

HTML = ParseMode.HTML


def encode_start_param(chat_id):
    return "nf_" + str(chat_id).replace("-", "m")


def decode_start_param(arg):
    try:
        if not arg.startswith("nf_"):
            return None
        raw = arg[3:].replace("m", "-")
        return int(raw)
    except Exception:
        return None


def pv_url(chat_id):
    return f"https://t.me/{config.BOT_USERNAME}?start={encode_start_param(chat_id)}"


async def send_form(ctx, s, uid, fallback_name=""):
    if not s or not s.mode or s.mode_id != "namefamily":
        return False

    if uid not in s.players:
        if s.state == "running":
            s.players[uid] = {"name": fallback_name or f"کاربر {uid}", "score": 0}
        else:
            return False

    try:
        msg = await ctx.bot.send_message(
            chat_id=uid,
            text=s.mode.form_text(uid),
            parse_mode=HTML,
            reply_markup=s.mode.form_kb(s.chat_id, uid),
        )
        s.mode.private_messages[uid] = msg.message_id
        return True
    except Forbidden:
        return False


async def start_from_private(ctx, chat_id, user):
    s = sess.get(chat_id)
    if not s or s.state != "running" or s.mode_id != "namefamily":
        return False

    return await send_form(ctx, s, user.id, user.first_name)


async def start_group_namefamily(ctx, s):
    """بعد از شروع مود، پیام گروه و فرم‌های PV را ارسال می‌کند."""

    url = pv_url(s.chat_id)

    await ctx.bot.send_message(
        chat_id=s.chat_id,
        text=(
            "✉️ <b>پاسخ‌های این مسابقه از طریق گفتگوی خصوصی ربات ثبت می‌شوند.</b>\n"
            "لطفاً وارد PV ربات شوید و فرم اسم‌وفامیل را کامل کنید."
        ),
        parse_mode=HTML,
        reply_markup=M([[B("ورود به PV ربات", url=url)]])
    )

    failed = []

    for uid, info in s.players.items():
        ok = await send_form(ctx, s, uid, info["name"])
        if not ok:
            failed.append(info["name"])

    if failed:
        names = "، ".join(failed)
        await ctx.bot.send_message(
            chat_id=s.chat_id,
            text=(
                "⚠️ بعضی بازیکن‌ها هنوز ربات را Start نکرده‌اند:\n"
                f"{names}\n\n"
                "برای ثبت پاسخ، روی دکمه زیر بزنید:"
            ),
            reply_markup=M([[B("Start ربات و دریافت فرم", url=url)]])
        )


async def on_nf_cb(update, ctx):
    q = update.callback_query
    await q.answer()

    parts = q.data.split(":")
    # nf:set:<chat_id>:<cat_idx>
    if len(parts) != 4 or parts[1] != "set":
        return

    chat_id = int(parts[2])
    cat_idx = int(parts[3])

    s = sess.get(chat_id)
    if not s or s.state != "running" or s.mode_id != "namefamily":
        return await q.message.reply_text("⛔️ این مسابقه دیگر فعال نیست.")

    if s.mode.locked:
        return await q.message.reply_text("⛔️ زمان پاسخ‌گویی تمام شده.")

    cat = s.mode.cats[cat_idx]

    ctx.user_data["nf_await"] = {
        "chat_id": chat_id,
        "cat_idx": cat_idx,
        "form_msg_id": q.message.message_id,
    }

    await q.message.reply_text(
        f"✍️ پاسخ دسته <b>{cat}</b> را با حرف <b>«{s.mode.letter}»</b> بفرست.\n"
        "برای حذف پاسخ این دسته، فقط بنویس: <code>-</code>",
        parse_mode=HTML
    )


async def on_nf_text(update, ctx):
    if update.effective_chat.type != "private":
        return False

    state = ctx.user_data.get("nf_await")
    if not state:
        return False

    uid = update.effective_user.id
    chat_id = state["chat_id"]
    cat_idx = state["cat_idx"]
    form_msg_id = state.get("form_msg_id")

    s = sess.get(chat_id)
    if not s or s.state != "running" or s.mode_id != "namefamily":
        ctx.user_data.pop("nf_await", None)
        await update.message.reply_text("⛔️ این مسابقه دیگر فعال نیست.")
        return True

    ok, msg = s.mode.submit_answer(uid, cat_idx, update.message.text)
    ctx.user_data.pop("nf_await", None)

    await update.message.reply_text(("✅ " if ok else "⛔️ ") + msg)

    if form_msg_id:
        try:
            await ctx.bot.edit_message_text(
                chat_id=uid,
                message_id=form_msg_id,
                text=s.mode.form_text(uid),
                parse_mode=HTML,
                reply_markup=s.mode.form_kb(chat_id, uid),
            )
            s.mode.private_messages[uid] = form_msg_id
        except BadRequest:
            pass

    if ok and s.mode.is_complete(uid) and not s.mode.final_countdown_started:
        s.mode.final_countdown_started = True
        remaining = s.remaining() if hasattr(s, "remaining") else None
        seconds = 15 if remaining is None else max(1, min(15, int(remaining)))

        text = (
            f"⏳ اولین بازیکن پاسخ‌های خود را کامل کرد.\n"
            f"فقط <b>{seconds} ثانیه</b> تا پایان مسابقه باقی مانده است."
        )

        await ctx.bot.send_message(s.chat_id, text, parse_mode=HTML)

        for pid in s.players:
            try:
                await ctx.bot.send_message(pid, text, parse_mode=HTML)
            except Exception:
                pass

        asyncio.create_task(_finish_after_delay(ctx, chat_id, seconds))

    return True


async def _finish_after_delay(ctx, chat_id, seconds):
    await asyncio.sleep(seconds)

    s = sess.get(chat_id)
    if not s or s.state != "running" or s.mode_id != "namefamily":
        return

    from handlers import lobby
    await lobby._finish(ctx, chat_id, reason="namefamily_fast")
