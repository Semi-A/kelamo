# -*- coding: utf-8 -*-
"""باغچه کلمو: UI دکمه‌ای، سبک و مناسب تلگرام."""

import html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core import db
from features import garden_service

HTML = ParseMode.HTML
DIV = "━━━━━━━━━━━━━━━━"


def _e(text):
    return html.escape(str(text or ""))

_ART_SEED = r"""
        .
       / \
      /___\
   ___\___/___
  /___________\
"""

_ART_SPROUT = r"""
        |
       \|/
        |
   _____|_____
  /___________\
"""

_ART_SAPLING = r"""
      \  |  /
       \ | /
        \|/
         |
         |
   ______|______
  /_____________\
"""

_ART_SMALL = r"""
       .----.
    .-'      '-.
   /    ||      \
   \    ||      /
    '-. ||  .-'
        ||
        ||
   _____||_____
  /____________\
"""

_ART_TREE = r"""
       .--------.
    .-'          '-.
   /   .------.     \
  |   /        \     |
  |   \        /     |
   \   '------'     /
    '-.          .-'
        ||||||
        ||||||
   _____||||||_____
  /________________\
"""

_ART_FRUIT = r"""
       .--------.
    .-'  o  o    '-.
   /  o  .----.  o  \
  |     /  oo  \     |
  |  o  \      /  o  |
   \  o  '----'  o  /
    '-.   o  o   .-'
        ||||||
        ||||||
   _____||||||_____
  /________________\
"""

def _tree_art(tree):
    if not tree:
        return _ART_SEED

    growth = int(tree.get("growth") or 0)


    if growth >= 100:
        return _ART_FRUIT
    if growth >= 80:
        return _ART_TREE
    if growth >= 60:
        return _ART_SMALL
    if growth >= 40:
        return _ART_SAPLING
    if growth >= 20:
        return _ART_SPROUT
    return _ART_SEED

def _rarity_label(rarity):
    return {
        "normal": "معمولی",
        "blossom": "شکوفه‌دار",
        "rare": "کمیاب",
        "golden": "طلایی",
    }.get(rarity or "normal", "معمولی")


def _status(tree):
    if not tree:
        return "منتظر کاشت بذر"
    if int(tree.get("growth") or 0) >= 100:
        return "🎁 آماده برداشت"
    return "در حال رشد"


def garden_card(uid, viewer_uid=None):
    data = db.garden_public(uid)
    tree = data["tree"]
    seeds = data["seeds"]
    name = _e(data["name"])
    seed_count = sum(int(s["qty"] or 0) for s in seeds)

    if tree:
        growth = int(tree.get("growth") or 0)
        coins = int(tree.get("pending_coins") or 0)
        xp = int(tree.get("pending_xp") or 0)
        boxes = int(tree.get("pending_boxes") or 0)
        seed_type = _e(tree.get("seed_type") or "کلمو")
        rarity = _rarity_label(tree.get("rarity"))
    else:
        growth = 0
        coins = 0
        xp = 0
        boxes = 0
        seed_type = "—"
        rarity = "—"

    owner = "باغچه من" if viewer_uid == uid else f"باغچه {name}"
    lines = [
        f"🌳 <b>{owner}</b>",
        DIV,
        f"<pre>{_tree_art(tree)}</pre>",
        DIV,
        f"📈 رشد: <b>{growth}٪</b>",
        f"🌰 بذرها: <b>{seed_count}</b>",
        f"🧬 نوع درخت: <b>{seed_type}</b>",
        f"💠 کیفیت: <b>{rarity}</b>",
        f"💰 Coin آماده برداشت: <b>{coins}</b>",
        f"⭐ XP آماده برداشت: <b>{xp}</b>",
        f"🎁 Lucky Box آماده: <b>{boxes}</b>",
        f"🎁 وضعیت: <b>{_status(tree)}</b>",
        DIV,
    ]
    if viewer_uid == uid:
        lines.append(f"💧 آبیاری امروز باقی‌مانده: <b>{db.garden_water_left(uid)}</b>")
        lines.append("با بازی کردن، پاسخ درست و سر زدن روزانه رشد می‌کند.")
    return "\n".join(lines)


def home_kb(uid):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌱 کاشت بذر", callback_data="g:plant"),
            InlineKeyboardButton("🎁 برداشت", callback_data="g:harvest")
        ],
        [
            InlineKeyboardButton("🎒 موجودی بذرها", callback_data="g:inv"),
            InlineKeyboardButton("👥 باغ دوستان", callback_data="g:friends")
        ],
        [
            InlineKeyboardButton("📖 راهنمای باغچه", callback_data="g:help")
        ],
        [
            InlineKeyboardButton("🔄 تازه‌سازی", callback_data="g:home")
        ],
    ])

def plant_kb(uid):
    seeds = db.garden_seed_inventory(uid)
    rows = []
    for s in seeds[:12]:
        label = f"🌰 {s['seed_type']} ×{s['qty']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"g:plant:{s['seed_type']}")])
    rows.append([InlineKeyboardButton("↩ برگشت", callback_data="g:home")])
    return InlineKeyboardMarkup(rows)


def friends_kb(uid):
    rows = []
    for row in db.garden_friend_gardens(uid, 8):
        growth = int(row.get("growth") or 0)
        name = row.get("shown_name") or f"کاربر {row['user_id']}"
        rows.append([InlineKeyboardButton(f"🌳 {name} — {growth}٪", callback_data=f"g:view:{row['user_id']}")])
    if not rows:
        rows.append([InlineKeyboardButton("فعلاً باغ فعالی نیست", callback_data="g:noop")])
    rows.append([InlineKeyboardButton("↩ برگشت", callback_data="g:home")])
    return InlineKeyboardMarkup(rows)


def view_kb(target_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💧 آبیاری", callback_data=f"g:water:{target_id}")],
        [InlineKeyboardButton("👥 باغ دوستان", callback_data="g:friends"), InlineKeyboardButton("↩ باغ من", callback_data="g:home")],
    ])


async def open_garden(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.garden_ensure_starter(user.id, user.first_name)
    garden_service.on_daily_garden_visit(user.id)
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        return await q.message.edit_text(
            garden_card(user.id, viewer_uid=user.id), parse_mode=HTML, reply_markup=home_kb(user.id)
        )
    return await update.message.reply_text(
        garden_card(user.id, viewer_uid=user.id), parse_mode=HTML, reply_markup=home_kb(user.id)
    )


async def cmd_garden(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return await update.message.reply_text("🌳 باغچه در چت خصوصی ربات باز می‌شود.")
    return await open_garden(update, ctx)


async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    parts = (q.data or "").split(":")
    action = parts[1] if len(parts) > 1 else "home"
    db.garden_ensure_starter(user.id, user.first_name)

    if action == "noop":
        return await q.answer("فعلاً موردی برای نمایش نیست.", show_alert=True)

    if action == "home":
        garden_service.on_daily_garden_visit(user.id)
        await q.answer()
        return await q.message.edit_text(garden_card(user.id, viewer_uid=user.id), parse_mode=HTML, reply_markup=home_kb(user.id))

    if action == "help":
        await q.answer()
        text = ("📖 <b>راهنمای باغچه</b>\n" + DIV + "\n"
                "• با بازی، پاسخ درست و ورود روزانه رشد می‌گیرد.\n"
                "• هر روز می‌توانی باغ خودت و دوستانت را آبیاری کنی.\n"
                "• وقتی رشد به ۱۰۰٪ برسد، برداشت فعال می‌شود.\n"
                "• برداشت می‌تواند Coin، XP، Lucky Box یا بذر بدهد.\n"
                "• بذرهای بهتر، پاداش جذاب‌تری دارند.")
        return await q.message.edit_text(
            text,
            parse_mode=HTML,
            reply_markup=home_kb(user.id)
        )

    if action == "plant":
        if len(parts) >= 3:
            seed_type = ":".join(parts[2:])
            ok, msg = db.garden_plant_seed(user.id, seed_type)
            await q.answer(msg, show_alert=not ok)
            return await q.message.edit_text(garden_card(user.id, viewer_uid=user.id), parse_mode=HTML, reply_markup=home_kb(user.id))
        await q.answer()
        seeds = db.garden_seed_inventory(user.id)
        text = "🌱 <b>کاشت بذر</b>\n" + DIV + "\n"
        if seeds:
            text += "یکی از بذرها را انتخاب کن تا در باغچه کاشته شود."
        else:
            text += "فعلاً بذری نداری. با بازی کردن، بردن مسابقه یا Lucky Box بذر می‌گیری."
        return await q.message.edit_text(text, parse_mode=HTML, reply_markup=plant_kb(user.id))

    if action == "harvest":
        ok, msg, reward = db.garden_harvest(user.id)
        if ok and reward:
            extra = f"\n\n💰 +{reward['coins']} Coin\n⭐ +{reward['xp']} XP"
            if reward.get("boxes"):
                extra += f"\n🎁 +{reward['boxes']} Lucky Box/بذر جایزه"
            if reward.get("seed"):
                extra += f"\n🌰 بذر جدید: {reward['seed']}"
            await q.answer("برداشت شد!", show_alert=False)
            text = "🎁 <b>برداشت باغچه</b>\n" + DIV + extra + "\n\n" + garden_card(user.id, viewer_uid=user.id)
        else:
            await q.answer(msg, show_alert=True)
            text = garden_card(user.id, viewer_uid=user.id)
        return await q.message.edit_text(text, parse_mode=HTML, reply_markup=home_kb(user.id))

    if action == "inv":
        seeds = db.garden_seed_inventory(user.id)
        if seeds:
            body = "\n".join(f"🌰 <b>{_e(s['seed_type'])}</b> × {int(s['qty'])}" for s in seeds)
        else:
            body = "هنوز بذری نداری. بعد از مسابقه‌ها و Lucky Box شانس گرفتن بذر داری."
        text = "🎒 <b>موجودی بذرها</b>\n" + DIV + "\n" + body
        await q.answer()
        return await q.message.edit_text(text, parse_mode=HTML, reply_markup=plant_kb(user.id))

    if action == "friends":
        text = "👥 <b>باغ دوستان</b>\n" + DIV + "\nیک باغ را انتخاب کن و اگر سهمیه داری آبیاری کن."
        await q.answer()
        return await q.message.edit_text(text, parse_mode=HTML, reply_markup=friends_kb(user.id))

    if action == "view" and len(parts) >= 3:
        try:
            target_id = int(parts[2])
        except (ValueError, TypeError):
            return await q.answer("شناسه نامعتبر است.", show_alert=True)
        await q.answer()
        return await q.message.edit_text(garden_card(target_id, viewer_uid=user.id), parse_mode=HTML, reply_markup=view_kb(target_id))

    if action == "water" and len(parts) >= 3:
        try:
            target_id = int(parts[2])
        except (ValueError, TypeError):
            return await q.answer("شناسه نامعتبر است.", show_alert=True)
        ok, msg = db.garden_water(user.id, target_id)
        await q.answer(msg, show_alert=not ok)
        return await q.message.edit_text(garden_card(target_id, viewer_uid=user.id), parse_mode=HTML, reply_markup=view_kb(target_id))

    await q.answer("دکمه نامعتبر است.", show_alert=True)
