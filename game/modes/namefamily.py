# -*- coding: utf-8 -*-
"""مود حرفه‌ای اسم‌وفامیل با ثبت پاسخ در PV.

- دسته‌ها فقط ۹ مورد استاندارد اسم‌وفامیل هستند: غذا، رنگ، میوه، حیوان، اشیا، عضو بدن، شهر، کشور، شغل.
- پاسخ‌ها با دیتابیس همان دسته تطبیق داده می‌شوند (نه هر متن دلخواه).
- پاسخ‌های نامعتبر بعداً می‌توانند وارد صف پیشنهاد کلمه شوند (در handlers/lobby.py).
"""

import random
import html

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M

PERSIAN_LETTERS = list("ابپتجچحخدرزسشصطعفقکگلمنوهی")

PT_UNIQUE = 10
PT_SHARED = 5
PT_INVALID = 0
PT_EMPTY = 0




def load_db_categories(limit=None):
    """تمام دسته‌بندی‌های دارای کلمه در دیتابیس، بدون نیاز به تغییر کد."""
    from core import db

    cats = [cat for cat, cnt in db.list_categories() if int(cnt or 0) > 0]

    return cats[:limit] if limit else cats

class NameFamilyMode:
    id = "namefamily"
    name = "اسم‌وفامیل"
    emoji = "✍️"

    def __init__(self, words=None, ruleset=None, num_categories=None, **kw):
        self.words = list(words or [])
        self.ruleset = ruleset
        self.letter = random.choice(PERSIAN_LETTERS)

        # فقط دسته‌های استاندارد اسم‌وفامیل
        self.cats = load_db_categories()

        # uid -> {cat_index: answer}
        self.answers = {}

        self.locked = False
        self.final_countdown_started = False
        self.final_deadline = None

        # uid -> private form message id
        self.private_messages = {}

    def tutorial(self):
        if not self.cats:
            return (
                "✍️ <b>مود اسم‌وفامیل</b>\n"
                "هنوز هیچ دسته‌بندی با کلمه در دیتابیس ثبت نشده. ادمین باید اول کلمه اضافه کند."
            )

        cats = "، ".join(self.cats)
        return (
            "✍️ <b>مود اسم‌وفامیل</b>\n"
            f"حرف این دور: <b>«{self.letter}»</b>\n"
            f"دسته‌ها: {cats}\n\n"
            "پاسخ‌ها در گفتگوی خصوصی ربات ثبت می‌شوند.\n"
            "برای هر دسته جداگانه جواب بده و تا پایان مسابقه می‌تونی ویرایش کنی."
        )

    def new_question(self):
        return {
            "prompt": (
                f"✍️ <b>اسم‌وفامیل — حرف «{self.letter}»</b>\n\n"
                "پاسخ‌ها از طریق PV ربات ثبت می‌شوند."
            ),
            "letter": self.letter,
        }

    def form_text(self, uid):
        done = len(self.answers.get(uid, {}))
        total = len(self.cats)
        return (
            f"✍️ <b>فرم اسم‌وفامیل</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"حرف این مسابقه: <b>«{self.letter}»</b>\n"
            f"تکمیل‌شده: <b>{done}/{total}</b>\n\n"
            "روی هر دسته بزن و پاسخ همان دسته را ارسال کن.\n"
            "تا قبل از پایان مسابقه می‌تونی هر پاسخ رو ویرایش کنی."
        )

    def form_kb(self, chat_id, uid):
        user_answers = self.answers.get(uid, {})
        rows = []

        for i, cat in enumerate(self.cats):
            mark = "✅" if i in user_answers and user_answers[i].strip() else "⬜"
            rows.append([
                B(f"{mark} {cat}", callback_data=f"nf:set:{chat_id}:{i}")
            ])

        return M(rows)

    def submit_answer(self, uid, cat_idx, text):
        if self.locked:
            return False, "⛔️ زمان پاسخ‌گویی تمام شده."

        if cat_idx < 0 or cat_idx >= len(self.cats):
            return False, "دسته نامعتبر است."

        text = (text or "").strip()

        self.answers.setdefault(uid, {})

        if text in ("-", "حذف"):
            self.answers[uid].pop(cat_idx, None)
            return True, "پاسخ حذف شد."

        self.answers[uid][cat_idx] = text
        return True, "پاسخ ثبت شد."

    def is_complete(self, uid):
        user_answers = self.answers.get(uid, {})
        return bool(self.cats) and all(
            i in user_answers and user_answers[i].strip()
            for i in range(len(self.cats))
        )

    def lock(self):
        self.locked = True

    # ---- اعتبارسنجی واقعی با دیتابیس ----
    def _is_valid_for_cat(self, cat, answer):
        from core import db

        answer = (answer or "").strip()
        if not answer:
            return False

        # باید با حرف مسابقه شروع شود
        if not db.normalize_word(answer).startswith(db.normalize_word(self.letter)):
            return False

        # باید دقیقاً همان دسته‌ی دیتابیس باشد (بدون mapping اضافه)
        return db.word_exists(cat, answer)

    def evaluate(self, players):
        """خروجی:
        {
          uid: {
            "name": ...,
            "total": ...,
            "cells": [
              {"cat":..., "answer":..., "status":..., "points":...}
            ]
          }
        }
        """
        result = {}

        valid_by_cat = {i: {} for i in range(len(self.cats))}

        for uid in players:
            user_answers = self.answers.get(uid, {})
            for i, cat in enumerate(self.cats):
                ans = user_answers.get(i, "").strip()
                if self._is_valid_for_cat(cat, ans):
                    from core import db
                    key = db.normalize_word(ans)
                    valid_by_cat[i].setdefault(key, []).append(uid)

        for uid, info in players.items():
            total = 0
            cells = []
            user_answers = self.answers.get(uid, {})

            for i, cat in enumerate(self.cats):
                ans = user_answers.get(i, "").strip()

                if not ans:
                    status = "⭕"
                    points = PT_EMPTY
                    shown = "—"
                elif not self._is_valid_for_cat(cat, ans):
                    status = "❌"
                    points = PT_INVALID
                    shown = ans
                else:
                    from core import db
                    key = db.normalize_word(ans)
                    duplicated = len(valid_by_cat[i].get(key, [])) > 1

                    if duplicated:
                        status = "🟨"
                        points = PT_SHARED
                    else:
                        status = "✅"
                        points = PT_UNIQUE

                    shown = ans

                total += points
                cells.append({
                    "cat": cat,
                    "answer": shown,
                    "status": status,
                    "points": points,
                })

            result[uid] = {
                "name": info["name"],
                "total": total,
                "cells": cells,
            }

        return result

    def result_text(self, players):
        evaluated = self.evaluate(players)
        ranking = sorted(
            evaluated.items(),
            key=lambda kv: kv[1]["total"],
            reverse=True
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"🏁 <b>نتایج اسم‌وفامیل — حرف «{html.escape(self.letter)}»</b>",
            "━━━━━━━━━━━━━━",
        ]

        for i, (_, data) in enumerate(ranking):
            badge = medals[i] if i < 3 else f"{i + 1}."
            lines.append(
                f"{badge} <b>{html.escape(data['name'])}</b> — {data['total']} امتیاز"
            )

        lines.append("\n📋 <b>جزئیات پاسخ‌ها</b>")
        lines.append("━━━━━━━━━━━━━━")

        for uid, data in ranking:
            lines.append(f"\n👤 <b>{html.escape(data['name'])}</b>")

            for cell in data["cells"]:
                lines.append(
                    f"{html.escape(cell['cat'])}: "
                    f"{cell['status']} {html.escape(cell['answer'])} "
                    f"(+{cell['points']})"
                )

            lines.append(f"⭐ مجموع: <b>{data['total']}</b>")

        return "\n".join(lines)
