# -*- coding: utf-8 -*-
"""هندلر پیشنهاد کلمات توسط کاربران (در PV)."""

from telegram.constants import ParseMode
from features import suggestion_service as ss

HTML = ParseMode.HTML


async def start_suggest(update, ctx):
    if update.effective_chat.type != "private":
        msg = update.message
        if msg:
            return await msg.reply_text(
                "پیشنهاد کلمه را در چت خصوصی ربات ثبت کن."
            )
        return
    ctx.user_data["suggest_step"] = "word"
    ctx.user_data["suggest_data"] = {}

    target = (
        update.callback_query.message
        if update.callback_query
        else update.message
    )

    send = (
        target.edit_text
        if update.callback_query
        else target.reply_text
    )

    await send(
        "💡 <b>پیشنهاد کلمه جدید</b>\n"
        "━━━━━━━━━━━━━━\n"
        "اول خود کلمه را بفرست:",
        parse_mode=HTML
    )


async def on_suggest_text(update, ctx):
    if update.effective_chat.type != "private":
        return False

    step = ctx.user_data.get("suggest_step")
    if not step:
        return False

    text = (update.message.text or "").strip()
    data = ctx.user_data.setdefault("suggest_data", {})

    if step == "word":
        data["word"] = text
        ctx.user_data["suggest_step"] = "category"
        await update.message.reply_text(
            "حالا دسته‌بندی کلمه را بفرست. مثال: خوراکی، شهر، حیوان، بازیکنان فوتبال"
        )
        return True

    if step == "category":
        data["category"] = text
        ctx.user_data["suggest_step"] = "description"
        await update.message.reply_text("اگر توضیحی داری بفرست؛ اگر نداری فقط بنویس: -")
        return True

    if step == "description":
        desc = "" if text == "-" else text

        u = update.effective_user
        ok, msg = ss.create(
            uid=u.id,
            user_name=u.first_name,
            word=data.get("word"),
            category=data.get("category"),
            description=desc,
            source=data.get("source", "menu")
        )

        ctx.user_data.pop("suggest_step", None)
        ctx.user_data.pop("suggest_data", None)

        await update.message.reply_text(("✅ " if ok else "⚠️ ") + msg)
        return True

    return False
