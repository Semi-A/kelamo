# -*- coding: utf-8 -*-
"""Game Session مستقل + Join System + تنظیمات لابی + سیستم زمان.
وضعیت هر گروه در حافظه نگه‌داری می‌شود (یک بازی فعال در هر گروه).
حالت‌ها: lobby -> countdown -> tutorial -> running -> ended
"""
import time
from game.rules import RuleSet
from game.modes import get_mode_class, mode_meta
from core.normalize import normalize_word

# گزینه‌های زمان مسابقه (ثانیه) — 0 یعنی نامحدود
TIME_OPTIONS = [
    (120,  "۲ دقیقه"),
    (300,  "۵ دقیقه"),
    (600,  "۱۰ دقیقه"),
    (900,  "۱۵ دقیقه"),
    (0,    "نامحدود"),
]

DIFFICULTY_OPTIONS = [
    ("easy",   "آسان"),
    ("normal", "معمولی"),
    ("hard",   "سخت"),
]


def time_label(seconds):
    for s, lbl in TIME_OPTIONS:
        if s == seconds:
            return lbl
    return "نامحدود"


def difficulty_label(key):
    for k, lbl in DIFFICULTY_OPTIONS:
        if k == key:
            return lbl
    return "معمولی"


class Session:
    def __init__(self, chat_id, host_id, host_name):
        self.chat_id = chat_id
        self.host_id = host_id
        self.host_name = host_name
        self.mode_id = "classic"
        self.ruleset = RuleSet()
        self.players = {}                  # uid -> {"name":.., "score":0}
        self.state = "lobby"
        self.category = ""
        self.words = []
        self.mode = None
        self.question = None
        self.used = set()
        self.focus_mode = False
        self.panel_msg_id = None
        # ---- سیستم زمان ----
        self.time_limit = 300              # پیش‌فرض ۵ دقیقه
        self.started_at = None
        self.deadline = None
        # ---- سختی (برای جای خالی) ----
        self.difficulty = "normal"
        # ---- وظیفه زمان‌بندی اتمام خودکار ----
        self.timer_task = None
        # ---- شناسه پیام زنده‌ی بازی ----
        self.live_msg_id = None
        self.correct_total = 0
        self.wrong_total = 0
        self.correct_by_user = {}
        self.wrong_by_user = {}
        self.warns = {}
        self.warns = {}   # uid -> تعداد اخطار در همین بازی

    def add_warn(self, uid):
        """یک اخطار اضافه می‌کند و تعداد کل اخطارهای کاربر را برمی‌گرداند."""
        self.warns[uid] = self.warns.get(uid, 0) + 1
        return self.warns[uid]
    
    # ---- Join System ----
    def join(self, uid, name):
        if self.state != "lobby":
            return False
        if uid in self.players:
            return False
        self.players[uid] = {"name": name, "score": 0}
        return True

    def is_member(self, uid):
        return uid in self.players

    def player_lines(self):
        if not self.players:
            return "—"
        return "\n".join(f"{i+1}. {p['name']}"
                         for i, p in enumerate(self.players.values()))

    def count(self):
        return len(self.players)

    # ---- mode ----
    def set_mode(self, mode_id):
        from game.modes import REGISTRY
        if mode_id not in REGISTRY:
            return False
        self.mode_id = mode_id
        self.mode = None
        self.question = None
        self.used = set()
        self.ruleset.rules = []
        return True

    def mode_name(self):
        return mode_meta(self.mode_id)["name"]

    def is_round_based(self):
        """مودهایی که به‌جای سوال پیاپی، یک دور جمعی دارند (مثل اسم‌وفامیل)."""
        return self.mode_id == "namefamily"

    def build_mode(self):
        cls = get_mode_class(self.mode_id)
        kwargs = {"ruleset": self.ruleset}
        if self.mode_id in ("classic_random", "classic_choice"):
            kwargs["category"] = self.category
        if self.mode_id == "blank":
            kwargs["difficulty"] = self.difficulty
        self.mode = cls(self.words, **kwargs)
        return self.mode

    # ---- gameplay ----
    def next_question(self):
        self.question = self.mode.new_question()
        return self.question

    def submit(self, uid, name, text):
        if self.state != "running" or not self.question:
            return None
        from core.normalize import normalize_word
        w = text.strip()
        nw = normalize_word(w)
        if uid not in self.players:
            self.players[uid] = {"name": name, "score": 0}
        if nw in self.used:
            self.wrong_total += 1
            self.wrong_by_user[uid] = self.wrong_by_user.get(uid, 0) + 1
            return {"ok": False, "reason": "تکراری"}
        ok, reason = self.mode.check_answer(self.question, w)
        if not ok:
            self.wrong_total += 1
            self.wrong_by_user[uid] = self.wrong_by_user.get(uid, 0) + 1
            return {"ok": False, "reason": reason}
        self.used.add(nw)
        self.correct_total += 1
        self.correct_by_user[uid] = self.correct_by_user.get(uid, 0) + 1
        pts = 10
        if self.ruleset.is_active("bonus"):
            pts += 5
        self.players[uid]["score"] += pts
        return {
            "ok": True,
            "points": pts,
            "score": self.players[uid]["score"],
            "found": len(self.used),
            "total": len({normalize_word(w) for w in self.words})
        }
    
    def progress(self):
        total = len({
            normalize_word(w)
            for w in self.words
            if (w or "").strip()
        })
        found = len(self.used)
        return found, total


    def is_completed(self):
        found, total = self.progress()
        return total > 0 and found >= total


    def add_warn(self, uid):
        self.warns[uid] = self.warns.get(uid, 0) + 1
        return self.warns[uid]


    # ---- زمان ----
    def start_timer(self):
        self.started_at = time.time()
        if self.time_limit > 0:
            self.deadline = self.started_at + self.time_limit
        else:
            self.deadline = None

    def remaining(self):
        if self.deadline is None:
            return None
        return max(0, int(self.deadline - time.time()))

    def remaining_label(self):
        r = self.remaining()
        if r is None:
            return "نامحدود ♾"
        m, s = divmod(r, 60)
        return f"{m:02d}:{s:02d}"

    def leader(self):
        rk = self.ranking()
        if rk and rk[0][1]["score"] > 0:
            return rk[0][1]["name"], rk[0][1]["score"]
        return None

    def ranking(self):
        return sorted(self.players.items(), key=lambda kv: kv[1]["score"], reverse=True)


# ---- رجیستری session‌های فعال ----
_sessions = {}

def get(chat_id):
    return _sessions.get(chat_id)

def exists(chat_id):
    return chat_id in _sessions

def create(chat_id, host_id, host_name, mode_id="classic_random"):
    from game.modes import REGISTRY
    if mode_id not in REGISTRY:
        mode_id = "classic_random"
    s = Session(chat_id, host_id, host_name)
    s.mode_id = mode_id
    _sessions[chat_id] = s
    return s

def remove(chat_id):
    s = _sessions.pop(chat_id, None)
    if s and s.timer_task:
        try:
            s.timer_task.cancel()
        except Exception:
            pass
    return s
