# -*- coding: utf-8 -*-
"""Game Session مستقل + Join System + تنظیمات لابی + سیستم زمان.
وضعیت هر گروه در حافظه نگه‌داری می‌شود (یک بازی فعال در هر گروه).
حالت‌ها: lobby -> countdown -> tutorial -> running -> ended

نسخه‌ی Phase 1 (Beta): اصلاحات پایداری تایمر، ضدتکرار سوال، قفل پایان،
و رتبه‌بندی قطعی از طریق game.ranking.
"""
import time
from game.rules import RuleSet
from game.modes import get_mode_class, mode_meta
from core.normalize import normalize_word
from game import ranking

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

# مودهایی که «رد کردن خودکار پس از بی‌پاسخی» ندارند.
NO_AUTOSKIP_MODES = {"classic_random", "classic_choice", "namefamily"}
AUTOSKIP_SECONDS = 20


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
        self.used = set()                  # کلمات پذیرفته‌شده‌ی همین دور (ضدتکرار پاسخ)
        self.focus_mode = False
        self.panel_msg_id = None
        # ---- سیستم زمان ----
        self.time_limit = 300              # پیش‌فرض ۵ دقیقه
        self.started_at = None
        self.deadline = None
        # ---- سختی (برای جای خالی) ----
        self.difficulty = "normal"
        # ---- وظیفه زمان‌بندی اتمام خودکار + وظیفه استاتوس دوره‌ای ----
        self.timer_task = None
        self.status_task = None
        # ---- شناسه پیام زنده‌ی بازی ----
        self.live_msg_id = None
        self.correct_total = 0
        self.wrong_total = 0
        self.correct_by_user = {}
        self.wrong_by_user = {}
        self.warns = {}                    # uid -> تعداد اخطار در همین بازی
        # ---- قفل پایان: جلوگیری از رویداد پایان تکراری (race condition) ----
        self.finishing = False
        # ---- تاریخچه‌ی سوالات: هر سوال فقط یک‌بار تا اتمام همه ----
        self.question_history = set()
        # ---- زمان آخرین فعالیت پاسخ (برای autoskip) ----
        self.last_answer_at = None

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
        self.question_history = set()
        self.ruleset.rules = []
        return True

    def mode_name(self):
        return mode_meta(self.mode_id)["name"]

    def is_round_based(self):
        """مودهایی که به‌جای سوال پیاپی، یک دور جمعی دارند (مثل اسم‌وفامیل)."""
        return self.mode_id == "namefamily"

    def autoskip_enabled(self):
        """آیا این مود پس از ۲۰ثانیه بی‌پاسخی باید خودکار رد شود؟"""
        return self.mode_id not in NO_AUTOSKIP_MODES

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
    def _question_signature(self, q):
        """امضای یکتا برای یک سوال، جهت جلوگیری از تکرار در یک مسابقه."""
        if not isinstance(q, dict):
            return str(q)
        # از prompt به‌عنوان امضا استفاده می‌کنیم (در همه‌ی مودها یکتا و پایدار است).
        return q.get("prompt") or repr(sorted(q.get("answers", [])))

    def next_question(self):
        """سوال بعدی را می‌سازد و از تکرار در همین مسابقه جلوگیری می‌کند.

        باگ قبلی: بعضی مودها ممکن بود سوال تکراری تولید کنند یا در تلاش برای
        اجتناب از تکرار وارد حلقه‌ی بی‌نهایت شوند. اینجا سقف تلاش (bounded loop)
        گذاشته شده تا هرگز فریز نشود؛ اگر همه‌ی سوالات تمام شدند، تاریخچه ریست
        می‌شود تا بازی ادامه یابد.
        """
        MAX_TRIES = 12
        q = None
        for _ in range(MAX_TRIES):
            q = self.mode.new_question()
            sig = self._question_signature(q)
            if sig not in self.question_history:
                self.question_history.add(sig)
                self.question = q
                return q
        # همه‌ی سوالات (تا این‌جا) دیده شده‌اند → تاریخچه را پاک کن و ادامه بده.
        self.question_history.clear()
        if q is not None:
            self.question_history.add(self._question_signature(q))
        self.question = q
        return q

    def submit(self, uid, name, text):
        if self.state != "running" or not self.question:
            return None
        w = text.strip()
        nw = normalize_word(w)
        if uid not in self.players:
            self.players[uid] = {"name": name, "score": 0}
        if nw in self.used:
            # کلمه‌ی تکراری در همین دور — بدون کسر امتیاز، فقط علامت‌گذاری می‌شود.
            return {"ok": False, "reason": "duplicate"}
        ok, reason = self.mode.check_answer(self.question, w)
        if not ok:
            self.wrong_total += 1
            self.wrong_by_user[uid] = self.wrong_by_user.get(uid, 0) + 1
            return {"ok": False, "reason": reason}
        self.used.add(nw)
        self.correct_total += 1
        self.correct_by_user[uid] = self.correct_by_user.get(uid, 0) + 1
        self.last_answer_at = time.time()
        pts = 10
        if self.ruleset.is_active("bonus"):
            pts += 5
        self.players[uid]["score"] += pts
        return {
            "ok": True,
            "points": pts,
            "score": self.players[uid]["score"],
            "found": len(self.used),
            "total": len({normalize_word(x) for x in self.words})
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

    # ---- زمان ----
    def start_timer(self):
        self.started_at = time.time()
        self.last_answer_at = self.started_at
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
        """رتبه‌بندی قطعی از طریق game.ranking (منبع واحد حقیقت).

        باگ قبلی: مرتب‌سازی صرفاً بر اساس score بود و در تساوی نتیجه‌ی
        غیرقطعی می‌داد. حالا tie-break با user_id قطعی است.
        """
        return ranking.sort_players(self.players)


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
    """session را حذف می‌کند و همه‌ی taskهای پس‌زمینه را امن لغو می‌کند."""
    s = _sessions.pop(chat_id, None)
    if s:
        for task in (s.timer_task, s.status_task):
            if task:
                try:
                    task.cancel()
                except Exception:
                    pass
    return s
