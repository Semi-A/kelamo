# Project Dump


================================================================================
FILE: __init__.py
================================================================================

```py

```


================================================================================
FILE: config.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""پیکربندی Kalemo (کلمو).
مقادیر حساس از متغیرهای محیطی خوانده می‌شوند تا توکن داخل کد قرار نگیرد.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# توکن ربات (از @BotFather) — حتماً به‌صورت متغیر محیطی ست شود.
BOT_TOKEN = os.getenv("KALEMO_BOT_TOKEN")

# آدرس اتصال به PostgreSQL. روی Render مقدار «Internal Database URL» را اینجا بگذارید.
# مثال: postg://user:pass@host:5432/dbname
DATABASE_URL = os.getenv("DATABASE_URL")

# حداکثر اندازه‌ی connection pool. روی پلن رایگان Render کوچک نگه دارید.
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "5"))

# یوزرنیم ربات (بدون @) — برای لینک افزودن به گروه
BOT_USERNAME = os.getenv("KALEMO_BOT_USERNAME")

# آیدی عددی ادمین‌های اصلی (owner). با کاما جدا کنید: "123,456"
ADMIN_IDS = {
    int(x) for x in os.environ.get("KALEMO_ADMINS", "").replace(" ", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

# مسیر دیتابیس SQLite دیگر استفاده نمی‌شود؛ فقط برای سازگاری عقب‌رو نگه داشته شده.
DB_PATH = os.environ.get("KALEMO_DB", "kalemo.db")

# فاصله زمانی مجاز برای تغییر نام نمایشی (ثانیه) — پیش‌فرض ۷ روز
NAME_CHANGE_COOLDOWN = int(os.environ.get("KALEMO_NAME_COOLDOWN", str(7 * 24 * 3600)))

```


================================================================================
FILE: core\__init__.py
================================================================================

```py

```


================================================================================
FILE: core\db.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""لایه دیتابیس Kalemo — نسخه‌ی PostgreSQL.

این فایل جایگزین نسخه‌ی SQLite است. تمام امضاهای توابع عمومی (public API)
دقیقاً مثل قبل باقی مانده‌اند، بنابراین هیچ فایل دیگری در پروژه نیازی به
تغییر ندارد. فقط لایه‌ی ذخیره‌سازی از SQLite به PostgreSQL منتقل شده است.

نکات مهاجرت:
- به‌جای sqlite3 از psycopg (نسخه ۳) استفاده می‌شود.
- کانکشن از طریق یک ConnectionPool مدیریت می‌شود (مناسب پلن رایگان Render).
- placeholder پارامترها از «?» به «%s» تغییر کرده است.
- AUTOINCREMENT → GENERATED / SERIAL (اینجا از GENERATED ALWAYS AS IDENTITY).
- lastrowid → INSERT ... RETURNING id.
- INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING.
- executescript → execute (psycopg چند دستور را در یک رشته اجرا می‌کند).
- خطای یکتایی sqlite3.IntegrityError → psycopg.errors.UniqueViolation.
- PRAGMA table_info → information_schema.columns.
"""

import time
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config
from core.garden_db import init_garden

# سازگاری عقب‌رو: هر جای پروژه که db.IntegrityError را می‌گیرد کار کند.
IntegrityError = psycopg.errors.UniqueViolation

# ---------- connection pool ----------
# روی پلن رایگان Render تعداد کانکشن‌ها محدود است؛ pool کوچک نگه داشته می‌شود.
_DSN = config.DATABASE_URL
if not _DSN:
    raise RuntimeError(
        "DATABASE_URL تنظیم نشده است. در Render → Environment مقدار "
        "Internal Database URL دیتابیس PostgreSQL را ست کنید."
    )

_pool = ConnectionPool(
    conninfo=_DSN,
    min_size=1,
    max_size=int(config.DB_POOL_MAX),
    kwargs={"row_factory": dict_row, "autocommit": False},
    open=True,
)


@contextmanager
def conn():
    """یک کانکشن از pool می‌گیرد، cursor با دسترسی مثل dict برمی‌گرداند.

    برای حفظ سازگاری با کد قدیمی، شیءِ yield شده یک wrapper است که
    متد execute() آن یک cursor برمی‌گرداند (دقیقاً مثل sqlite3.Connection.execute).
    """
    with _pool.connection() as c:
        try:
            yield _ConnShim(c)
            c.commit()
        except Exception:
            c.rollback()
            raise


class _ConnShim:
    """سازگاری با API قدیمی sqlite3.

    در sqlite3، connection.execute(sql, params) خودش یک cursor برمی‌گرداند
    که می‌شود روی آن fetchone/fetchall/rowcount صدا زد. اینجا همان رفتار را
    شبیه‌سازی می‌کنیم تا کوئری‌های موجود بدون تغییر کار کنند.
    """

    def __init__(self, real_conn):
        self._c = real_conn

    def execute(self, sql, params=()):
        # ترجمه‌ی خودکار placeholder «?» به «%s» تا کوئری‌ها دست‌نخورده بمانند.
        if "?" in sql:
            sql = sql.replace("?", "%s")
        cur = self._c.cursor()
        cur.execute(sql, params)
        return cur

    def cursor(self):
        return self._c.cursor()


# ---------- normalization ----------
from core.normalize import normalize_word  # noqa: E402,F401  (سازگاری عقب‌رو)


# ---------- schema helpers ----------

def _table_columns(c, table):
    rows = c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table,),
    ).fetchall()
    return {r["column_name"] for r in rows}


def _ensure_column(c, table, column, ddl):
    if column not in _table_columns(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init():
    with conn() as c:
        # در PostgreSQL کلید افزایشی با IDENTITY ساخته می‌شود.
        c.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id         BIGINT PRIMARY KEY,
            name            TEXT,
            display_name    TEXT,
            name_changed_at BIGINT DEFAULT 0,
            level           INTEGER DEFAULT 1,
            xp              INTEGER DEFAULT 0,
            coins           INTEGER DEFAULT 0,
            streak          INTEGER DEFAULT 0,
            last_login      TEXT DEFAULT '',
            games           INTEGER DEFAULT 0,
            wins            INTEGER DEFAULT 0,
            best_score      INTEGER DEFAULT 0,
            onboarded       INTEGER DEFAULT 0,
            accepted_words  INTEGER DEFAULT 0,
            created_at      BIGINT
        );

        CREATE TABLE IF NOT EXISTS mission_progress (
            user_id   BIGINT,
            day       TEXT,
            progress  INTEGER DEFAULT 0,
            claimed   INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, day)
        );

        CREATE TABLE IF NOT EXISTS categories (
            id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name  TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS words (
            id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            category_id     INTEGER NOT NULL,
            word            TEXT NOT NULL,
            normalized_word TEXT DEFAULT '',
            difficulty      INTEGER DEFAULT 1,
            rarity          INTEGER DEFAULT 1,
            points          INTEGER DEFAULT 10,
            synonyms        TEXT DEFAULT '',
            clue            TEXT DEFAULT '',
            usage_count     INTEGER DEFAULT 0,
            last_used_by    BIGINT,
            last_used_at    BIGINT,
            UNIQUE(category_id, word),
            FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS admins (
            user_id  BIGINT PRIMARY KEY,
            added_by BIGINT,
            added_at BIGINT
        );

        CREATE TABLE IF NOT EXISTS word_suggestions (
            id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id         BIGINT,
            user_name       TEXT,
            word            TEXT NOT NULL,
            normalized_word TEXT DEFAULT '',
            category        TEXT NOT NULL,
            description     TEXT DEFAULT '',
            source          TEXT DEFAULT 'menu',
            status          TEXT DEFAULT 'pending',
            admin_id        BIGINT,
            admin_note      TEXT DEFAULT '',
            created_at      BIGINT,
            reviewed_at     BIGINT
        );

        CREATE TABLE IF NOT EXISTS match_reports (
            id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            chat_id     BIGINT,
            mode        TEXT,
            winner_id   BIGINT,
            players     INTEGER DEFAULT 0,
            created_at  BIGINT
        );

        CREATE TABLE IF NOT EXISTS change_logs (
            id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            admin_id    BIGINT,
            action      TEXT,
            target_type TEXT,
            target_id   TEXT,
            detail      TEXT,
            created_at  BIGINT
        );

        CREATE TABLE IF NOT EXISTS lucky_boxes (
            id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id     BIGINT,
            match_id    BIGINT,
            item_type   TEXT,
            item_value  TEXT,
            rarity      TEXT,
            opened      INTEGER DEFAULT 1,
            created_at  BIGINT
        );
        """)

        # ---- migrations برای دیتابیس‌های قدیمی ----
        _ensure_column(c, "players", "display_name", "display_name TEXT")
        _ensure_column(c, "players", "name_changed_at", "name_changed_at BIGINT DEFAULT 0")
        _ensure_column(c, "players", "accepted_words", "accepted_words INTEGER DEFAULT 0")

        _ensure_column(c, "words", "difficulty", "difficulty INTEGER DEFAULT 1")
        _ensure_column(c, "words", "rarity", "rarity INTEGER DEFAULT 1")
        _ensure_column(c, "words", "points", "points INTEGER DEFAULT 10")
        _ensure_column(c, "words", "synonyms", "synonyms TEXT DEFAULT ''")
        _ensure_column(c, "words", "clue", "clue TEXT DEFAULT ''")
        _ensure_column(c, "words", "normalized_word", "normalized_word TEXT DEFAULT ''")
        _ensure_column(c, "words", "usage_count", "usage_count INTEGER DEFAULT 0")
        _ensure_column(c, "words", "last_used_by", "last_used_by BIGINT")
        _ensure_column(c, "words", "last_used_at", "last_used_at BIGINT")

        c.execute("UPDATE words SET normalized_word='' WHERE normalized_word IS NULL")

        rows = c.execute("SELECT id, word FROM words WHERE normalized_word=''").fetchall()
        for r in rows:
            c.execute(
                "UPDATE words SET normalized_word=%s WHERE id=%s",
                (normalize_word(r["word"]), r["id"]),
            )

        c.execute("CREATE INDEX IF NOT EXISTS ix_words_normalized ON words(category_id, normalized_word)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_suggestions_status ON word_suggestions(status)")

        # partial unique index (نحو یکسان در Postgres)
        c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_players_display_name
        ON players(display_name)
        WHERE display_name IS NOT NULL AND TRIM(display_name) <> ''
        """)

        init_garden(c)

    seed_defaults()
    seed_namefamily_words(clean_extra_categories=True)


# ---------- players ----------

def get_player(uid):
    with conn() as c:
        r = c.execute("SELECT * FROM players WHERE user_id=%s", (uid,)).fetchone()
    return dict(r) if r else None


def get_profile(uid):
    return get_player(uid)


def ensure_player(uid, name):
    p = get_player(uid)
    if p:
        if name and p.get("name") != name:
            with conn() as c:
                c.execute("UPDATE players SET name=%s WHERE user_id=%s", (name, uid))
        return get_player(uid)

    with conn() as c:
        c.execute(
            "INSERT INTO players(user_id, name, created_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO NOTHING",
            (uid, name or "", int(time.time())),
        )
    return get_player(uid)


def save_player(uid, **fields):
    if not fields:
        return

    with conn() as c:
        valid_cols = _table_columns(c, "players")
        bad = [k for k in fields if k not in valid_cols]
        if bad:
            raise ValueError(f"Invalid player field(s): {', '.join(bad)}")

        cols = ", ".join(f"{k}=%s" for k in fields)
        values = list(fields.values())
        values.append(uid)
        c.execute(f"UPDATE players SET {cols} WHERE user_id=%s", values)


def is_onboarded(uid):
    p = get_player(uid)
    return bool(p and p.get("onboarded"))


def mark_onboarded(uid):
    save_player(uid, onboarded=1)


def all_player_ids():
    with conn() as c:
        rows = c.execute("SELECT user_id FROM players").fetchall()
    return [r["user_id"] for r in rows]


def get_display_name(uid):
    p = get_player(uid)
    if not p:
        return None
    dn = (p.get("display_name") or "").strip()
    return dn or None


def display_name(uid, fallback=""):
    return get_display_name(uid) or fallback or f"کاربر {uid}"


def is_display_name_taken(name, exclude_uid=None):
    name = (name or "").strip()
    if not name:
        return False

    with conn() as c:
        if exclude_uid is None:
            r = c.execute(
                "SELECT 1 FROM players WHERE display_name=%s LIMIT 1",
                (name,),
            ).fetchone()
        else:
            r = c.execute(
                "SELECT 1 FROM players WHERE display_name=%s AND user_id<>%s LIMIT 1",
                (name, exclude_uid),
            ).fetchone()
    return r is not None


def name_taken(name, exclude_uid=None):
    return is_display_name_taken(name, exclude_uid=exclude_uid)


def set_display_name(uid, name):
    name = (name or "").strip()
    if not name:
        raise ValueError("display name cannot be empty")

    if is_display_name_taken(name, exclude_uid=uid):
        raise IntegrityError("display name already taken")

    ensure_player(uid, "")
    with conn() as c:
        c.execute(
            "UPDATE players SET display_name=%s, name_changed_at=%s WHERE user_id=%s",
            (name, int(time.time()), uid),
        )


def stats():
    with conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM players").fetchone()["n"]
        games = c.execute("SELECT COALESCE(SUM(games),0) s FROM players").fetchone()["s"]
        wins = c.execute("SELECT COALESCE(SUM(wins),0) s FROM players").fetchone()["s"]
        coins = c.execute("SELECT COALESCE(SUM(coins),0) s FROM players").fetchone()["s"]
        active = c.execute("SELECT COUNT(*) n FROM players WHERE streak>0").fetchone()["n"]
    return {"players": total, "games": games, "wins": wins, "coins": coins, "active": active}


# ---------- missions ----------

def get_mission_progress(uid, day):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM mission_progress WHERE user_id=%s AND day=%s",
            (uid, day),
        ).fetchone()
    return dict(r) if r else {"user_id": uid, "day": day, "progress": 0, "claimed": 0}


def bump_mission(uid, day, amount=1):
    with conn() as c:
        c.execute("""
        INSERT INTO mission_progress(user_id, day, progress)
        VALUES (%s, %s, %s)
        ON CONFLICT(user_id, day)
        DO UPDATE SET progress = mission_progress.progress + %s
        """, (uid, day, amount, amount))


def claim_mission(uid, day):
    with conn() as c:
        c.execute("""
        INSERT INTO mission_progress(user_id, day, claimed)
        VALUES (%s, %s, 1)
        ON CONFLICT(user_id, day)
        DO UPDATE SET claimed = 1
        """, (uid, day))


def claim_mission_atomic(uid, day, coins, xp):
    """اتمیک: اگر قبلاً claim نشده، claim را ثبت و سکه/XP را اعمال می‌کند.
    برمی‌گرداند True اگر جایزه داده شد، False اگر قبلاً گرفته شده بود."""
    from core.progression import add_xp
    with conn() as c:
        row = c.execute(
            "SELECT claimed FROM mission_progress WHERE user_id=%s AND day=%s",
            (uid, day)).fetchone()
        if row and row["claimed"]:
            return False
        c.execute("""INSERT INTO mission_progress(user_id, day, claimed)
                     VALUES (%s, %s, 1)
                     ON CONFLICT(user_id, day) DO UPDATE SET claimed=1""",
                  (uid, day))
        p = c.execute("SELECT level, xp, coins FROM players WHERE user_id=%s",
                      (uid,)).fetchone()
        if p:
            new_level, new_xp, _ = add_xp(p["level"], p["xp"], xp)
            c.execute("UPDATE players SET coins=%s, level=%s, xp=%s WHERE user_id=%s",
                      (p["coins"] + coins, new_level, new_xp, uid))
    return True


# ---------- leaderboard ----------

def top_players(limit=10):
    """لیدربورد کلی بر اساس best_score.

    مرتب‌سازی قطعی (deterministic) طبق game.ranking:
      1) best_score نزولی  2) wins نزولی  3) user_id صعودی (ثبت‌نام زودتر)
    این تضمین می‌کند بازیکن با امتیاز کمتر هرگز بالاتر از بازیکن با امتیاز بیشتر
    نمایش داده نشود، و در تساوی همیشه ترتیب یکسان و قطعی باشد.
    خروجی نهایی قبل از برگشت با ranking.assert_sorted اعتبارسنجی می‌شود.
    """
    from game import ranking
    with conn() as c:
        rows = c.execute("""
        SELECT
            COALESCE(NULLIF(display_name, ''), name, 'کاربر') AS shown_name,
            best_score,
            wins,
            user_id
        FROM players
        ORDER BY best_score DESC, wins DESC, user_id ASC
        LIMIT %s
        """, (limit,)).fetchall()

    result = [(r["shown_name"], r["best_score"]) for r in rows]
    # گارد نهایی: اگر به هر دلیلی ترتیب خراب بود، همین‌جا شکست می‌خورد.
    ranking.assert_sorted(result, score_getter=lambda t: t[1])
    return result


# ---------- categories & words ----------

def add_category(name):
    name = (name or "").strip()
    if not name:
        return False

    try:
        with conn() as c:
            c.execute("INSERT INTO categories(name) VALUES (%s)", (name,))
        return True
    except IntegrityError:
        return False


def del_category(name):
    with conn() as c:
        cur = c.execute("DELETE FROM categories WHERE name=%s", ((name or "").strip(),))
        return cur.rowcount > 0


def get_category(name):
    with conn() as c:
        r = c.execute("SELECT * FROM categories WHERE name=%s", ((name or "").strip(),)).fetchone()
    return dict(r) if r else None


def list_categories():
    with conn() as c:
        rows = c.execute("""
        SELECT cat.name,
               (SELECT COUNT(*) FROM words w WHERE w.category_id=cat.id) cnt
        FROM categories cat
        ORDER BY cat.name
        """).fetchall()
    return [(r["name"], r["cnt"]) for r in rows]


def find_word(category, word):
    """جستجوی کلمه با نرمال‌سازی، در یک دسته‌ی مشخص."""
    cat = get_category(category)
    if not cat:
        return None

    nw = normalize_word(word)

    with conn() as c:
        r = c.execute("""
            SELECT *
            FROM words
            WHERE category_id=%s AND normalized_word=%s
            LIMIT 1
        """, (cat["id"], nw)).fetchone()

    return dict(r) if r else None


def word_exists(category, word):
    return find_word(category, word) is not None


def add_word(category, word, difficulty=1, rarity=1, points=10, synonyms="", clue=""):
    category = (category or "").strip()
    word = (word or "").strip()

    if not category or not word:
        return False

    cat = get_category(category)
    if not cat:
        if not add_category(category):
            return False
        cat = get_category(category)

    if find_word(category, word):
        return False

    if isinstance(synonyms, (list, tuple, set)):
        synonyms = "،".join(str(x).strip() for x in synonyms if str(x).strip())

    try:
        with conn() as c:
            c.execute("""
                INSERT INTO words(
                    category_id, word, normalized_word,
                    difficulty, rarity, points, synonyms, clue
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                cat["id"],
                word,
                normalize_word(word),
                int(difficulty or 1),
                int(rarity or 1),
                int(points or 10),
                synonyms or "",
                clue or "",
            ))
        return True
    except IntegrityError:
        return False


def del_word(category, word):
    cat = get_category(category)
    if not cat:
        return False

    with conn() as c:
        cur = c.execute(
            "DELETE FROM words WHERE category_id=%s AND word=%s",
            (cat["id"], (word or "").strip()),
        )
        return cur.rowcount > 0


def list_words(category):
    cat = get_category(category)
    if not cat:
        return None

    with conn() as c:
        rows = c.execute(
            "SELECT word FROM words WHERE category_id=%s ORDER BY word",
            (cat["id"],),
        ).fetchall()

    return [r["word"] for r in rows]


def lex_rows(category):
    cat = get_category(category)
    if not cat:
        return []

    with conn() as c:
        rows = c.execute("""
        SELECT word, difficulty, rarity, points, synonyms, clue, usage_count
        FROM words
        WHERE category_id=%s
        ORDER BY word
        """, (cat["id"],)).fetchall()

    return [dict(r) for r in rows]


def clue_pool():
    with conn() as c:
        rows = c.execute("""
        SELECT w.word, w.clue, c.name AS category
        FROM words w
        JOIN categories c ON c.id = w.category_id
        WHERE TRIM(COALESCE(w.clue, '')) <> ''
        ORDER BY w.usage_count ASC, w.word ASC
        """).fetchall()

    return [dict(r) for r in rows]


def bump_word_use(category, word, user_id=None):
    cat = get_category(category)
    if not cat:
        return False

    with conn() as c:
        cur = c.execute("""
        UPDATE words
        SET usage_count = COALESCE(usage_count, 0) + 1,
            last_used_by = %s,
            last_used_at = %s
        WHERE category_id=%s AND word=%s
        """, (user_id, int(time.time()), cat["id"], (word or "").strip()))
        return cur.rowcount > 0


def random_category():
    import random

    cats = [n for n, cnt in list_categories() if cnt > 0]
    return random.choice(cats) if cats else None


def import_words(records):
    added = 0
    skipped = 0
    with conn() as c:
        for r in records:
            if not isinstance(r, dict):
                skipped += 1
                continue
            word = (r.get("word") or r.get("کلمه") or "").strip()
            category = (r.get("category") or r.get("cat") or r.get("دسته") or "").strip()
            if not word or not category:
                skipped += 1
                continue
            cat = c.execute("SELECT id FROM categories WHERE name=%s", (category,)).fetchone()
            if not cat:
                try:
                    c.execute("INSERT INTO categories(name) VALUES (%s)", (category,))
                    cat_id = c.execute("SELECT id FROM categories WHERE name=%s", (category,)).fetchone()["id"]
                except IntegrityError:
                    skipped += 1
                    continue
            else:
                cat_id = cat["id"]
            nw = normalize_word(word)
            exists = c.execute(
                "SELECT 1 FROM words WHERE category_id=%s AND normalized_word=%s LIMIT 1",
                (cat_id, nw)).fetchone()
            if exists:
                skipped += 1
                continue
            try:
                c.execute("""INSERT INTO words(category_id, word, normalized_word,
                             difficulty, rarity, points, synonyms, clue)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (cat_id, word, nw,
                           int(r.get("difficulty", 1) or 1),
                           int(r.get("rarity", 1) or 1),
                           int(r.get("points", 10) or 10),
                           r.get("synonyms", "") or "",
                           r.get("clue", "") or ""))
                added += 1
            except IntegrityError:
                skipped += 1
    return added, skipped


def seed_defaults():
    if list_categories():
        return

    seed = {
        "خوراکی": ["سیب", "نان", "پنیر", "ماست", "خرما", "کباب", "قورمه", "آش"],
        "حیوانات": ["شیر", "ببر", "گربه", "اسب", "فیل", "روباه", "خرگوش", "عقاب"],
        "شهرها": ["تهران", "شیراز", "اصفهان", "تبریز", "مشهد", "یزد", "رشت", "اهواز"],
        "ورزش": ["فوتبال", "والیبال", "شنا", "دو", "کشتی", "بسکتبال", "تنیس", "اسکی"],
    }

    for cat, words in seed.items():
        add_category(cat)
        for w in words:
            add_word(cat, w)


# ---------- word suggestions ----------

def add_suggestion(user_id, user_name, word, category, description="", source="menu"):
    word = (word or "").strip()
    category = (category or "").strip()
    description = (description or "").strip()

    if not word or not category:
        return False

    if word_exists(category, word):
        return False

    with conn() as c:
        cat = get_category(category)
        cat_id = cat["id"] if cat else None

        if cat_id:
            dup = c.execute(
                "SELECT 1 FROM words WHERE category_id=%s AND normalized_word=%s LIMIT 1",
                (cat_id, normalize_word(word))
            ).fetchone()

            if dup:
                return False

        pending = c.execute(
            """SELECT 1 FROM word_suggestions
               WHERE category=%s AND normalized_word=%s
               AND status='pending' LIMIT 1""",
            (category, normalize_word(word))
        ).fetchone()

        if pending:
            return False

        c.execute("""
            INSERT INTO word_suggestions(
                user_id, user_name, word, normalized_word,
                category, description, source, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
        """, (
            user_id,
            user_name or "",
            word,
            normalize_word(word),
            category,
            description,
            source,
            int(time.time())
        ))

    return True


def pending_suggestions(limit=10):
    with conn() as c:
        rows = c.execute("""
            SELECT *
            FROM word_suggestions
            WHERE status='pending'
            ORDER BY created_at ASC
            LIMIT %s
        """, (limit,)).fetchall()

    return [dict(r) for r in rows]


def get_suggestion(sid):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM word_suggestions WHERE id=%s",
            (sid,)
        ).fetchone()

    return dict(r) if r else None


def approve_suggestion(sid, admin_id, new_word=None, new_category=None):
    s = get_suggestion(sid)
    if not s or s["status"] != "pending":
        return False, "پیشنهاد پیدا نشد یا قبلاً بررسی شده."

    word = (new_word or s["word"]).strip()
    category = (new_category or s["category"]).strip()
    nw = normalize_word(word)
    now = int(time.time())

    with conn() as c:
        cat = c.execute("SELECT id FROM categories WHERE name=%s", (category,)).fetchone()
        if not cat:
            c.execute("INSERT INTO categories(name) VALUES (%s)", (category,))
            cat_id = c.execute("SELECT id FROM categories WHERE name=%s", (category,)).fetchone()["id"]
        else:
            cat_id = cat["id"]

        dup = c.execute("SELECT 1 FROM words WHERE category_id=%s AND normalized_word=%s LIMIT 1",
                        (cat_id, nw)).fetchone()
        ok = False
        if not dup:
            try:
                c.execute("""INSERT INTO words(category_id, word, normalized_word)
                             VALUES (%s,%s,%s)""", (cat_id, word, nw))
                ok = True
            except IntegrityError:
                ok = False

        c.execute("""UPDATE word_suggestions
                     SET status='approved', word=%s, normalized_word=%s, category=%s,
                         admin_id=%s, reviewed_at=%s WHERE id=%s""",
                  (word, nw, category, admin_id, now, sid))
        c.execute("UPDATE players SET accepted_words=COALESCE(accepted_words,0)+1 WHERE user_id=%s",
                  (s["user_id"],))
        c.execute("""INSERT INTO change_logs(admin_id, action, target_type, target_id, detail, created_at)
                     VALUES (%s, 'approve_suggestion', 'word_suggestion', %s, %s, %s)""",
                  (admin_id, str(sid), f"{word} -> {category}, inserted={ok}", now))

    return True, "پیشنهاد تأیید شد و کلمه به دیتابیس اضافه شد."


def reject_suggestion(sid, admin_id, note=""):
    s = get_suggestion(sid)
    if not s or s["status"] != "pending":
        return False

    with conn() as c:
        c.execute("""
            UPDATE word_suggestions
            SET status='rejected',
                admin_id=%s,
                admin_note=%s,
                reviewed_at=%s
            WHERE id=%s
        """, (
            admin_id,
            note or "",
            int(time.time()),
            sid
        ))

        c.execute("""
            INSERT INTO change_logs(admin_id, action, target_type, target_id, detail, created_at)
            VALUES (%s, 'reject_suggestion', 'word_suggestion', %s, %s, %s)
        """, (
            admin_id,
            str(sid),
            note or "",
            int(time.time())
        ))

    return True


def edit_suggestion(sid, admin_id, word=None, category=None, description=None):
    s = get_suggestion(sid)
    if not s or s["status"] != "pending":
        return False

    new_word = (word or s["word"]).strip()
    new_category = (category or s["category"]).strip()
    new_description = description if description is not None else s["description"]

    with conn() as c:
        c.execute("""
            UPDATE word_suggestions
            SET word=%s,
                normalized_word=%s,
                category=%s,
                description=%s,
                admin_id=%s
            WHERE id=%s
        """, (
            new_word,
            normalize_word(new_word),
            new_category,
            new_description or "",
            admin_id,
            sid
        ))

        c.execute("""
            INSERT INTO change_logs(admin_id, action, target_type, target_id, detail, created_at)
            VALUES (%s, 'edit_suggestion', 'word_suggestion', %s, %s, %s)
        """, (
            admin_id,
            str(sid),
            f"{new_word} -> {new_category}",
            int(time.time())
        ))

    return True


def suggestion_stats_for_user(uid):
    with conn() as c:
        total = c.execute("""
            SELECT COUNT(*) n
            FROM word_suggestions
            WHERE user_id=%s
        """, (uid,)).fetchone()["n"]

        approved = c.execute("""
            SELECT COUNT(*) n
            FROM word_suggestions
            WHERE user_id=%s AND status='approved'
        """, (uid,)).fetchone()["n"]

    return {"total": total, "approved": approved}


# ---------- match reports ----------

def add_match_report(chat_id, mode, winner_id, players_count):
    with conn() as c:
        cur = c.execute("""
            INSERT INTO match_reports(chat_id, mode, winner_id, players, created_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            chat_id,
            mode,
            winner_id,
            players_count,
            int(time.time())
        ))
        return cur.fetchone()["id"]


def latest_match_reports(limit=10):
    with conn() as c:
        rows = c.execute("""
            SELECT *
            FROM match_reports
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,)).fetchall()

    return [dict(r) for r in rows]


# ---------- lucky box ----------

def add_lucky_box(user_id, match_id, item_type, item_value, rarity):
    with conn() as c:
        c.execute("""
            INSERT INTO lucky_boxes(user_id, match_id, item_type, item_value, rarity, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            match_id,
            item_type,
            str(item_value),
            rarity,
            int(time.time())
        ))


# ---------- admins ----------

def is_db_admin(uid):
    with conn() as c:
        r = c.execute("SELECT 1 FROM admins WHERE user_id=%s", (uid,)).fetchone()
    return r is not None


def add_admin(uid, by):
    with conn() as c:
        c.execute("""
        INSERT INTO admins(user_id, added_by, added_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING
        """, (uid, by, int(time.time())))


def del_admin(uid):
    with conn() as c:
        cur = c.execute("DELETE FROM admins WHERE user_id=%s", (uid,))
        return cur.rowcount > 0


def list_admins():
    with conn() as c:
        rows = c.execute("SELECT user_id FROM admins ORDER BY added_at DESC").fetchall()
    return [r["user_id"] for r in rows]


# ---------- NameFamily fixed categories seed ----------

NAMEFAMILY_ALLOWED_CATEGORIES = ["غذا", "رنگ", "میوه", "حیوان", "اشیا", "عضو بدن", "شهر", "کشور", "شغل"]

NAMEFAMILY_WORD_BANK = {
    "غذا": ["آب", "آبگوشت", "آش", "آش رشته", "آش دوغ", "آجیل", "املت", "برنج", "باقالی پلو", "بستنی", "بیسکویت", "پاستا", "پنیر", "پیتزا", "تخم مرغ", "ترشی", "ته چین", "جوجه کباب", "چای", "چلوکباب", "چلوگوشت", "چیپس", "حلوا", "حلیم", "حمص", "خوراک لوبیا", "خوراک مرغ", "خورشت آلو", "خورشت به", "خورشت کرفس", "خرما", "دلمه", "دوغ", "دونات", "دمپختک", "رولت", "زرشک پلو", "ژله", "سالاد", "سالاد الویه", "ساندویچ", "سوپ", "سوشی", "سوهان", "شامی", "شله زرد", "شیر", "شیرینی", "شکلات", "عدس پلو", "عدسی", "عسل", "فسنجان", "فلافل", "فرنی", "قهوه", "قورمه سبزی", "قیمه", "قطاب", "کباب", "کباب کوبیده", "کشک بادمجان", "کتلت", "کیک", "کلوچه", "کله پاچه", "کوکو", "کمپوت", "گز", "لازانیا", "لوبیا پلو", "ماست", "ماکارونی", "مربا", "مرصع پلو", "میرزاقاسمی", "نان", "نان بربری", "نان تافتون", "نان سنگک", "نان لواش", "نوشابه", "وافل", "یتیمچه"],
    "رنگ": ["آبی", "آبی آسمانی", "آبی کبالت", "آبی نفتی", "آجری", "آکوامارین", "آلبالویی", "ارغوانی", "استخوانی", "اسطوخودوسی", "بادمجانی", "بژ", "بنفش", "بورگاندی", "پسته ای", "خاکستری", "خاکی", "خردلی", "دودی", "رزگلد", "زرشکی", "زرد", "زیتونی", "سبز", "سبز آبی", "سبز چمنی", "سبز زمردی", "سدری", "سرخابی", "سرمه ای", "سفید", "سیاه", "شامپاینی", "صدفی", "صورتی", "طاووسی", "طلایی", "عنابی", "فیروزه ای", "قرمز", "قهوه ای", "کبالت", "کرم", "کاراملی", "کهربایی", "گرافیتی", "لاجوردی", "لیمویی", "مسی", "مرجانی", "مرمری", "مشکی", "موشی", "ماشی", "نارنجی", "نخودی", "نقره ای", "نیلی", "یاسی", "یشمی"],
    "میوه": ["آلبالو", "آلو", "آلوچه", "آناناس", "انار", "انبه", "انجیر", "انگور", "ازگیل", "بالنگ", "به", "پاپایا", "پرتقال", "تمشک", "توت", "توت فرنگی", "خرمالو", "خرما", "خیار", "دارابی", "ذغال اخته", "زرشک", "زالزالک", "زردآلو", "سنجد", "سیب", "شاه توت", "شلیل", "طالبی", "عناب", "غوره", "گریپ فروت", "گلابی", "گوجه سبز", "گیلاس", "لیمو", "لیمو ترش", "لیمو شیرین", "موز", "نارگیل", "نارنج", "نارنگی", "هلو", "هندوانه", "کیوی", "کامکوات", "کنار"],
    "حیوان": ["آهو", "آفتاب پرست", "آناکوندا", "اسب", "اسب آبی", "اختاپوس", "اردک", "الاغ", "ایگوانا", "ببر", "بز", "بوفالو", "تمساح", "جغد", "خر", "خرچنگ", "خرس", "خرگوش", "خفاش", "دلفین", "راکون", "راسو", "روباه", "زرافه", "زنبور", "سگ", "سنجاب", "سمندر", "سوسک", "سوسمار", "شامپانزه", "شاهین", "شتر", "شترمرغ", "شیر", "طاووس", "طوطی", "عقاب", "عقرب", "غاز", "فیل", "فلامینگو", "قناری", "قورباغه", "قو", "کبوتر", "کبرا", "کرم", "کرگدن", "کفتار", "کلاغ", "کوسه", "کوالا", "گاو", "گربه", "گوسفند", "گنجشک", "گوزن", "گورخر", "گرگ", "لاک پشت", "لاما", "مار", "مارمولک", "ماهی", "مرغ", "مگس", "ملخ", "میمون", "مورچه", "نهنگ", "یوزپلنگ"],
    "اشیا": ["آچار", "آینه", "اتو", "اجاق", "اره", "اره برقی", "اسکنر", "انبردست", "بالش", "باتری", "بشقاب", "بطری", "پتو", "پرده", "پرینتر", "پنجره", "پیچ گوشتی", "تابه", "تخت", "تلویزیون", "تلسکوپ", "جارو", "جاروبرقی", "جعبه", "چراغ", "چراغ قوه", "چاقو", "چتر", "چکش", "چمدان", "چنگال", "خودکار", "در", "دریل", "دفتر", "دوربین", "دکمه", "رادیو", "رایانه", "روتر", "زیپ", "ساعت", "سطل", "سشوار", "سوزن", "سه پایه", "شارژر", "شانه", "صندلی", "ظرف", "عینک", "فرش", "فشارسنج", "فلش مموری", "قابلمه", "قالی", "قاشق", "قفل", "قیچی", "قطب نما", "کابل", "کارت گرافیک", "کاغذ", "کلاه", "کلید", "کمد", "کتاب", "کفش", "کیبورد", "کیف", "گلدان", "گوشی", "لیوان", "لپ تاپ", "لباس", "مایکروویو", "ماشین لباسشویی", "مادربرد", "ماوس", "مداد", "میز", "میکروسکوپ", "مودم", "مانیتور", "نخ", "نردبان", "هدفون", "هارددیسک", "یخچال"],
    "عضو بدن": ["آرنج", "ابرو", "استخوان", "انگشت", "بازو", "بافت", "بینی", "پا", "پاشنه", "پوست", "پیشانی", "تاندون", "ترقوه", "جمجمه", "چانه", "چشم", "حنجره", "حلق", "خون", "دست", "دندان", "دل", "دهان", "رگ", "رباط", "ریه", "زانو", "زبان", "ستون فقرات", "سر", "شبکیه", "شانه", "طحال", "عصب", "عضله", "غضروف", "قلب", "قرنیه", "کبد", "کتف", "کف دست", "کلیه", "کمر", "گونه", "گوش", "گردن", "لب", "لوزالمعده", "مچ", "مردمک", "مری", "معده", "مغز", "مخچه", "مفصل", "مو", "مویرگ", "ناخن", "نای"],
    "شهر": ["آبادان", "آستارا", "آمل", "اردبیل", "اراک", "ارومیه", "اصفهان", "اهواز", "ایلام", "انزلی", "بابل", "بابلسر", "بانه", "بجنورد", "بروجرد", "بم", "بندرعباس", "بوشهر", "بیرجند", "بهبهان", "تبریز", "تنکابن", "تهران", "جیرفت", "چابهار", "چالوس", "خرم آباد", "خرمشهر", "خوی", "دامغان", "دزفول", "رامسر", "رشت", "رفسنجان", "زاهدان", "زنجان", "ساری", "ساوه", "سبزوار", "سقز", "سنندج", "سیرجان", "شاهرود", "شاهین شهر", "شهرکرد", "شیراز", "قائم شهر", "قائن", "قزوین", "قم", "قشم", "کاشان", "کرج", "کرمان", "کرمانشاه", "کیش", "گرگان", "لاهیجان", "لنگرود", "محلات", "مراغه", "مرند", "مشهد", "ملایر", "مهاباد", "میناب", "نهاوند", "نیشابور", "همدان", "یزد", "یاسوج"],
    "کشور": ["آذربایجان", "آرژانتین", "آلمان", "آمریکا", "اتریش", "اردن", "ارمنستان", "استرالیا", "اسپانیا", "اسلواکی", "اسلوونی", "افغانستان", "امارات", "اندونزی", "انگلیس", "ایران", "ایتالیا", "ایسلند", "بحرین", "برزیل", "بلژیک", "بلغارستان", "بنگلادش", "بوتان", "بوتسوانا", "بوسنی", "پاکستان", "پرتغال", "پرو", "تاجیکستان", "تایلند", "ترکمنستان", "ترکیه", "چین", "دانمارک", "روسیه", "رومانی", "ژاپن", "سوریه", "سوئد", "سوئیس", "سنگال", "عراق", "عمان", "فرانسه", "فنلاند", "فیلیپین", "قطر", "قرقیزستان", "قزاقستان", "کانادا", "کامبوج", "کلمبیا", "کره", "کویت", "گرجستان", "لبنان", "لائوس", "لهستان", "ماداگاسکار", "مالزی", "مصر", "مکزیک", "مغولستان", "موزامبیک", "نروژ", "نپال", "نیوزیلند", "هلند", "هند", "ویتنام", "یمن", "یونان"],
    "شغل": ["آتش نشان", "آرایشگر", "آشپز", "استاد", "اقتصاددان", "بازیگر", "بازاریاب", "باغبان", "باستان شناس", "برنامه نویس", "برق کار", "پرستار", "پلیس", "پزشک", "تحلیلگر", "تدوینگر", "جراح", "خبرنگار", "خلبان", "خیاط", "داده کاو", "دامپزشک", "داروساز", "دندان پزشک", "راننده", "روان شناس", "روزنامه نگار", "زیست شناس", "ستاره شناس", "سرباز", "صندوقدار", "صدابردار", "طراح", "طراح تجربه کاربر", "عکاس", "فیلمبردار", "فروشنده", "قاضی", "کارآفرین", "کارگردان", "کارگر", "کارشناس امنیت", "کشاورز", "کتابدار", "گرافیست", "لوله کش", "مترجم", "مدیر", "مدیر محصول", "مربی", "ملوان", "منشی", "مهندس", "معمار", "معلم", "مکانیک", "نانوا", "نجار", "نقاش", "نگهبان", "نورپرداز", "نویسنده", "ورزشکار", "وکیل", "هواشناس"]
}


def seed_namefamily_words(clean_extra_categories=True):
    allowed = set(NAMEFAMILY_ALLOWED_CATEGORIES)
    with conn() as c:
        if clean_extra_categories:
            rows = c.execute("SELECT name FROM categories").fetchall()
            for r in rows:
                if r["name"] not in allowed:
                    c.execute("DELETE FROM categories WHERE name=%s", (r["name"],))
        for cat in NAMEFAMILY_ALLOWED_CATEGORIES:
            c.execute("INSERT INTO categories(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (cat,))
            cat_id = c.execute("SELECT id FROM categories WHERE name=%s", (cat,)).fetchone()["id"]
            for word in NAMEFAMILY_WORD_BANK.get(cat, []):
                w = (word or "").strip()
                if not w:
                    continue
                nw = normalize_word(w)
                exists = c.execute("SELECT 1 FROM words WHERE category_id=%s AND normalized_word=%s LIMIT 1", (cat_id, nw)).fetchone()
                if exists:
                    continue
                c.execute("""
                    INSERT INTO words(category_id, word, normalized_word, difficulty, rarity, points)
                    VALUES (%s, %s, %s, 1, 1, 10)
                    ON CONFLICT (category_id, word) DO NOTHING
                """, (cat_id, w, nw))
    return True


# ---------- garden (delegation) ----------
from core.garden_db import GardenAPI as _GardenAPI  # noqa: E402
_garden = _GardenAPI(conn)

def garden_ensure_starter(uid, name=""):        return _garden.ensure_starter(uid, name)
def garden_add_growth(uid, amount, source="", detail=""):
                                                return _garden.add_growth(uid, amount, source, detail)
def garden_add_seed(uid, seed_type=None, qty=1, source=""):
                                                return _garden.add_seed(uid, seed_type, qty, source)
def garden_random_seed_type():                  return _garden.random_seed_type()
def garden_daily_visit(uid):                    return _garden.daily_visit(uid)
def garden_seed_inventory(uid):                 return _garden.seed_inventory(uid)
def garden_plant_seed(uid, seed_type):          return _garden.plant_seed(uid, seed_type)
def garden_harvest(uid):                        return _garden.harvest(uid)
def garden_water_left(uid):                     return _garden.water_left(uid)
def garden_water(uid, target_id):               return _garden.water(uid, target_id)
def garden_public(uid):                         return _garden.public(uid)
def garden_friend_gardens(uid, limit=8):        return _garden.friend_gardens(uid, limit)

```


================================================================================
FILE: core\garden_db.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""لایه دیتابیس باغچه‌ی کلمو — نسخه‌ی PostgreSQL.

فقط از conn() هسته استفاده می‌کند. تمام امضاهای عمومی بدون تغییر مانده‌اند.

تغییرات مهاجرت نسبت به نسخه‌ی SQLite:
- executescript → execute (psycopg چند دستور در یک رشته را اجرا می‌کند).
- placeholder «?» → «%s».
- در UPSERT، ستون‌های جدول باید با نام جدول واجد شرایط شوند
  (مثلاً garden_seeds.qty) چون در Postgres «qty» به‌تنهایی مبهم است.
- AUTOINCREMENT وجود ندارد؛ این جدول‌ها کلید طبیعی دارند (نیازی نیست).
- BIGINT برای user_id (آی‌دی‌های تلگرام بزرگ هستند).
"""
import random
import time

SEED_TYPES = ["کلمو", "شکوفه", "یاقوت", "طلایی"]
RARITY_BY_SEED = {"کلمو": "normal", "شکوفه": "blossom", "یاقوت": "rare", "طلایی": "golden"}
DAILY_WATER_QUOTA = 5
HARVEST_AT = 100


def init_garden(c):
    """جدول‌ها را می‌سازد. c یک اتصال باز از conn() است."""
    c.execute("""
    CREATE TABLE IF NOT EXISTS garden_players (
        user_id     BIGINT PRIMARY KEY,
        name        TEXT DEFAULT '',
        last_visit  TEXT DEFAULT '',
        water_day   TEXT DEFAULT '',
        water_used  INTEGER DEFAULT 0,
        created_at  BIGINT
    );
    CREATE TABLE IF NOT EXISTS garden_trees (
        user_id        BIGINT PRIMARY KEY,
        seed_type      TEXT DEFAULT 'کلمو',
        rarity         TEXT DEFAULT 'normal',
        growth         INTEGER DEFAULT 0,
        pending_coins  INTEGER DEFAULT 0,
        pending_xp     INTEGER DEFAULT 0,
        pending_boxes  INTEGER DEFAULT 0,
        planted_at     BIGINT
    );
    CREATE TABLE IF NOT EXISTS garden_seeds (
        user_id    BIGINT,
        seed_type  TEXT,
        qty        INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, seed_type)
    );
    """)


def _today():
    import datetime
    return datetime.date.today().isoformat()


def random_seed_type():
    return random.choices(SEED_TYPES, weights=[60, 22, 13, 5], k=1)[0]


class GardenAPI:
    """با یک تابع conn (context manager) مقداردهی می‌شود."""
    def __init__(self, conn_factory):
        self._conn = conn_factory

    # ---- setup ----
    def ensure_starter(self, uid, name=""):
        with self._conn() as c:
            init_garden(c)
            row = c.execute("SELECT 1 FROM garden_players WHERE user_id=%s", (uid,)).fetchone()
            if not row:
                c.execute("INSERT INTO garden_players(user_id, name, created_at) VALUES (%s,%s,%s) "
                          "ON CONFLICT (user_id) DO NOTHING",
                          (uid, name or "", int(time.time())))
                # بذر شروع
                c.execute("""INSERT INTO garden_seeds(user_id, seed_type, qty) VALUES (%s, 'کلمو', 1)
                             ON CONFLICT(user_id, seed_type)
                             DO UPDATE SET qty = garden_seeds.qty + 1""", (uid,))
            elif name:
                c.execute("UPDATE garden_players SET name=%s WHERE user_id=%s", (name, uid))

    # ---- growth / seeds ----
    def add_growth(self, uid, amount, source="", detail=""):
        self.ensure_starter(uid)
        with self._conn() as c:
            tree = c.execute("SELECT growth FROM garden_trees WHERE user_id=%s", (uid,)).fetchone()
            if not tree:
                return  # درختی کاشته نشده
            new_growth = min(HARVEST_AT, int(tree["growth"]) + int(amount))
            grew = new_growth - int(tree["growth"])
            # هر واحد رشد → سکه/xp در انتظار برداشت
            c.execute("""UPDATE garden_trees
                         SET growth=%s, pending_coins=pending_coins+%s, pending_xp=pending_xp+%s
                         WHERE user_id=%s""",
                      (new_growth, grew * 2, grew, uid))

    def add_seed(self, uid, seed_type=None, qty=1, source=""):
        self.ensure_starter(uid)
        st = seed_type or random_seed_type()
        with self._conn() as c:
            c.execute("""INSERT INTO garden_seeds(user_id, seed_type, qty) VALUES (%s,%s,%s)
                         ON CONFLICT(user_id, seed_type)
                         DO UPDATE SET qty = garden_seeds.qty + %s""",
                      (uid, st, qty, qty))
        return st

    def random_seed_type(self):
        return random_seed_type()

    def daily_visit(self, uid):
        self.ensure_starter(uid)
        today = _today()
        with self._conn() as c:
            row = c.execute("SELECT last_visit FROM garden_players WHERE user_id=%s", (uid,)).fetchone()
            if row and row["last_visit"] == today:
                return False
            c.execute("UPDATE garden_players SET last_visit=%s WHERE user_id=%s", (today, uid))
        self.add_growth(uid, 5, source="daily_visit")
        return True

    # ---- inventory / planting ----
    def seed_inventory(self, uid):
        self.ensure_starter(uid)
        with self._conn() as c:
            rows = c.execute("""SELECT seed_type, qty FROM garden_seeds
                                WHERE user_id=%s AND qty>0 ORDER BY seed_type""", (uid,)).fetchall()
        return [dict(r) for r in rows]

    def plant_seed(self, uid, seed_type):
        self.ensure_starter(uid)
        with self._conn() as c:
            existing = c.execute("SELECT growth FROM garden_trees WHERE user_id=%s", (uid,)).fetchone()
            if existing and int(existing["growth"]) < HARVEST_AT:
                return False, "یه درخت در حال رشد داری. اول برداشتش کن."
            seed = c.execute("SELECT qty FROM garden_seeds WHERE user_id=%s AND seed_type=%s",
                             (uid, seed_type)).fetchone()
            if not seed or int(seed["qty"]) <= 0:
                return False, "این بذر رو نداری."
            c.execute("UPDATE garden_seeds SET qty=qty-1 WHERE user_id=%s AND seed_type=%s",
                      (uid, seed_type))
            rarity = RARITY_BY_SEED.get(seed_type, "normal")
            c.execute("""INSERT INTO garden_trees(user_id, seed_type, rarity, growth,
                         pending_coins, pending_xp, pending_boxes, planted_at)
                         VALUES (%s,%s,%s,0,0,0,0,%s)
                         ON CONFLICT(user_id) DO UPDATE SET
                            seed_type=excluded.seed_type, rarity=excluded.rarity,
                            growth=0, pending_coins=0, pending_xp=0, pending_boxes=0,
                            planted_at=excluded.planted_at""",
                      (uid, seed_type, rarity, int(time.time())))
        return True, f"بذر «{seed_type}» کاشته شد 🌱"

    def harvest(self, uid):
        self.ensure_starter(uid)
        with self._conn() as c:
            tree = c.execute("SELECT * FROM garden_trees WHERE user_id=%s", (uid,)).fetchone()
            if not tree:
                return False, "درختی برای برداشت نداری.", None
            if int(tree["growth"]) < HARVEST_AT:
                return False, f"درخت هنوز آماده نیست ({int(tree['growth'])}٪).", None
            coins = int(tree["pending_coins"])
            xp = int(tree["pending_xp"])
            boxes = int(tree["pending_boxes"])
            # جایزه به بازیکن اصلی (جدول players هسته)
            p = c.execute("SELECT level, xp, coins FROM players WHERE user_id=%s", (uid,)).fetchone()
            if p:
                from core.progression import add_xp
                nl, nx, _ = add_xp(p["level"], p["xp"], xp)
                c.execute("UPDATE players SET coins=%s, level=%s, xp=%s WHERE user_id=%s",
                          (p["coins"] + coins, nl, nx, uid))
            # درخت برداشت شد → پاک
            c.execute("DELETE FROM garden_trees WHERE user_id=%s", (uid,))
            # شانس بذر جایزه
            reward_seed = None
            if random.random() < 0.4:
                reward_seed = random_seed_type()
                c.execute("""INSERT INTO garden_seeds(user_id, seed_type, qty) VALUES (%s,%s,1)
                             ON CONFLICT(user_id, seed_type)
                             DO UPDATE SET qty = garden_seeds.qty + 1""",
                          (uid, reward_seed))
        return True, "برداشت شد!", {"coins": coins, "xp": xp, "boxes": boxes, "seed": reward_seed}

    # ---- watering ----
    def water_left(self, uid):
        self.ensure_starter(uid)
        today = _today()
        with self._conn() as c:
            row = c.execute("SELECT water_day, water_used FROM garden_players WHERE user_id=%s",
                            (uid,)).fetchone()
            if not row or row["water_day"] != today:
                return DAILY_WATER_QUOTA
            return max(0, DAILY_WATER_QUOTA - int(row["water_used"]))

    def water(self, uid, target_id):
        if uid == target_id:
            return False, "درخت خودت رو نمی‌تونی با سهمیه‌ی دوستان آبیاری کنی."
        self.ensure_starter(uid)
        self.ensure_starter(target_id)
        today = _today()
        with self._conn() as c:
            row = c.execute("SELECT water_day, water_used FROM garden_players WHERE user_id=%s",
                            (uid,)).fetchone()
            used = int(row["water_used"]) if row and row["water_day"] == today else 0
            if used >= DAILY_WATER_QUOTA:
                return False, "سهمیه‌ی آبیاری امروزت تموم شده."
            tree = c.execute("SELECT growth FROM garden_trees WHERE user_id=%s", (target_id,)).fetchone()
            if not tree:
                return False, "این باغ درختی نداره."
            c.execute("UPDATE garden_players SET water_day=%s, water_used=%s WHERE user_id=%s",
                      (today, used + 1, uid))
        self.add_growth(target_id, 3, source="friend_water")
        return True, "آبیاری شد 💧 (+۳٪ رشد)"

    # ---- views ----
    def public(self, uid):
        self.ensure_starter(uid)
        with self._conn() as c:
            gp = c.execute("SELECT name FROM garden_players WHERE user_id=%s", (uid,)).fetchone()
            tree = c.execute("SELECT * FROM garden_trees WHERE user_id=%s", (uid,)).fetchone()
            seeds = c.execute("SELECT seed_type, qty FROM garden_seeds WHERE user_id=%s AND qty>0",
                              (uid,)).fetchall()
        return {
            "name": (gp["name"] if gp and gp["name"] else f"کاربر {uid}"),
            "tree": dict(tree) if tree else None,
            "seeds": [dict(s) for s in seeds],
        }

    def friend_gardens(self, uid, limit=8):
        self.ensure_starter(uid)
        with self._conn() as c:
            rows = c.execute("""
                SELECT t.user_id, t.growth,
                       COALESCE(NULLIF(p.display_name,''), p.name, gp.name) AS shown_name
                FROM garden_trees t
                LEFT JOIN players p ON p.user_id = t.user_id
                LEFT JOIN garden_players gp ON gp.user_id = t.user_id
                WHERE t.user_id <> %s
                ORDER BY t.growth DESC
                LIMIT %s""", (uid, limit)).fetchall()
        return [dict(r) for r in rows]

```


================================================================================
FILE: core\missions.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""تعریف ماموریت‌های روزانه."""
import datetime

MISSIONS = [
    {"key": "play3",   "text": "۳ بازی انجام بده",            "goal": 3, "type": "games",   "coins": 50, "xp": 30},
    {"key": "win1",    "text": "۱ بازی ببر",                  "goal": 1, "type": "wins",    "coins": 60, "xp": 40},
    {"key": "answer10","text": "۱۰ جواب درست بده",            "goal": 10,"type": "answers", "coins": 70, "xp": 50},
    {"key": "suggest1","text": "۱ کلمه جدید پیشنهاد بده",     "goal": 1, "type": "suggest", "coins": 40, "xp": 25},
    {"key": "streak",  "text": "امروز هم سر بزن (ورود روزانه)","goal": 1, "type": "login",   "coins": 30, "xp": 15},
]

def mission_of_day(day_str=None):
    if day_str is None:
        day_str = datetime.date.today().isoformat()
    idx = sum(ord(c) for c in day_str) % len(MISSIONS)
    return MISSIONS[idx]

```


================================================================================
FILE: core\normalize.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""نرمال‌سازی مرکزی کلمات فارسی — همه‌ی مودها و دیتابیس از همین استفاده می‌کنند."""
import re

_AR_FA = str.maketrans({
    "ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ؤ": "و", "ئ": "ی",
})
# اعراب و علائم کوچک عربی
_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_SEPARATORS = re.compile(r"[\s\u200c\u200d\-ـ_]+")


def normalize_word(text):
    """برای مقایسه دقیق واژه‌ها: فاصله، نیم‌فاصله، خط فاصله و کشیده نادیده گرفته می‌شوند."""
    s = (text or "").strip().translate(_AR_FA)
    s = _DIACRITICS.sub("", s)
    s = _SEPARATORS.sub("", s)
    return s.lower()
```


================================================================================
FILE: core\progression.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""منطق پیشرفت بازیکن: XP، Level، استریک. خالص و قابل تست (بدون دیتابیس)."""

def xp_needed(level):
    return 100 + (level - 1) * 50

def add_xp(level, xp, gained):
    gained = max(0, int(gained or 0))      # هیچ‌وقت منفی نشود
    xp = max(0, int(xp or 0))
    level = max(1, int(level or 1))
    xp += gained
    gained_levels = 0
    while xp >= xp_needed(level):
        xp -= xp_needed(level)
        level += 1
        gained_levels += 1
    return level, xp, gained_levels

def levelup_reward(level):
    return 25 + level * 5

def streak_reward(streak_days):
    base = 20
    bonus = min(streak_days, 7) * 10
    return base + bonus

def update_streak(last_day, today, current_streak):
    import datetime
    if not last_day:
        return 1, False
    if last_day == today:
        return current_streak, False
    d_last = datetime.date.fromisoformat(last_day)
    d_today = datetime.date.fromisoformat(today)
    diff = (d_today - d_last).days
    if diff == 1:
        return current_streak + 1, False
    return 1, True

```


================================================================================
FILE: features\__init__.py
================================================================================

```py

```


================================================================================
FILE: features\admin_service.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""سرویس ادمین: تشخیص دسترسی + عملیات مدیریتی."""
import config
from core import db, progression as pr

def is_admin(uid):
    return uid in config.ADMIN_IDS or db.is_db_admin(uid)

def is_owner(uid):
    """فقط ادمین‌های اصلی config می‌توانند ادمین همکار اضافه/حذف کنند."""
    return uid in config.ADMIN_IDS

def stats_text():
    s = db.stats()
    cats = db.list_categories()
    nwords = sum(c for _, c in cats)
    return (
        "📊 <b>آمار کلی کلمو</b>\n━━━━━━━━━━━━━━\n"
        f"👤 بازیکن‌ها: <b>{s['players']}</b>\n"
        f"🔥 فعال (استریک‌دار): <b>{s['active']}</b>\n"
        f"🎮 کل بازی‌ها: <b>{s['games']}</b>\n"
        f"🏆 کل بردها: <b>{s['wins']}</b>\n"
        f"🪙 سکه در گردش: <b>{s['coins']:,}</b>\n"
        f"🗂 دسته‌ها: <b>{len(cats)}</b> | کلمات: <b>{nwords}</b>"
    )

def give(target_uid, coins=0, xp=0):
    p = db.get_player(target_uid)
    if not p:
        return None
    fields = {}
    if coins:
        fields["coins"] = p["coins"] + coins
    if xp:
        lvl, newxp, _ = pr.add_xp(p["level"], p["xp"], xp)
        fields["level"] = lvl
        fields["xp"] = newxp
    if fields:
        db.save_player(target_uid, **fields)
    return db.get_player(target_uid)

```


================================================================================
FILE: features\garden.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""🌱 باغچه‌ی کلمو (Kalemo Garden) — فاز آینده (Preview).

این ماژول فقط طرح اولیه‌ی یک سیستم «پیشرفت غیرفعال» (Idle Progression) است
برای افزایش بازگشت روزانه‌ی کاربران. فعلاً پیاده‌سازی نمی‌شود و در جریان
بازی نقشی ندارد — صرفاً به‌عنوان نقطه‌ی توسعه‌ی آینده اینجا لحاظ شده است.

ایده‌ی کلی:
- هر بازیکن یک باغچه دارد که با سکه/XP بازی رشد می‌کند.
- گیاهان به‌مرور زمان (حتی وقتی کاربر آفلاین است) رشد می‌کنند.
- برداشت روزانه → سکه‌ی اضافی → انگیزه‌ی بازگشت هر روز.

طراحی ماژولار: وقتی فعال شد، فقط کافی است یک هندلر و چند جدول اضافه شود؛
هسته‌ی بازی نیازی به تغییر ندارد.
"""

ENABLED = False  # وقتی True شود، فاز باغچه فعال می‌شود.


def preview_card():
    return (
        "🌱 <b>باغچه‌ی کلمو — به‌زودی</b>\n"
        "━━━━━━━━━━━━━━\n"
        "یه باغچه برای خودت بساز که حتی وقتی نیستی رشد می‌کنه!\n"
        "هر روز برگرد و محصولتو برداشت کن 🪙"
    )

```


================================================================================
FILE: features\garden_service.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""رویدادهای سبک باغچه کلمو.

این فایل عمداً کوچک نگه داشته شده تا هر بخش ربات بتواند بدون وابستگی UI
به رشد درخت، بذر و پاداش‌های باغچه وصل شود.
"""

import random
from core import db


def on_correct_answer(uid):
    """پاسخ صحیح در مودهای سوالی: رشد کوچک اما فوری."""
    db.garden_add_growth(uid, 3, source="correct_answer", detail="پاسخ صحیح")


def on_match_played(uid):
    """پایان مسابقه برای همه شرکت‌کننده‌ها."""
    db.garden_add_growth(uid, 4, source="match_played", detail="حضور در مسابقه")
    if random.random() < 0.12:
        db.garden_add_seed(uid, source="match_seed")


def on_match_win(uid):
    """برد مسابقه: رشد بیشتر و شانس بذر."""
    db.garden_add_growth(uid, 12, source="match_win", detail="برد مسابقه")
    if random.random() < 0.35:
        db.garden_add_seed(uid, source="win_seed")


def on_lucky_box(uid, item=None):
    """باز شدن/گرفتن Lucky Box باعث رشد و گاهی بذر می‌شود."""
    db.garden_add_growth(uid, 7, source="lucky_box", detail="Lucky Box")
    if item and item.get("type") == "seed":
        seed_type = db.garden_random_seed_type()
        db.garden_add_seed(uid, seed_type, 1, source="lucky_box_seed")
        return seed_type
    if random.random() < 0.18:
        return db.garden_add_seed(uid, source="lucky_box_bonus_seed")
    return None


def on_daily_garden_visit(uid):
    return db.garden_daily_visit(uid)

```


================================================================================
FILE: features\lucky_box.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""🎁 جعبه شانس بعد از پایان مسابقه.

Phase 1: احتمال ظاهر شدن به‌طور محسوس کاهش یافت (۰٫۲۵ → ۰٫۱۰) و نام در همه‌جا
از «Lucky Box» به «🎁 جعبه شانس» تغییر کرد.
"""

import logging
import random
from core import db, progression as pr

logger = logging.getLogger("kalemo.luckybox")

# نام نمایشی واحد برای استفاده در UI/پیام‌ها/لاگ‌ها.
BOX_NAME = "🎁 جعبه شانس"

# احتمال ظاهر شدن جعبه شانس بعد از هر مسابقه (به‌ازای هر بازیکن).
DROP_CHANCE = 0.10

ITEMS = [
    {"type": "coin", "value": 30, "rarity": "common", "weight": 40},
    {"type": "coin", "value": 80, "rarity": "common", "weight": 25},
    {"type": "xp", "value": 40, "rarity": "common", "weight": 25},
    {"type": "xp", "value": 100, "rarity": "rare", "weight": 8},
    {"type": "title", "value": "کلمه‌باز", "rarity": "rare", "weight": 4},
    {"type": "badge", "value": "برق ذهن", "rarity": "rare", "weight": 3},
    {"type": "profile_frame", "value": "طلایی", "rarity": "epic", "weight": 2},
    {"type": "seed", "value": 1, "rarity": "future", "weight": 5},
]


def _pick_item():
    weights = [i["weight"] for i in ITEMS]
    return random.choices(ITEMS, weights=weights, k=1)[0]


def try_grant(uid, match_id=None):
    """با احتمال DROP_CHANCE یک جعبه شانس به بازیکن می‌دهد.

    خطاها لاگ می‌شوند و None برمی‌گردد تا هرگز جریان پایان بازی را نشکنند.
    """
    try:
        if random.random() > DROP_CHANCE:
            return None

        item = _pick_item()
        p = db.get_player(uid)
        if not p:
            return None

        if item["type"] == "coin":
            db.save_player(uid, coins=p["coins"] + int(item["value"]))
        elif item["type"] == "xp":
            lvl, xp, _ = pr.add_xp(p["level"], p["xp"], int(item["value"]))
            db.save_player(uid, level=lvl, xp=xp)

        # title/badge/profile_frame/seed فعلاً فقط در جدول ذخیره می‌شوند.
        db.add_lucky_box(
            user_id=uid,
            match_id=match_id,
            item_type=item["type"],
            item_value=item["value"],
            rarity=item["rarity"],
        )
        logger.info("جعبه شانس به کاربر %s داده شد: %s", uid, item)
        return item
    except Exception:
        logger.exception("خطا در اعطای جعبه شانس به کاربر %s", uid)
        return None


def item_text(item):
    if item["type"] == "coin":
        return f"🪙 {item['value']} سکه"
    if item["type"] == "xp":
        return f"⚡️ {item['value']} XP"
    if item["type"] == "title":
        return f"🏷 عنوان: {item['value']}"
    if item["type"] == "badge":
        return f"🏅 نشان: {item['value']}"
    if item["type"] == "profile_frame":
        return f"🖼 قاب پروفایل: {item['value']}"
    if item["type"] == "seed":
        return f"🌱 بذر: {item['value']}"
    return str(item["value"])

```


================================================================================
FILE: features\player_service.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""سرویس بازیکن: منطق پیشرفت/استریک/ماموریت + مدیریت نام نمایشی."""
import datetime
import time
import config
from core import db, progression as pr, missions as ms
from ui import cards


def today():
    return datetime.date.today().isoformat()


def register(uid, name):
    existed = db.get_player(uid) is not None
    p = db.ensure_player(uid, name)
    return p, (not existed)


def display_name(uid, fallback=""):
    return db.get_display_name(uid) or fallback


# ---- نام نمایشی ----
def validate_name(name):
    """قوانین: ۳ تا ۲۰ کاراکتر. برمی‌گرداند (ok, msg)."""
    name = (name or "").strip()
    if len(name) < 3:
        return False, "نام باید حداقل ۳ کاراکتر باشه."
    if len(name) > 20:
        return False, "نام باید حداکثر ۲۰ کاراکتر باشه."
    return True, None


def name_cooldown_left(uid):
    """ثانیه‌های باقی‌مانده تا اجازه‌ی تغییر نام (۰ یعنی آزاد)."""
    p = db.get_player(uid)
    if not p:
        return 0
    last = p.get("name_changed_at", 0)
    if last == 0:
        return 0
    elapsed = int(time.time()) - last
    left = config.NAME_CHANGE_COOLDOWN - elapsed
    return max(0, left)


def set_name(uid, fallback, name):
    """تلاش برای ثبت نام نمایشی. برمی‌گرداند (ok, msg)."""
    db.ensure_player(uid, fallback)
    ok, msg = validate_name(name)
    if not ok:
        return False, msg
    name = name.strip()
    # اگر همین نام فعلی است
    current = db.get_display_name(uid)
    if current and current != name:
        left = name_cooldown_left(uid)
        if left > 0:
            days = left // 86400
            hours = (left % 86400) // 3600
            when = f"{days} روز و {hours} ساعت" if days else f"{hours} ساعت"
            return False, f"تا تغییر بعدی باید {when} صبر کنی."
    if db.is_display_name_taken(name, exclude_uid=uid):
        return False, "این نام قبلاً گرفته شده، یکی دیگه انتخاب کن."
    db.set_display_name(uid, name)
    return True, None


def daily_login(uid, name):
    p = db.ensure_player(uid, name)
    if p["last_login"] == today():
        return {"already": True, "player": p}
    streak, broke = pr.update_streak(p["last_login"], today(), p["streak"])
    reward = pr.streak_reward(streak)
    db.save_player(uid, streak=streak, last_login=today(), coins=p["coins"] + reward)
    db.bump_mission(uid, today(), 0)
    p = db.get_player(uid)
    return {"already": False, "broke": broke, "coins_gained": reward,
            "streak": streak, "player": p, "mission": ms.mission_of_day(today())}


def record_game(uid, name, won, score, correct_answers=0):
    p = db.ensure_player(uid, name)
    xp_gain = 30 + score // 5 + (40 if won else 0)
    new_level, new_xp, levels_gained = pr.add_xp(p["level"], p["xp"], xp_gain)
    is_record = score > p["best_score"]
    coins_gain = sum(pr.levelup_reward(p["level"] + i + 1) for i in range(levels_gained))
    db.save_player(uid,
                   games=p["games"] + 1,
                   wins=p["wins"] + (1 if won else 0),
                   best_score=max(p["best_score"], score),
                   level=new_level, xp=new_xp,
                   coins=p["coins"] + coins_gain)
    db.bump_mission(uid, today(), 1)
    return {"levels_gained": levels_gained, "new_level": new_level,
            "is_record": is_record, "xp_gain": xp_gain, "coins_gain": coins_gain}


def profile_view(uid, name):
    p = db.ensure_player(uid, name)
    shown = (p["display_name"] or "").strip() or p["name"]
    data = dict(name=shown, level=p["level"], xp=p["xp"],
                xp_needed=pr.xp_needed(p["level"]), coins=p["coins"],
                streak=p["streak"], wins=p["wins"], games=p["games"],
                best=p["best_score"])
    card = cards.profile_card(data)
    st = db.suggestion_stats_for_user(uid)
    card += f"\n\n💡 کلمات تاییدشده: <b>{st['approved']}</b>"
    return card


def mission_view(uid):
    m = ms.mission_of_day(today())
    mp = db.get_mission_progress(uid, today())
    done = mp["progress"] >= m["goal"]
    status = "✅ کامل!" if done else f"{mp['progress']}/{m['goal']}"
    text = f"{m['text']}  ({status})\n🎁 جایزه: {m['coins']} سکه + {m['xp']} XP"
    return text, done, mp["claimed"]

```


================================================================================
FILE: features\profile_service.py
================================================================================

```py
"""سرویس پروفایل: نام نمایشی یکتا، اعتبارسنجی، محدودیت تغییر نام (۷ روز)."""
import re, time
from core import db, progression as pr
from ui import cards

NAME_MIN, NAME_MAX = 3, 20
RENAME_COOLDOWN = 7 * 24 * 3600   # ۷ روز
_valid = re.compile(r"^[\w\u0600-\u06FF\u200c ]{3,20}$", re.UNICODE)

def validate_name(name):
    """برمی‌گرداند (ok, normalized_or_error)."""
    n = (name or "").strip()
    if len(n) < NAME_MIN:
        return False, f"نام باید حداقل {NAME_MIN} کاراکتر باشد."
    if len(n) > NAME_MAX:
        return False, f"نام باید حداکثر {NAME_MAX} کاراکتر باشد."
    if not _valid.match(n):
        return False, "فقط حروف، عدد، فاصله و نیم‌فاصله مجاز است."
    if db.name_taken(n):
        return False, "این نام قبلاً گرفته شده. یکی دیگه انتخاب کن."
    return True, n

def has_name(uid):
    p = db.get_profile(uid)
    return bool(p and p["display_name"])

def get_name(uid, fallback=""):
    return db.display_name(uid, fallback)

def can_rename(uid):
    """برمی‌گرداند (ok, seconds_left)."""
    p = db.get_profile(uid)
    if not p or not p["name_changed_at"]:
        return True, 0
    elapsed = int(time.time()) - p["name_changed_at"]
    left = RENAME_COOLDOWN - elapsed
    return (left <= 0), max(0, left)

def set_name(uid, name):
    ok, res = validate_name(name)
    if not ok:
        return False, res
    db.set_display_name(uid, res)
    return True, res

def profile_view(uid, fallback=""):
    p = db.ensure_player(uid, fallback)
    name = get_name(uid, fallback)
    data = dict(name=name, level=p["level"], xp=p["xp"],
                xp_needed=pr.xp_needed(p["level"]), coins=p["coins"],
                streak=p["streak"], wins=p["wins"], games=p["games"],
                best=p["best_score"])
    return cards.profile_card(data)
```


================================================================================
FILE: features\suggestion_service.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""سرویس پیشنهاد کلمات."""

import datetime
from core import db


def create(uid, user_name, word, category, description="", source="menu"):
    word = (word or "").strip()
    category = (category or "").strip()

    if len(word) < 2:
        return False, "کلمه خیلی کوتاه است."

    if len(category) < 2:
        return False, "دسته‌بندی نامعتبر است."

    if db.word_exists(category, word):
        return False, "این کلمه از قبل در دیتابیس وجود دارد."

    ok = db.add_suggestion(
        user_id=uid,
        user_name=user_name,
        word=word,
        category=category,
        description=description,
        source=source
    )

    if not ok:
        return False, "ثبت پیشنهاد انجام نشد."

    db.bump_mission(uid, datetime.date.today().isoformat(), 1)

    return True, "پیشنهاد ثبت شد و وارد صف بررسی مدیران شد."

```


================================================================================
FILE: game\__init__.py
================================================================================

```py

```


================================================================================
FILE: game\modes\__init__.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""رجیستری مودها (Mode System).
هر مود یک ماژول مستقل است؛ افزودن مود جدید = ساخت کلاس + یک خط در REGISTRY.
"""
"""رجیستری مودها (Mode System)."""
from .classic import ClassicRandomMode, ClassicChoiceMode
from .blank import BlankMode
from .variable import VariableMode
from .namefamily import NameFamilyMode
from .clue import ClueMode

# ترتیب نمایش در پنل انتخاب مود
MODE_ORDER = ["classic_random", "classic_choice", "blank", "namefamily", "variable", "clue"]

REGISTRY = {
    ClassicRandomMode.id: ClassicRandomMode,
    BlankMode.id: BlankMode,
    ClassicChoiceMode.id: ClassicChoiceMode,
    VariableMode.id: VariableMode,
    NameFamilyMode.id: NameFamilyMode,
    ClueMode.id: ClueMode,
}

_META = {
    "classic_random": {
        "name": "کلاسیک رندوم",
        "emoji": "🎯",
        "desc": "دسته به‌صورت تصادفی انتخاب می‌شود."
    },
    "classic_choice": {
        "name": "کلاسیک انتخابی",
        "emoji": "📂",
        "desc": "سازنده دسته را انتخاب می‌کند."
    },
    "blank": {
        "name": "جای خالی",
        "emoji": "🧩",
        "desc": "کلمه‌ی ناقص را کامل کن."
    },
    "namefamily": {
        "name": "اسم‌وفامیل",
        "emoji": "✍️",
        "desc": "با یک حرف، دسته‌هارو پر کن."
    },
    "variable": {
        "name": "قوانین متغیر",
        "emoji": "🎲",
        "desc": "هر دور قوانین عوض می‌شود."
    },
    "clue": {
        "name": "سرنخ",
        "emoji": "🕵️",
        "desc": "از روی سرنخ، جواب را حدس بزن."
    },
}

def mode_meta(mode_id):
    m = _META.get(mode_id, _META["classic_random"])
    return {"id": mode_id, **m}


def get_mode_class(mode_id):
    return REGISTRY.get(mode_id, ClassicRandomMode)

```


================================================================================
FILE: game\modes\base.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""رابط پایه مودها. هر مود یک کلاس مستقل است."""
from core.normalize import normalize_word


class BaseMode:
    id = "base"
    name = "پایه"
    emoji = "🎮"

    def __init__(self, words, ruleset=None):
        self.words = list(words)
        self.ruleset = ruleset

    @staticmethod
    def norm(text):
        return normalize_word(text)

    def tutorial(self):
        return f"{self.emoji} مود {self.name}\nآماده باشید..."

    def new_question(self):
        raise NotImplementedError

    def check_answer(self, question, text):
        raise NotImplementedError
```


================================================================================
FILE: game\modes\blank.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""مود جای خالی (Enhanced Missing Letters).
حذف هوشمند حروف با سختی پویا و جلوگیری از الگوی تکراری.
نمونه‌ها: س-ب → سیب | در-ت → درخت | ک-امی-ن → کامیون

نسخه‌ی Phase 1 (Beta) — رفع باگ‌ها:
- باگ فریز/کرش: در نسخه‌ی قبلی `pool` فقط وقتی ساخته می‌شد که لیست کلمات
  «خالی» بود؛ در نتیجه با وجود کلمه، `pool` تعریف‌نشده می‌ماند و
  `NameError` رخ می‌داد (استثنای خاموش → فریز بازی). حالا `pool` همیشه
  به‌درستی ساخته می‌شود.
- حذف کد تکراری انتخاب کلمه (قبلاً منطق انتخاب دوبار کپی شده بود).
- حلقه‌ی ضدتکرار محدود است (bounded) تا هرگز بی‌نهایت نشود.
"""
import random
from .base import BaseMode

DASH = "-"  # جای خالی نمایشی

# نگاشت سطح سختی → نسبت حروف حذف‌شده
DIFFICULTY = {
    "easy":   0.30,
    "normal": 0.45,
    "hard":   0.65,
}


class BlankMode(BaseMode):
    id = "blank"; name = "جای خالی"; emoji = "🧩"

    def __init__(self, words, ruleset=None, difficulty="normal", **kw):
        super().__init__(words, ruleset)
        self.difficulty = difficulty if difficulty in DIFFICULTY else "normal"
        self._recent = []  # کلمات اخیر برای ضدتکرار

    def tutorial(self):
        names = {"easy": "آسان", "normal": "معمولی", "hard": "سخت"}
        return ("🧩 <b>مود جای خالی</b>\n"
                "کلمه‌ی ناقص رو کامل کن و کلمه‌ی کامل رو بفرست.\n"
                "مثال: <code>س-ب</code> ← <b>سیب</b>\n"
                f"سختی: <b>{names[self.difficulty]}</b>\nآماده باشید...")

    # ---- حذف هوشمند ----
    def _mask_word(self, word):
        word = (word or "").strip()
        n = len(word)
        if n == 0:
            return DASH
        if n <= 2:
            hide_count = 1
        else:
            ratio = DIFFICULTY[self.difficulty]
            hide_count = max(1, min(n - 1, round(n * ratio)))
        positions = random.sample(range(n), k=hide_count)
        hidden = set(positions)
        out = []
        i = 0
        while i < n:
            if i in hidden:
                # چند حرف پشت‌سرهمِ حذف‌شده را با یک خط تیره نشان بده
                while i < n and i in hidden:
                    i += 1
                out.append(DASH)
            else:
                out.append(word[i])
                i += 1
        return "".join(out)

    def new_question(self):
        # pool همیشه از کلمات معتبر ساخته می‌شود (رفع باگ NameError).
        pool = [w for w in self.words if (w or "").strip()]
        if not pool:
            return {"prompt": "کلمه‌ای برای این دسته ثبت نشده 😅", "answer": None}

        # ضدتکرار: تا چند تلاش، کلمه‌ای متفاوت از اخیرها انتخاب کن (حلقه‌ی محدود).
        word = random.choice(pool)
        for _ in range(8):
            if word not in self._recent:
                break
            word = random.choice(pool)

        masked = self._mask_word(word)
        # اگر کلمه فقط یک حرف مؤثر داشت، ماسک ممکن است کل کلمه را نشان دهد؛
        # در آن صورت هم مشکلی نیست چون بازیکن باید همان کلمه را بفرستد.
        self._recent = (self._recent + [word])[-5:]
        return {
            "prompt": f"🧩 کلمه رو کامل کن:\n\n<code>{masked}</code>",
            "answer": word,
        }

    def check_answer(self, question, text):
        ans = question.get("answer")
        if not ans:
            return False, "نامعتبر"
        if self.norm(text) == self.norm(ans):
            return True, None
        return False, "غلط"

```


================================================================================
FILE: game\modes\classic.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""مودهای کلاسیک: رندوم و انتخابی. هر دو فقط با دیتابیس معتبر کار می‌کنند."""
from .base import BaseMode


class ClassicRandomMode(BaseMode):
    id = "classic_random"
    name = "کلاسیک رندوم"
    emoji = "🎯"

    def __init__(self, words, category="", ruleset=None):
        super().__init__(words, ruleset)
        self.category = category

    def tutorial(self):
        return (
            f"🎯 <b>کلاسیک رندوم</b>\n"
            f"دسته: <b>{self.category}</b>\n"
            "کلمه‌ی مرتبط بفرست."
        )

    def new_question(self):
        return {
            "prompt": f"📂 دسته: <b>{self.category}</b>\nکلمه‌ی مرتبط بگو.",
            "answers": {self.norm(w) for w in self.words},
        }

    def check_answer(self, question, text):
        if self.norm(text) not in question.get("answers", set()):
            return False, "نامعتبر"
        return True, None


class ClassicChoiceMode(ClassicRandomMode):
    id = "classic_choice"
    name = "کلاسیک انتخابی"
    emoji = "📂"

    def tutorial(self):
        return (
            f"📂 <b>کلاسیک انتخابی</b>\n"
            f"دسته: <b>{self.category}</b>\n"
            "کلمه‌ی مرتبط بفرست."
        )
```


================================================================================
FILE: game\modes\clue.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""مود سرنخ (Clue Mode).
ربات یک سرنخ می‌دهد، بازیکن جواب درست را حدس می‌زند.
سلطان جنگل → شیر | برج ایفل → فرانسه | میوه زرد → موز
"""
import random
from .base import BaseMode

# بانک سرنخ‌ها (clue → set of acceptable answers)
CLUES = [
    ("سلطان جنگل", {"شیر"}),
    ("برج ایفل", {"فرانسه", "پاریس"}),
    ("میوه‌ی زرد و خمیده", {"موز"}),
    ("سیاره‌ی سرخ", {"مریخ"}),
    ("پایتخت ایران", {"تهران"}),
    ("بزرگ‌ترین اقیانوس", {"آرام"}),
    ("حیوانی با خرطوم", {"فیل"}),
    ("فلز زرد و گران‌بها", {"طلا"}),
    ("ستاره‌ی مرکز منظومه‌ی شمسی", {"خورشید"}),
    ("سریع‌ترین حیوان خشکی", {"یوزپلنگ", "یوز"}),
    ("نوشیدنی داغ صبحگاهی", {"چای", "قهوه"}),
    ("پرنده‌ای که نمی‌پرد و در قطب است", {"پنگوئن"}),
    ("شهر عاشقان و کلیسای کلوسئوم", {"رم"}),
    ("میوه‌ی قرمز با هسته‌های ریز روی پوست", {"توت‌فرنگی"}),
    ("فصل ریزش برگ‌ها", {"پاییز"}),
    ("بزرگ‌ترین قاره", {"آسیا"}),
    ("نویسنده‌ی شاهنامه", {"فردوسی"}),
    ("کوهی آتش‌فشانی در ژاپن", {"فوجی"}),
    ("حیوانی که عسل می‌سازد", {"زنبور"}),
    ("سیاه و سفید و اهل چین", {"پاندا"}),
]


class ClueMode(BaseMode):
    id = "clue"; name = "سرنخ"; emoji = "🕵️"

    def __init__(self, words, ruleset=None, **kw):
        super().__init__(words, ruleset)
        self._recent = []

    def tutorial(self):
        return ("🕵️ <b>مود سرنخ</b>\n"
                "من یه سرنخ می‌دم، تو جواب درست رو حدس بزن!\n"
                "مثال: <code>سلطان جنگل</code> ← <b>شیر</b>\nآماده باشید...")

    def new_question(self):
        clue, answers = random.choice(CLUES)
        for _ in range(8):
            if clue not in self._recent:
                break
            clue, answers = random.choice(CLUES)
        self._recent = (self._recent + [clue])[-6:]
        return {
            "prompt": f"🕵️ <b>سرنخ:</b>\n\n<b>{clue}</b>\n\n<i>جواب رو حدس بزن!</i>",
            "answers": {self.norm(a) for a in answers},
        }

    def check_answer(self, question, text):
        if self.norm(text) in question["answers"]:
            return True, None
        return False, "نادرست"
```


================================================================================
FILE: game\modes\namefamily.py
================================================================================

```py
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

```


================================================================================
FILE: game\modes\variable.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""مود قوانین متغیر: هر دور چند قانون تصادفی فعال می‌شود؛
کلمه باید هم در دسته باشد و هم همه قوانین آن دور را رعایت کند.
نکته: قوانین طوری انتخاب می‌شوند که حداقل یک کلمه‌ی دسته آن‌ها را رعایت کند
(تا دور غیرقابل‌بردن نشود)."""
import random
from .base import BaseMode
from game.rules import RuleSet

class VariableMode(BaseMode):
    id = "variable"; name = "قوانین متغیر"; emoji = "🎲"

    def tutorial(self):
        return ("🎲 مود قوانین متغیر\n"
                "هر دور چند قانون تصادفی فعال می‌شه (مثلاً «شروع با م» + «حداقل ۵ حرف»).\n"
                "کلمه‌ای بگو که هم تو دسته باشه هم قوانین رو رعایت کنه.\nآماده باشید...")

    def _solvable_ruleset(self, attempts=25):
        """یک RuleSet می‌سازد که حداقل یک کلمه‌ی دسته آن را رعایت کند."""
        for _ in range(attempts):
            rs = RuleSet().randomize_for_round(n=2)
            if any(rs.validate(w)[0] for w in self.words):
                return rs
        # اگر با ۲ قانون نشد، با یک قانون
        for _ in range(attempts):
            rs = RuleSet().randomize_for_round(n=1)
            if any(rs.validate(w)[0] for w in self.words):
                return rs
        return RuleSet()  # بدون قانون (همیشه حل‌شدنی)

    def new_question(self):
        if not [w for w in self.words if (w or "").strip()]:
            return {"prompt": "کلمه‌ای برای این دسته ثبت نشده 😅",
                    "ruleset": None, "answers": set()}
        rs = self._solvable_ruleset()
        return {"prompt": f"قوانین این دور:\n{rs.describe()}\n\nیه کلمه‌ی مناسب بگو!",
                "ruleset": rs, "answers": {self.norm(w) for w in self.words}}

    def check_answer(self, question, text):
        if not question.get("ruleset"):
            return False, "نامعتبر"
        w = self.norm(text)
        if w not in question["answers"]:
            return False, "نامرتبط"
        # قوانین روی متن اصلی کاربر اعمال می‌شوند (طول/حروف)
        ok, failed = question["ruleset"].validate(text.strip())
        if not ok:
            return False, f"قانون رعایت نشد: {failed}"
        return True, None
```


================================================================================
FILE: game\ranking.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""رتبه‌بندی مرکزی و واحد کلمو (Single Source of Truth).

هدف: حذف منطق تکراریِ مرتب‌سازی که در چند جا پراکنده بود و باعث باگ رتبه‌بندی
(نمایش بازیکن با امتیاز صفر بالاتر از بازیکن با امتیاز بیشتر) می‌شد.

قوانین مرتب‌سازی قطعی (deterministic tie-break):
1. امتیاز بیشتر (score) — نزولی
2. برد بیشتر (wins) — نزولی
3. ثبت‌نام زودتر / user_id کوچک‌تر — صعودی

هر جای پروژه که رتبه‌بندی می‌خواهد، فقط از این ماژول استفاده می‌کند.
"""


def rank_key(score=0, wins=0, uid=0):
    """کلید مرتب‌سازی قطعی. با sort(key=..., reverse=True) استفاده نکنید؛
    این کلید طوری ساخته شده که خودش «هرچه بزرگ‌تر = رتبه بهتر» را رعایت کند
    و user_id کوچک‌تر (ثبت زودتر) در تساوی برنده باشد.
    """
    # user_id را منفی می‌کنیم تا در حالت نزولی، uid کوچک‌تر بالاتر بیاید.
    return (int(score or 0), int(wins or 0), -int(uid or 0))


def sort_players(players):
    """players: dict یا list از (uid, info) که info شامل score و اختیاری wins است.

    خروجی: list مرتب‌شده‌ی (uid, info) به‌صورت نزولی و قطعی.
    """
    items = players.items() if isinstance(players, dict) else list(players)
    return sorted(
        items,
        key=lambda kv: rank_key(
            score=kv[1].get("score", 0),
            wins=kv[1].get("wins", 0),
            uid=kv[0],
        ),
        reverse=True,
    )


def sort_rows(rows, score_getter, wins_getter=None, id_getter=None):
    """مرتب‌سازی عمومی برای ردیف‌های دلخواه (مثلاً خروجی SQL).

    rows: iterable
    score_getter/wins_getter/id_getter: توابعی که از هر ردیف مقدار را می‌گیرند.
    """
    def key(r):
        return rank_key(
            score=score_getter(r),
            wins=wins_getter(r) if wins_getter else 0,
            uid=id_getter(r) if id_getter else 0,
        )
    return sorted(rows, key=key, reverse=True)


def assert_sorted(ranked, score_getter):
    """اعتبارسنجی خودکار: مطمئن می‌شود لیست واقعاً نزولی مرتب شده است.

    اگر جایی امتیاز پایین‌تر بالاتر از امتیاز بالاتر باشد، AssertionError می‌دهد.
    این تابع قبل از ارسال هر لیدربورد صدا زده می‌شود تا باگ رتبه هرگز به کاربر نرسد.
    """
    prev = None
    for i, item in enumerate(ranked):
        cur = int(score_getter(item) or 0)
        if prev is not None and cur > prev:
            raise AssertionError(
                f"Leaderboard not sorted: index {i} score {cur} > previous {prev}"
            )
        prev = cur
    return True

```


================================================================================
FILE: game\rules.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""موتور قوانین ماژولار (Rule Engine) برای کلمو.
هر قانون یک کلاس مستقل با شناسه، برچسب، و تابع check(word, ctx) است.
افزودن قانون جدید = فقط افزودن یک کلاس و ثبت آن در REGISTRY.
"""
import random

# الفبای فارسی برای انتخاب تصادفی حرف
PERSIAN_LETTERS = list("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی")

class Rule:
    id = "base"
    label = "قانون پایه"
    # اگر True باشد، هنگام فعال‌سازی یک پارامتر تصادفی می‌گیرد (مثل حرف یا عدد)
    needs_param = False

    def __init__(self, param=None):
        self.param = param

    def randomize(self):
        """پارامتر تصادفی برای مود «قوانین متغیر»."""
        return None

    def describe(self):
        """متن فارسی قابل‌نمایش قانون."""
        return self.label

    def check(self, word):
        """آیا کلمه این قانون را رعایت می‌کند؟ (True/False)"""
        return True


class MinLen(Rule):
    id = "min_len"; label = "حداقل تعداد حروف"; needs_param = True
    def randomize(self): self.param = random.choice([4, 5, 6]); return self
    def describe(self): return f"حداقل {self.param} حرف"
    def check(self, w): return len(w) >= int(self.param)

class MaxLen(Rule):
    id = "max_len"; label = "حداکثر تعداد حروف"; needs_param = True
    def randomize(self): self.param = random.choice([5, 6, 7]); return self
    def describe(self): return f"حداکثر {self.param} حرف"
    def check(self, w): return len(w) <= int(self.param)

class ExactLen(Rule):
    id = "exact_len"; label = "تعداد حروف دقیق"; needs_param = True
    def randomize(self): self.param = random.choice([5, 6]); return self
    def describe(self): return f"دقیقاً {self.param} حرف"
    def check(self, w): return len(w) == int(self.param)

class StartsWith(Rule):
    id = "starts_with"; label = "شروع با حرف"; needs_param = True
    def randomize(self): self.param = random.choice(PERSIAN_LETTERS); return self
    def describe(self): return f"شروع با «{self.param}»"
    def check(self, w): return w.startswith(self.param)

class EndsWith(Rule):
    id = "ends_with"; label = "پایان با حرف"; needs_param = True
    def randomize(self): self.param = random.choice(PERSIAN_LETTERS); return self
    def describe(self): return f"پایان با «{self.param}»"
    def check(self, w): return w.endswith(self.param)

class MustContain(Rule):
    id = "must_contain"; label = "داشتن حرف مشخص"; needs_param = True
    def randomize(self): self.param = random.choice(PERSIAN_LETTERS); return self
    def describe(self): return f"شامل حرف «{self.param}»"
    def check(self, w): return self.param in w

class MustNotContain(Rule):
    id = "must_not_contain"; label = "نداشتن حرف مشخص"; needs_param = True
    def randomize(self): self.param = random.choice(list("اوینر")); return self
    def describe(self): return f"بدون حرف «{self.param}»"
    def check(self, w): return self.param not in w

# قوانینی که فقط حالت/پرچم هستند (برای آینده، اثر مستقیم روی check ندارند یا ساده‌اند)
class TimeLimit(Rule):
    id = "time_limit"; label = "محدودیت زمانی"
    def describe(self): return "محدودیت زمانی فعال"

class BonusScore(Rule):
    id = "bonus"; label = "امتیاز ویژه"
    def describe(self): return "امتیاز ویژه فعال"

# ثبت همه قوانین — افزودن قانون جدید فقط همین‌جا یک خط
REGISTRY = {r.id: r for r in [
    MinLen, MaxLen, ExactLen, StartsWith, EndsWith,
    MustContain, MustNotContain, TimeLimit, BonusScore,
]}

# قوانینی که برای مود «قوانین متغیر» تصادفی انتخاب می‌شوند (پارامتری‌ها)
RANDOMIZABLE = ["min_len", "max_len", "exact_len", "starts_with",
                "ends_with", "must_contain", "must_not_contain"]


class RuleSet:
    """مجموعه قوانین فعال یک بازی. مستقل و قابل سریال‌سازی ساده."""
    def __init__(self):
        self.rules = []  # list[Rule]

    def toggle(self, rule_id):
        """قانون پرچمی را روشن/خاموش می‌کند (برای پنل قوانین دستی)."""
        existing = next((r for r in self.rules if r.id == rule_id), None)
        if existing:
            self.rules.remove(existing)
            return False
        cls = REGISTRY.get(rule_id)
        if not cls:
            return None
        r = cls()
        if r.needs_param:
            r.randomize()
        self.rules.append(r)
        return True

    def is_active(self, rule_id):
        return any(r.id == rule_id for r in self.rules)

    def randomize_for_round(self, n=2):
        """برای مود قوانین متغیر: n قانون تصادفی پارامتری."""
        self.rules = []
        ids = random.sample(RANDOMIZABLE, k=min(n, len(RANDOMIZABLE)))
        for rid in ids:
            self.rules.append(REGISTRY[rid]().randomize())
        return self

    def describe(self):
        if not self.rules:
            return "—"
        return "\n".join(f"• {r.describe()}" for r in self.rules)

    def validate(self, word):
        """آیا کلمه همه قوانین فعال را رعایت می‌کند؟
        برمی‌گرداند (ok, failed_rule_text یا None)."""
        for r in self.rules:
            if not r.check(word):
                return False, r.describe()
        return True, None

```


================================================================================
FILE: game\session.py
================================================================================

```py
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

```


================================================================================
FILE: handlers\__init__.py
================================================================================

```py

```


================================================================================
FILE: handlers\admin.py
================================================================================

```py
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

```


================================================================================
FILE: handlers\garden.py
================================================================================

```py
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

```


================================================================================
FILE: handlers\lobby.py
================================================================================

```py
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
import asyncio
import html
import logging
import re
import time
from datetime import timedelta

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
        await q.answer(f"مود شد: {s.mode_name()}")
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
                await _finish(ctx, s.chat_id, reason="time")
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
    except Exception:
        log.exception("خطای غیرمنتظره در حلقه‌ی بازی (chat_id=%s)", s.chat_id)


async def _autoskip(ctx, s):
    """سوال فعلی را رد می‌کند و سوال بعدی را نمایش می‌دهد."""
    try:
        prev = s.question.get("prompt") if isinstance(s.question, dict) else None
        s.next_question()
        s.last_answer_at = time.time()   # تایمر auto-skip را ریست کن
        # اگر واقعاً سوال عوض شد، اطلاع بده
        cur = s.question.get("prompt") if isinstance(s.question, dict) else None
        if cur != prev:
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


async def _update_live(ctx, s):
    if not s.live_msg_id:
        return
    try:
        await ctx.bot.edit_message_text(
            chat_id=s.chat_id, message_id=s.live_msg_id,
            text=panels.live_text(s), parse_mode=HTML,
            reply_markup=panels.running_kb(s))
    except Exception:
        # خطای «message is not modified» عادی است؛ لاگ نمی‌کنیم.
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
    if not s.focus_mode:
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

```


================================================================================
FILE: handlers\menu.py
================================================================================

```py
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
```


================================================================================
FILE: handlers\namefamily_private.py
================================================================================

```py
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

```


================================================================================
FILE: handlers\router.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""مسیریاب پیام‌های متنی: نام نمایشی/پیشنهاد کلمه/ادمین (خصوصی) یا شروع/بازی (گروه)."""
from telegram import Update
from telegram.ext import ContextTypes
from handlers import admin, lobby, menu, suggestions, namefamily_private
from game import session as sess

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    text = update.message.text if update.message else ""

    if chat.type == "private":
        # اول: پاسخ‌های در حال ثبت اسم‌وفامیل
        if await namefamily_private.on_nf_text(update, ctx):
            return
        # سپس: دریافت نام نمایشی (آنبوردینگ/تغییر نام)
        if await menu.on_name_text(update, ctx):
            return
        # سپس: پیشنهاد کلمه
        if await suggestions.on_suggest_text(update, ctx):
            return
        # سپس: ورودی‌های ادمین
        if await admin.on_admin_text(update, ctx):
            return
        if await admin.on_words_text(update, ctx):
            return
        return

    # گروه: «شروع کلمو» / «شروع بازی» → باز کردن لابی
        # گروه
    if lobby.is_start_text(text):
        if sess.exists(chat.id):
            # بازی فعاله → مثل /newgame تکراری رفتار کن (حذف + اخطار)
            return await lobby.handle_start_during_game(update, ctx)
        return await lobby.open_lobby(update, ctx)
    await lobby.on_group_text(update, ctx)



```


================================================================================
FILE: handlers\suggestions.py
================================================================================

```py
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

```


================================================================================
FILE: kalemo_seed_words.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""Seed اختصاصی اسم‌وفامیل کلمو.

فقط این دسته‌ها را نگه می‌دارد و کامل می‌کند:
غذا، رنگ، میوه، حیوان، اشیا، عضو بدن، شهر، کشور، شغل
"""

from core import db


def seed_kalemo_words():
    db.init()
    db.seed_namefamily_words(clean_extra_categories=True)
    total = sum(count for _, count in db.list_categories())
    return total


if __name__ == "__main__":
    total = seed_kalemo_words()
    print(f"✅ NameFamily database cleaned and seeded. total words: {total}")

```


================================================================================
FILE: main.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""نقطه‌ی ورود ربات کلمو (Kalemo).
ساخته‌شده با python-telegram-bot v21+ — کاملاً async، Application Builder،
CallbackQueryHandler برای همه‌ی دکمه‌ها، و Menu Button تلگرام.

اجرا:
    export KALEMO_BOT_TOKEN="123:ABC"
    export KALEMO_BOT_USERNAME="KalemoBot"
    export KALEMO_ADMINS="1053046454"
    python main.py
"""

import os

import psutil
import threading
import time


import logging
import threading
from web import run
from telegram import (
    Update, BotCommand, BotCommandScopeAllPrivateChats,
    MenuButtonCommands,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, Defaults, filters,
)

import config
from core import db
from handlers import menu, admin, lobby, router, namefamily_private, garden

from flask import Flask
import threading

app = Flask(__name__)


@app.route("/")
def home():
    return "Kalemo Bot is alive!", 200

def monitor():
    p = psutil.Process()

    while True:
        cpu = p.cpu_percent(interval=1)
        ram = p.memory_info().rss / 1024 / 1024

        log.info(
            f"CPU: {cpu:.1f}% | RAM: {ram:.1f} MB"
        )

        time.sleep(30)


def run_web():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )

threading.Thread(target=run_web).start()


threading.Thread(
    target=run,
    daemon=True
).start()

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kalemo")


# ---------- Menu Button + Commands ----------
async def _post_init(app: Application):
    """ثبت دستورها و فعال‌سازی Menu Button تلگرام (UI بدون یادگیری دستور)."""
    private_cmds = [
        BotCommand("start", "🎮 ایجاد بازی / منوی اصلی"),
        BotCommand("play", "▶ ادامه/شروع بازی"),
        BotCommand("profile", "👤 پروفایل"),
        BotCommand("garden", "🌳 باغچه"),
        BotCommand("leaderboard", "🏆 لیدربورد"),
        BotCommand("settings", "⚙ تنظیمات"),
        BotCommand("help", "❓ راهنما"),
    ]
    await app.bot.set_my_commands(
        private_cmds, scope=BotCommandScopeAllPrivateChats())
    # دکمه‌ی منوی تلگرام → فهرست دستورها
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    log.info("Menu button & commands registered.")


# ---------- میان‌برهای منو از طریق دستور ----------
async def cmd_menu_shortcut(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دستورهای منو در چت خصوصی را به همان نمای دکمه‌ای می‌رساند."""
    if update.effective_chat.type != "private":
        return await update.message.reply_text(
            "🎮 برای بازی تو گروه «شروع کلمو» بنویس یا /newgame بزن!")
    from features import player_service as svc
    from ui import keyboards as kb, persona, cards
    uid = update.effective_user.id
    name = svc.display_name(uid, update.effective_user.first_name)
    svc.register(uid, update.effective_user.first_name)
    cmd = (update.message.text or "/").split()[0].lstrip("/").split("@")[0]

    if cmd == "profile":
        return await update.message.reply_text(
            svc.profile_view(uid, name), parse_mode=ParseMode.HTML,
            reply_markup=kb.profile_menu())
    if cmd == "leaderboard":
        rows = db.top_players(10)
        return await update.message.reply_text(
            cards.leaderboard_card("لیدربورد", rows), parse_mode=ParseMode.HTML,
            reply_markup=kb.back_menu())
    if cmd == "settings":
        cur = db.get_display_name(uid) or "—"
        return await update.message.reply_text(
            "⚙ <b>تنظیمات</b>\n━━━━━━━━━━━━━━\nنام نمایشی فعلی: <b>%s</b>" % cur,
            parse_mode=ParseMode.HTML, reply_markup=kb.settings_menu())
    if cmd == "help":
        return await update.message.reply_text(
            "❓ منو رو به گروه اضافه کن و «شروع کلمو» بنویس 🔥",
            reply_markup=kb.play_in_group())
    # play / default
    return await update.message.reply_text(
        persona.say("welcome"), reply_markup=kb.main_menu())


def build_app() -> Application:
    db.init()
    defaults = Defaults(parse_mode=None)
    app = (ApplicationBuilder()
           .token(config.BOT_TOKEN)
           .defaults(defaults)
           .post_init(_post_init)
           .build())

    # دستورهای پایه
    app.add_handler(CommandHandler("start", menu.cmd_start))
    app.add_handler(CommandHandler(["play", "profile", "leaderboard", "settings", "help"],
                                   cmd_menu_shortcut))
    app.add_handler(CommandHandler("admin", admin.cmd_admin))
    app.add_handler(CommandHandler("garden", garden.cmd_garden))

    # بازی گروهی
    app.add_handler(CommandHandler("newgame", lobby.cmd_newgame))
    app.add_handler(CommandHandler("endgame", lobby.cmd_endgame))
    app.add_handler(CommandHandler("gsettings", lobby.cmd_settings))

    # CallbackQueryها — همه دکمه‌محور
    app.add_handler(CallbackQueryHandler(menu.on_onboarding, pattern=r"^ob:"))
    app.add_handler(CallbackQueryHandler(menu.on_mission_claim, pattern=r"^mission:"))
    app.add_handler(CallbackQueryHandler(namefamily_private.on_nf_cb, pattern=r"^nf:"))
    app.add_handler(CallbackQueryHandler(garden.on_cb, pattern=r"^g:"))
    app.add_handler(CallbackQueryHandler(menu.on_menu, pattern=r"^m:"))
    app.add_handler(CallbackQueryHandler(admin.on_admin_cb, pattern=r"^a:"))
    app.add_handler(CallbackQueryHandler(lobby.on_cb, pattern=r"^k:"))

    # پیام‌های متنی → مسیریاب
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router.on_text))

    return app


def main():
    if not config.BOT_TOKEN or config.BOT_TOKEN.startswith("PUT-YOUR"):
        raise SystemExit("⛔️ KALEMO_BOT_TOKEN ست نشده. متغیر محیطی رو تنظیم کن.")
    threading.Thread(
        target=run,
        daemon=True
    ).start()
    app = build_app()
    log.info("Kalemo is running…")
    threading.Thread(
        target=monitor,
        daemon=True
    ).start()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

```


================================================================================
FILE: project_dump.md
================================================================================

```md
# Project Dump


================================================================================
FILE: __init__.py
================================================================================

```py

```


================================================================================
FILE: config.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""پیکربندی Kalemo (کلمو).
مقادیر حساس از متغیرهای محیطی خوانده می‌شوند تا توکن داخل کد قرار نگیرد.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# توکن ربات (از @BotFather) — حتماً به‌صورت متغیر محیطی ست شود.
BOT_TOKEN = os.getenv("KALEMO_BOT_TOKEN")

# آدرس اتصال به PostgreSQL. روی Render مقدار «Internal Database URL» را اینجا بگذارید.
# مثال: postg://user:pass@host:5432/dbname
DATABASE_URL = os.getenv("DATABASE_URL")

# حداکثر اندازه‌ی connection pool. روی پلن رایگان Render کوچک نگه دارید.
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "5"))

# یوزرنیم ربات (بدون @) — برای لینک افزودن به گروه
BOT_USERNAME = os.getenv("KALEMO_BOT_USERNAME")

# آیدی عددی ادمین‌های اصلی (owner). با کاما جدا کنید: "123,456"
ADMIN_IDS = {
    int(x) for x in os.environ.get("KALEMO_ADMINS", "").replace(" ", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

# مسیر دیتابیس SQLite دیگر استفاده نمی‌شود؛ فقط برای سازگاری عقب‌رو نگه داشته شده.
DB_PATH = os.environ.get("KALEMO_DB", "kalemo.db")

# فاصله زمانی مجاز برای تغییر نام نمایشی (ثانیه) — پیش‌فرض ۷ روز
NAME_CHANGE_COOLDOWN = int(os.environ.get("KALEMO_NAME_COOLDOWN", str(7 * 24 * 3600)))

```


================================================================================
FILE: core\__init__.py
================================================================================

```py

```


================================================================================
FILE: core\db.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""لایه دیتابیس Kalemo — نسخه‌ی PostgreSQL.

این فایل جایگزین نسخه‌ی SQLite است. تمام امضاهای توابع عمومی (public API)
دقیقاً مثل قبل باقی مانده‌اند، بنابراین هیچ فایل دیگری در پروژه نیازی به
تغییر ندارد. فقط لایه‌ی ذخیره‌سازی از SQLite به PostgreSQL منتقل شده است.

نکات مهاجرت:
- به‌جای sqlite3 از psycopg (نسخه ۳) استفاده می‌شود.
- کانکشن از طریق یک ConnectionPool مدیریت می‌شود (مناسب پلن رایگان Render).
- placeholder پارامترها از «?» به «%s» تغییر کرده است.
- AUTOINCREMENT → GENERATED / SERIAL (اینجا از GENERATED ALWAYS AS IDENTITY).
- lastrowid → INSERT ... RETURNING id.
- INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING.
- executescript → execute (psycopg چند دستور را در یک رشته اجرا می‌کند).
- خطای یکتایی sqlite3.IntegrityError → psycopg.errors.UniqueViolation.
- PRAGMA table_info → information_schema.columns.
"""

import time
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config
from core.garden_db import init_garden

# سازگاری عقب‌رو: هر جای پروژه که db.IntegrityError را می‌گیرد کار کند.
IntegrityError = psycopg.errors.UniqueViolation

# ---------- connection pool ----------
# روی پلن رایگان Render تعداد کانکشن‌ها محدود است؛ pool کوچک نگه داشته می‌شود.
_DSN = config.DATABASE_URL
if not _DSN:
    raise RuntimeError(
        "DATABASE_URL تنظیم نشده است. در Render → Environment مقدار "
        "Internal Database URL دیتابیس PostgreSQL را ست کنید."
    )

_pool = ConnectionPool(
    conninfo=_DSN,
    min_size=1,
    max_size=int(config.DB_POOL_MAX),
    kwargs={"row_factory": dict_row, "autocommit": False},
    open=True,
)


@contextmanager
def conn():
    """یک کانکشن از pool می‌گیرد، cursor با دسترسی مثل dict برمی‌گرداند.

    برای حفظ سازگاری با کد قدیمی، شیءِ yield شده یک wrapper است که
    متد execute() آن یک cursor برمی‌گرداند (دقیقاً مثل sqlite3.Connection.execute).
    """
    with _pool.connection() as c:
        try:
            yield _ConnShim(c)
            c.commit()
        except Exception:
            c.rollback()
            raise


class _ConnShim:
    """سازگاری با API قدیمی sqlite3.

    در sqlite3، connection.execute(sql, params) خودش یک cursor برمی‌گرداند
    که می‌شود روی آن fetchone/fetchall/rowcount صدا زد. اینجا همان رفتار را
    شبیه‌سازی می‌کنیم تا کوئری‌های موجود بدون تغییر کار کنند.
    """

    def __init__(self, real_conn):
        self._c = real_conn

    def execute(self, sql, params=()):
        # ترجمه‌ی خودکار placeholder «?» به «%s» تا کوئری‌ها دست‌نخورده بمانند.
        if "?" in sql:
            sql = sql.replace("?", "%s")
        cur = self._c.cursor()
        cur.execute(sql, params)
        return cur

    def cursor(self):
        return self._c.cursor()


# ---------- normalization ----------
from core.normalize import normalize_word  # noqa: E402,F401  (سازگاری عقب‌رو)


# ---------- schema helpers ----------

def _table_columns(c, table):
    rows = c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table,),
    ).fetchall()
    return {r["column_name"] for r in rows}


def _ensure_column(c, table, column, ddl):
    if column not in _table_columns(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init():
    with conn() as c:
        # در PostgreSQL کلید افزایشی با IDENTITY ساخته می‌شود.
        c.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id         BIGINT PRIMARY KEY,
            name            TEXT,
            display_name    TEXT,
            name_changed_at BIGINT DEFAULT 0,
            level           INTEGER DEFAULT 1,
            xp              INTEGER DEFAULT 0,
            coins           INTEGER DEFAULT 0,
            streak          INTEGER DEFAULT 0,
            last_login      TEXT DEFAULT '',
            games           INTEGER DEFAULT 0,
            wins            INTEGER DEFAULT 0,
            best_score      INTEGER DEFAULT 0,
            onboarded       INTEGER DEFAULT 0,
            accepted_words  INTEGER DEFAULT 0,
            created_at      BIGINT
        );

        CREATE TABLE IF NOT EXISTS mission_progress (
            user_id   BIGINT,
            day       TEXT,
            progress  INTEGER DEFAULT 0,
            claimed   INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, day)
        );

        CREATE TABLE IF NOT EXISTS categories (
            id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name  TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS words (
            id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            category_id     INTEGER NOT NULL,
            word            TEXT NOT NULL,
            normalized_word TEXT DEFAULT '',
            difficulty      INTEGER DEFAULT 1,
            rarity          INTEGER DEFAULT 1,
            points          INTEGER DEFAULT 10,
            synonyms        TEXT DEFAULT '',
            clue            TEXT DEFAULT '',
            usage_count     INTEGER DEFAULT 0,
            last_used_by    BIGINT,
            last_used_at    BIGINT,
            UNIQUE(category_id, word),
            FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS admins (
            user_id  BIGINT PRIMARY KEY,
            added_by BIGINT,
            added_at BIGINT
        );

        CREATE TABLE IF NOT EXISTS word_suggestions (
            id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id         BIGINT,
            user_name       TEXT,
            word            TEXT NOT NULL,
            normalized_word TEXT DEFAULT '',
            category        TEXT NOT NULL,
            description     TEXT DEFAULT '',
            source          TEXT DEFAULT 'menu',
            status          TEXT DEFAULT 'pending',
            admin_id        BIGINT,
            admin_note      TEXT DEFAULT '',
            created_at      BIGINT,
            reviewed_at     BIGINT
        );

        CREATE TABLE IF NOT EXISTS match_reports (
            id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            chat_id     BIGINT,
            mode        TEXT,
            winner_id   BIGINT,
            players     INTEGER DEFAULT 0,
            created_at  BIGINT
        );

        CREATE TABLE IF NOT EXISTS change_logs (
            id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            admin_id    BIGINT,
            action      TEXT,
            target_type TEXT,
            target_id   TEXT,
            detail      TEXT,
            created_at  BIGINT
        );

        CREATE TABLE IF NOT EXISTS lucky_boxes (
            id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id     BIGINT,
            match_id    BIGINT,
            item_type   TEXT,
            item_value  TEXT,
            rarity      TEXT,
            opened      INTEGER DEFAULT 1,
            created_at  BIGINT
        );
        """)

        # ---- migrations برای دیتابیس‌های قدیمی ----
        _ensure_column(c, "players", "display_name", "display_name TEXT")
        _ensure_column(c, "players", "name_changed_at", "name_changed_at BIGINT DEFAULT 0")
        _ensure_column(c, "players", "accepted_words", "accepted_words INTEGER DEFAULT 0")

        _ensure_column(c, "words", "difficulty", "difficulty INTEGER DEFAULT 1")
        _ensure_column(c, "words", "rarity", "rarity INTEGER DEFAULT 1")
        _ensure_column(c, "words", "points", "points INTEGER DEFAULT 10")
        _ensure_column(c, "words", "synonyms", "synonyms TEXT DEFAULT ''")
        _ensure_column(c, "words", "clue", "clue TEXT DEFAULT ''")
        _ensure_column(c, "words", "normalized_word", "normalized_word TEXT DEFAULT ''")
        _ensure_column(c, "words", "usage_count", "usage_count INTEGER DEFAULT 0")
        _ensure_column(c, "words", "last_used_by", "last_used_by BIGINT")
        _ensure_column(c, "words", "last_used_at", "last_used_at BIGINT")

        c.execute("UPDATE words SET normalized_word='' WHERE normalized_word IS NULL")

        rows = c.execute("SELECT id, word FROM words WHERE normalized_word=''").fetchall()
        for r in rows:
            c.execute(
                "UPDATE words SET normalized_word=%s WHERE id=%s",
                (normalize_word(r["word"]), r["id"]),
            )

        c.execute("CREATE INDEX IF NOT EXISTS ix_words_normalized ON words(category_id, normalized_word)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_suggestions_status ON word_suggestions(status)")

        # partial unique index (نحو یکسان در Postgres)
        c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_players_display_name
        ON players(display_name)
        WHERE display_name IS NOT NULL AND TRIM(display_name) <> ''
        """)

        init_garden(c)

    seed_defaults()
    seed_namefamily_words(clean_extra_categories=True)


# ---------- players ----------

def get_player(uid):
    with conn() as c:
        r = c.execute("SELECT * FROM players WHERE user_id=%s", (uid,)).fetchone()
    return dict(r) if r else None


def get_profile(uid):
    return get_player(uid)


def ensure_player(uid, name):
    p = get_player(uid)
    if p:
        if name and p.get("name") != name:
            with conn() as c:
                c.execute("UPDATE players SET name=%s WHERE user_id=%s", (name, uid))
        return get_player(uid)

    with conn() as c:
        c.execute(
            "INSERT INTO players(user_id, name, created_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO NOTHING",
            (uid, name or "", int(time.time())),
        )
    return get_player(uid)


def save_player(uid, **fields):
    if not fields:
        return

    with conn() as c:
        valid_cols = _table_columns(c, "players")
        bad = [k for k in fields if k not in valid_cols]
        if bad:
            raise ValueError(f"Invalid player field(s): {', '.join(bad)}")

        cols = ", ".join(f"{k}=%s" for k in fields)
        values = list(fields.values())
        values.append(uid)
        c.execute(f"UPDATE players SET {cols} WHERE user_id=%s", values)


def is_onboarded(uid):
    p = get_player(uid)
    return bool(p and p.get("onboarded"))


def mark_onboarded(uid):
    save_player(uid, onboarded=1)


def all_player_ids():
    with conn() as c:
        rows = c.execute("SELECT user_id FROM players").fetchall()
    return [r["user_id"] for r in rows]


def get_display_name(uid):
    p = get_player(uid)
    if not p:
        return None
    dn = (p.get("display_name") or "").strip()
    return dn or None


def display_name(uid, fallback=""):
    return get_display_name(uid) or fallback or f"کاربر {uid}"


def is_display_name_taken(name, exclude_uid=None):
    name = (name or "").strip()
    if not name:
        return False

    with conn() as c:
        if exclude_uid is None:
            r = c.execute(
                "SELECT 1 FROM players WHERE display_name=%s LIMIT 1",
                (name,),
            ).fetchone()
        else:
            r = c.execute(
                "SELECT 1 FROM players WHERE display_name=%s AND user_id<>%s LIMIT 1",
                (name, exclude_uid),
            ).fetchone()
    return r is not None


def name_taken(name, exclude_uid=None):
    return is_display_name_taken(name, exclude_uid=exclude_uid)


def set_display_name(uid, name):
    name = (name or "").strip()
    if not name:
        raise ValueError("display name cannot be empty")

    if is_display_name_taken(name, exclude_uid=uid):
        raise IntegrityError("display name already taken")

    ensure_player(uid, "")
    with conn() as c:
        c.execute(
            "UPDATE players SET display_name=%s, name_changed_at=%s WHERE user_id=%s",
            (name, int(time.time()), uid),
        )


def stats():
    with conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM players").fetchone()["n"]
        games = c.execute("SELECT COALESCE(SUM(games),0) s FROM players").fetchone()["s"]
        wins = c.execute("SELECT COALESCE(SUM(wins),0) s FROM players").fetchone()["s"]
        coins = c.execute("SELECT COALESCE(SUM(coins),0) s FROM players").fetchone()["s"]
        active = c.execute("SELECT COUNT(*) n FROM players WHERE streak>0").fetchone()["n"]
    return {"players": total, "games": games, "wins": wins, "coins": coins, "active": active}


# ---------- missions ----------

def get_mission_progress(uid, day):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM mission_progress WHERE user_id=%s AND day=%s",
            (uid, day),
        ).fetchone()
    return dict(r) if r else {"user_id": uid, "day": day, "progress": 0, "claimed": 0}


def bump_mission(uid, day, amount=1):
    with conn() as c:
        c.execute("""
        INSERT INTO mission_progress(user_id, day, progress)
        VALUES (%s, %s, %s)
        ON CONFLICT(user_id, day)
        DO UPDATE SET progress = mission_progress.progress + %s
        """, (uid, day, amount, amount))


def claim_mission(uid, day):
    with conn() as c:
        c.execute("""
        INSERT INTO mission_progress(user_id, day, claimed)
        VALUES (%s, %s, 1)
        ON CONFLICT(user_id, day)
        DO UPDATE SET claimed = 1
        """, (uid, day))


def claim_mission_atomic(uid, day, coins, xp):
    """اتمیک: اگر قبلاً claim نشده، claim را ثبت و سکه/XP را اعمال می‌کند.
    برمی‌گرداند True اگر جایزه داده شد، False اگر قبلاً گرفته شده بود."""
    from core.progression import add_xp
    with conn() as c:
        row = c.execute(
            "SELECT claimed FROM mission_progress WHERE user_id=%s AND day=%s",
            (uid, day)).fetchone()
        if row and row["claimed"]:
            return False
        c.execute("""INSERT INTO mission_progress(user_id, day, claimed)
                     VALUES (%s, %s, 1)
                     ON CONFLICT(user_id, day) DO UPDATE SET claimed=1""",
                  (uid, day))
        p = c.execute("SELECT level, xp, coins FROM players WHERE user_id=%s",
                      (uid,)).fetchone()
        if p:
            new_level, new_xp, _ = add_xp(p["level"], p["xp"], xp)
            c.execute("UPDATE players SET coins=%s, level=%s, xp=%s WHERE user_id=%s",
                      (p["coins"] + coins, new_level, new_xp, uid))
    return True


# ---------- leaderboard ----------

def top_players(limit=10):
    """لیدربورد کلی بر اساس best_score.

    مرتب‌سازی قطعی (deterministic) طبق game.ranking:
      1) best_score نزولی  2) wins نزولی  3) user_id صعودی (ثبت‌نام زودتر)
    این تضمین می‌کند بازیکن با امتیاز کمتر هرگز بالاتر از بازیکن با امتیاز بیشتر
    نمایش داده نشود، و در تساوی همیشه ترتیب یکسان و قطعی باشد.
    خروجی نهایی قبل از برگشت با ranking.assert_sorted اعتبارسنجی می‌شود.
    """
    from game import ranking
    with conn() as c:
        rows = c.execute("""
        SELECT
            COALESCE(NULLIF(display_name, ''), name, 'کاربر') AS shown_name,
            best_score,
            wins,
            user_id
        FROM players
        ORDER BY best_score DESC, wins DESC, user_id ASC
        LIMIT %s
        """, (limit,)).fetchall()

    result = [(r["shown_name"], r["best_score"]) for r in rows]
    # گارد نهایی: اگر به هر دلیلی ترتیب خراب بود، همین‌جا شکست می‌خورد.
    ranking.assert_sorted(result, score_getter=lambda t: t[1])
    return result


# ---------- categories & words ----------

def add_category(name):
    name = (name or "").strip()
    if not name:
        return False

    try:
        with conn() as c:
            c.execute("INSERT INTO categories(name) VALUES (%s)", (name,))
        return True
    except IntegrityError:
        return False


def del_category(name):
    with conn() as c:
        cur = c.execute("DELETE FROM categories WHERE name=%s", ((name or "").strip(),))
        return cur.rowcount > 0


def get_category(name):
    with conn() as c:
        r = c.execute("SELECT * FROM categories WHERE name=%s", ((name or "").strip(),)).fetchone()
    return dict(r) if r else None


def list_categories():
    with conn() as c:
        rows = c.execute("""
        SELECT cat.name,
               (SELECT COUNT(*) FROM words w WHERE w.category_id=cat.id) cnt
        FROM categories cat
        ORDER BY cat.name
        """).fetchall()
    return [(r["name"], r["cnt"]) for r in rows]


def find_word(category, word):
    """جستجوی کلمه با نرمال‌سازی، در یک دسته‌ی مشخص."""
    cat = get_category(category)
    if not cat:
        return None

    nw = normalize_word(word)

    with conn() as c:
        r = c.execute("""
            SELECT *
            FROM words
            WHERE category_id=%s AND normalized_word=%s
            LIMIT 1
        """, (cat["id"], nw)).fetchone()

    return dict(r) if r else None


def word_exists(category, word):
    return find_word(category, word) is not None


def add_word(category, word, difficulty=1, rarity=1, points=10, synonyms="", clue=""):
    category = (category or "").strip()
    word = (word or "").strip()

    if not category or not word:
        return False

    cat = get_category(category)
    if not cat:
        if not add_category(category):
            return False
        cat = get_category(category)

    if find_word(category, word):
        return False

    if isinstance(synonyms, (list, tuple, set)):
        synonyms = "،".join(str(x).strip() for x in synonyms if str(x).strip())

    try:
        with conn() as c:
            c.execute("""
                INSERT INTO words(
                    category_id, word, normalized_word,
                    difficulty, rarity, points, synonyms, clue
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                cat["id"],
                word,
                normalize_word(word),
                int(difficulty or 1),
                int(rarity or 1),
                int(points or 10),
                synonyms or "",
                clue or "",
            ))
        return True
    except IntegrityError:
        return False


def del_word(category, word):
    cat = get_category(category)
    if not cat:
        return False

    with conn() as c:
        cur = c.execute(
            "DELETE FROM words WHERE category_id=%s AND word=%s",
            (cat["id"], (word or "").strip()),
        )
        return cur.rowcount > 0


def list_words(category):
    cat = get_category(category)
    if not cat:
        return None

    with conn() as c:
        rows = c.execute(
            "SELECT word FROM words WHERE category_id=%s ORDER BY word",
            (cat["id"],),
        ).fetchall()

    return [r["word"] for r in rows]


def lex_rows(category):
    cat = get_category(category)
    if not cat:
        return []

    with conn() as c:
        rows = c.execute("""
        SELECT word, difficulty, rarity, points, synonyms, clue, usage_count
        FROM words
        WHERE category_id=%s
        ORDER BY word
        """, (cat["id"],)).fetchall()

    return [dict(r) for r in rows]


def clue_pool():
    with conn() as c:
        rows = c.execute("""
        SELECT w.word, w.clue, c.name AS category
        FROM words w
        JOIN categories c ON c.id = w.category_id
        WHERE TRIM(COALESCE(w.clue, '')) <> ''
        ORDER BY w.usage_count ASC, w.word ASC
        """).fetchall()

    return [dict(r) for r in rows]


def bump_word_use(category, word, user_id=None):
    cat = get_category(category)
    if not cat:
        return False

    with conn() as c:
        cur = c.execute("""
        UPDATE words
        SET usage_count = COALESCE(usage_count, 0) + 1,
            last_used_by = %s,
            last_used_at = %s
        WHERE category_id=%s AND word=%s
        """, (user_id, int(time.time()), cat["id"], (word or "").strip()))
        return cur.rowcount > 0


def random_category():
    import random

    cats = [n for n, cnt in list_categories() if cnt > 0]
    return random.choice(cats) if cats else None


def import_words(records):
    added = 0
    skipped = 0
    with conn() as c:
        for r in records:
            if not isinstance(r, dict):
                skipped += 1
                continue
            word = (r.get("word") or r.get("کلمه") or "").strip()
            category = (r.get("category") or r.get("cat") or r.get("دسته") or "").strip()
            if not word or not category:
                skipped += 1
                continue
            cat = c.execute("SELECT id FROM categories WHERE name=%s", (category,)).fetchone()
            if not cat:
                try:
                    c.execute("INSERT INTO categories(name) VALUES (%s)", (category,))
                    cat_id = c.execute("SELECT id FROM categories WHERE name=%s", (category,)).fetchone()["id"]
                except IntegrityError:
                    skipped += 1
                    continue
            else:
                cat_id = cat["id"]
            nw = normalize_word(word)
            exists = c.execute(
                "SELECT 1 FROM words WHERE category_id=%s AND normalized_word=%s LIMIT 1",
                (cat_id, nw)).fetchone()
            if exists:
                skipped += 1
                continue
            try:
                c.execute("""INSERT INTO words(category_id, word, normalized_word,
                             difficulty, rarity, points, synonyms, clue)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (cat_id, word, nw,
                           int(r.get("difficulty", 1) or 1),
                           int(r.get("rarity", 1) or 1),
                           int(r.get("points", 10) or 10),
                           r.get("synonyms", "") or "",
                           r.get("clue", "") or ""))
                added += 1
            except IntegrityError:
                skipped += 1
    return added, skipped


def seed_defaults():
    if list_categories():
        return

    seed = {
        "خوراکی": ["سیب", "نان", "پنیر", "ماست", "خرما", "کباب", "قورمه", "آش"],
        "حیوانات": ["شیر", "ببر", "گربه", "اسب", "فیل", "روباه", "خرگوش", "عقاب"],
        "شهرها": ["تهران", "شیراز", "اصفهان", "تبریز", "مشهد", "یزد", "رشت", "اهواز"],
        "ورزش": ["فوتبال", "والیبال", "شنا", "دو", "کشتی", "بسکتبال", "تنیس", "اسکی"],
    }

    for cat, words in seed.items():
        add_category(cat)
        for w in words:
            add_word(cat, w)


# ---------- word suggestions ----------

def add_suggestion(user_id, user_name, word, category, description="", source="menu"):
    word = (word or "").strip()
    category = (category or "").strip()
    description = (description or "").strip()

    if not word or not category:
        return False

    if word_exists(category, word):
        return False

    with conn() as c:
        cat = get_category(category)
        cat_id = cat["id"] if cat else None

        if cat_id:
            dup = c.execute(
                "SELECT 1 FROM words WHERE category_id=%s AND normalized_word=%s LIMIT 1",
                (cat_id, normalize_word(word))
            ).fetchone()

            if dup:
                return False

        pending = c.execute(
            """SELECT 1 FROM word_suggestions
               WHERE category=%s AND normalized_word=%s
               AND status='pending' LIMIT 1""",
            (category, normalize_word(word))
        ).fetchone()

        if pending:
            return False

        c.execute("""
            INSERT INTO word_suggestions(
                user_id, user_name, word, normalized_word,
                category, description, source, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
        """, (
            user_id,
            user_name or "",
            word,
            normalize_word(word),
            category,
            description,
            source,
            int(time.time())
        ))

    return True


def pending_suggestions(limit=10):
    with conn() as c:
        rows = c.execute("""
            SELECT *
            FROM word_suggestions
            WHERE status='pending'
            ORDER BY created_at ASC
            LIMIT %s
        """, (limit,)).fetchall()

    return [dict(r) for r in rows]


def get_suggestion(sid):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM word_suggestions WHERE id=%s",
            (sid,)
        ).fetchone()

    return dict(r) if r else None


def approve_suggestion(sid, admin_id, new_word=None, new_category=None):
    s = get_suggestion(sid)
    if not s or s["status"] != "pending":
        return False, "پیشنهاد پیدا نشد یا قبلاً بررسی شده."

    word = (new_word or s["word"]).strip()
    category = (new_category or s["category"]).strip()
    nw = normalize_word(word)
    now = int(time.time())

    with conn() as c:
        cat = c.execute("SELECT id FROM categories WHERE name=%s", (category,)).fetchone()
        if not cat:
            c.execute("INSERT INTO categories(name) VALUES (%s)", (category,))
            cat_id = c.execute("SELECT id FROM categories WHERE name=%s", (category,)).fetchone()["id"]
        else:
            cat_id = cat["id"]

        dup = c.execute("SELECT 1 FROM words WHERE category_id=%s AND normalized_word=%s LIMIT 1",
                        (cat_id, nw)).fetchone()
        ok = False
        if not dup:
            try:
                c.execute("""INSERT INTO words(category_id, word, normalized_word)
                             VALUES (%s,%s,%s)""", (cat_id, word, nw))
                ok = True
            except IntegrityError:
                ok = False

        c.execute("""UPDATE word_suggestions
                     SET status='approved', word=%s, normalized_word=%s, category=%s,
                         admin_id=%s, reviewed_at=%s WHERE id=%s""",
                  (word, nw, category, admin_id, now, sid))
        c.execute("UPDATE players SET accepted_words=COALESCE(accepted_words,0)+1 WHERE user_id=%s",
                  (s["user_id"],))
        c.execute("""INSERT INTO change_logs(admin_id, action, target_type, target_id, detail, created_at)
                     VALUES (%s, 'approve_suggestion', 'word_suggestion', %s, %s, %s)""",
                  (admin_id, str(sid), f"{word} -> {category}, inserted={ok}", now))

    return True, "پیشنهاد تأیید شد و کلمه به دیتابیس اضافه شد."


def reject_suggestion(sid, admin_id, note=""):
    s = get_suggestion(sid)
    if not s or s["status"] != "pending":
        return False

    with conn() as c:
        c.execute("""
            UPDATE word_suggestions
            SET status='rejected',
                admin_id=%s,
                admin_note=%s,
                reviewed_at=%s
            WHERE id=%s
        """, (
            admin_id,
            note or "",
            int(time.time()),
            sid
        ))

        c.execute("""
            INSERT INTO change_logs(admin_id, action, target_type, target_id, detail, created_at)
            VALUES (%s, 'reject_suggestion', 'word_suggestion', %s, %s, %s)
        """, (
            admin_id,
            str(sid),
            note or "",
            int(time.time())
        ))

    return True


def edit_suggestion(sid, admin_id, word=None, category=None, description=None):
    s = get_suggestion(sid)
    if not s or s["status"] != "pending":
        return False

    new_word = (word or s["word"]).strip()
    new_category = (category or s["category"]).strip()
    new_description = description if description is not None else s["description"]

    with conn() as c:
        c.execute("""
            UPDATE word_suggestions
            SET word=%s,
                normalized_word=%s,
                category=%s,
                description=%s,
                admin_id=%s
            WHERE id=%s
        """, (
            new_word,
            normalize_word(new_word),
            new_category,
            new_description or "",
            admin_id,
            sid
        ))

        c.execute("""
            INSERT INTO change_logs(admin_id, action, target_type, target_id, detail, created_at)
            VALUES (%s, 'edit_suggestion', 'word_suggestion', %s, %s, %s)
        """, (
            admin_id,
            str(sid),
            f"{new_word} -> {new_category}",
            int(time.time())
        ))

    return True


def suggestion_stats_for_user(uid):
    with conn() as c:
        total = c.execute("""
            SELECT COUNT(*) n
            FROM word_suggestions
            WHERE user_id=%s
        """, (uid,)).fetchone()["n"]

        approved = c.execute("""
            SELECT COUNT(*) n
            FROM word_suggestions
            WHERE user_id=%s AND status='approved'
        """, (uid,)).fetchone()["n"]

    return {"total": total, "approved": approved}


# ---------- match reports ----------

def add_match_report(chat_id, mode, winner_id, players_count):
    with conn() as c:
        cur = c.execute("""
            INSERT INTO match_reports(chat_id, mode, winner_id, players, created_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            chat_id,
            mode,
            winner_id,
            players_count,
            int(time.time())
        ))
        return cur.fetchone()["id"]


def latest_match_reports(limit=10):
    with conn() as c:
        rows = c.execute("""
            SELECT *
            FROM match_reports
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,)).fetchall()

    return [dict(r) for r in rows]


# ---------- lucky box ----------

def add_lucky_box(user_id, match_id, item_type, item_value, rarity):
    with conn() as c:
        c.execute("""
            INSERT INTO lucky_boxes(user_id, match_id, item_type, item_value, rarity, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            match_id,
            item_type,
            str(item_value),
            rarity,
            int(time.time())
        ))


# ---------- admins ----------

def is_db_admin(uid):
    with conn() as c:
        r = c.execute("SELECT 1 FROM admins WHERE user_id=%s", (uid,)).fetchone()
    return r is not None


def add_admin(uid, by):
    with conn() as c:
        c.execute("""
        INSERT INTO admins(user_id, added_by, added_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING
        """, (uid, by, int(time.time())))


def del_admin(uid):
    with conn() as c:
        cur = c.execute("DELETE FROM admins WHERE user_id=%s", (uid,))
        return cur.rowcount > 0


def list_admins():
    with conn() as c:
        rows = c.execute("SELECT user_id FROM admins ORDER BY added_at DESC").fetchall()
    return [r["user_id"] for r in rows]


# ---------- NameFamily fixed categories seed ----------

NAMEFAMILY_ALLOWED_CATEGORIES = ["غذا", "رنگ", "میوه", "حیوان", "اشیا", "عضو بدن", "شهر", "کشور", "شغل"]

NAMEFAMILY_WORD_BANK = {
    "غذا": ["آب", "آبگوشت", "آش", "آش رشته", "آش دوغ", "آجیل", "املت", "برنج", "باقالی پلو", "بستنی", "بیسکویت", "پاستا", "پنیر", "پیتزا", "تخم مرغ", "ترشی", "ته چین", "جوجه کباب", "چای", "چلوکباب", "چلوگوشت", "چیپس", "حلوا", "حلیم", "حمص", "خوراک لوبیا", "خوراک مرغ", "خورشت آلو", "خورشت به", "خورشت کرفس", "خرما", "دلمه", "دوغ", "دونات", "دمپختک", "رولت", "زرشک پلو", "ژله", "سالاد", "سالاد الویه", "ساندویچ", "سوپ", "سوشی", "سوهان", "شامی", "شله زرد", "شیر", "شیرینی", "شکلات", "عدس پلو", "عدسی", "عسل", "فسنجان", "فلافل", "فرنی", "قهوه", "قورمه سبزی", "قیمه", "قطاب", "کباب", "کباب کوبیده", "کشک بادمجان", "کتلت", "کیک", "کلوچه", "کله پاچه", "کوکو", "کمپوت", "گز", "لازانیا", "لوبیا پلو", "ماست", "ماکارونی", "مربا", "مرصع پلو", "میرزاقاسمی", "نان", "نان بربری", "نان تافتون", "نان سنگک", "نان لواش", "نوشابه", "وافل", "یتیمچه"],
    "رنگ": ["آبی", "آبی آسمانی", "آبی کبالت", "آبی نفتی", "آجری", "آکوامارین", "آلبالویی", "ارغوانی", "استخوانی", "اسطوخودوسی", "بادمجانی", "بژ", "بنفش", "بورگاندی", "پسته ای", "خاکستری", "خاکی", "خردلی", "دودی", "رزگلد", "زرشکی", "زرد", "زیتونی", "سبز", "سبز آبی", "سبز چمنی", "سبز زمردی", "سدری", "سرخابی", "سرمه ای", "سفید", "سیاه", "شامپاینی", "صدفی", "صورتی", "طاووسی", "طلایی", "عنابی", "فیروزه ای", "قرمز", "قهوه ای", "کبالت", "کرم", "کاراملی", "کهربایی", "گرافیتی", "لاجوردی", "لیمویی", "مسی", "مرجانی", "مرمری", "مشکی", "موشی", "ماشی", "نارنجی", "نخودی", "نقره ای", "نیلی", "یاسی", "یشمی"],
    "میوه": ["آلبالو", "آلو", "آلوچه", "آناناس", "انار", "انبه", "انجیر", "انگور", "ازگیل", "بالنگ", "به", "پاپایا", "پرتقال", "تمشک", "توت", "توت فرنگی", "خرمالو", "خرما", "خیار", "دارابی", "ذغال اخته", "زرشک", "زالزالک", "زردآلو", "سنجد", "سیب", "شاه توت", "شلیل", "طالبی", "عناب", "غوره", "گریپ فروت", "گلابی", "گوجه سبز", "گیلاس", "لیمو", "لیمو ترش", "لیمو شیرین", "موز", "نارگیل", "نارنج", "نارنگی", "هلو", "هندوانه", "کیوی", "کامکوات", "کنار"],
    "حیوان": ["آهو", "آفتاب پرست", "آناکوندا", "اسب", "اسب آبی", "اختاپوس", "اردک", "الاغ", "ایگوانا", "ببر", "بز", "بوفالو", "تمساح", "جغد", "خر", "خرچنگ", "خرس", "خرگوش", "خفاش", "دلفین", "راکون", "راسو", "روباه", "زرافه", "زنبور", "سگ", "سنجاب", "سمندر", "سوسک", "سوسمار", "شامپانزه", "شاهین", "شتر", "شترمرغ", "شیر", "طاووس", "طوطی", "عقاب", "عقرب", "غاز", "فیل", "فلامینگو", "قناری", "قورباغه", "قو", "کبوتر", "کبرا", "کرم", "کرگدن", "کفتار", "کلاغ", "کوسه", "کوالا", "گاو", "گربه", "گوسفند", "گنجشک", "گوزن", "گورخر", "گرگ", "لاک پشت", "لاما", "مار", "مارمولک", "ماهی", "مرغ", "مگس", "ملخ", "میمون", "مورچه", "نهنگ", "یوزپلنگ"],
    "اشیا": ["آچار", "آینه", "اتو", "اجاق", "اره", "اره برقی", "اسکنر", "انبردست", "بالش", "باتری", "بشقاب", "بطری", "پتو", "پرده", "پرینتر", "پنجره", "پیچ گوشتی", "تابه", "تخت", "تلویزیون", "تلسکوپ", "جارو", "جاروبرقی", "جعبه", "چراغ", "چراغ قوه", "چاقو", "چتر", "چکش", "چمدان", "چنگال", "خودکار", "در", "دریل", "دفتر", "دوربین", "دکمه", "رادیو", "رایانه", "روتر", "زیپ", "ساعت", "سطل", "سشوار", "سوزن", "سه پایه", "شارژر", "شانه", "صندلی", "ظرف", "عینک", "فرش", "فشارسنج", "فلش مموری", "قابلمه", "قالی", "قاشق", "قفل", "قیچی", "قطب نما", "کابل", "کارت گرافیک", "کاغذ", "کلاه", "کلید", "کمد", "کتاب", "کفش", "کیبورد", "کیف", "گلدان", "گوشی", "لیوان", "لپ تاپ", "لباس", "مایکروویو", "ماشین لباسشویی", "مادربرد", "ماوس", "مداد", "میز", "میکروسکوپ", "مودم", "مانیتور", "نخ", "نردبان", "هدفون", "هارددیسک", "یخچال"],
    "عضو بدن": ["آرنج", "ابرو", "استخوان", "انگشت", "بازو", "بافت", "بینی", "پا", "پاشنه", "پوست", "پیشانی", "تاندون", "ترقوه", "جمجمه", "چانه", "چشم", "حنجره", "حلق", "خون", "دست", "دندان", "دل", "دهان", "رگ", "رباط", "ریه", "زانو", "زبان", "ستون فقرات", "سر", "شبکیه", "شانه", "طحال", "عصب", "عضله", "غضروف", "قلب", "قرنیه", "کبد", "کتف", "کف دست", "کلیه", "کمر", "گونه", "گوش", "گردن", "لب", "لوزالمعده", "مچ", "مردمک", "مری", "معده", "مغز", "مخچه", "مفصل", "مو", "مویرگ", "ناخن", "نای"],
    "شهر": ["آبادان", "آستارا", "آمل", "اردبیل", "اراک", "ارومیه", "اصفهان", "اهواز", "ایلام", "انزلی", "بابل", "بابلسر", "بانه", "بجنورد", "بروجرد", "بم", "بندرعباس", "بوشهر", "بیرجند", "بهبهان", "تبریز", "تنکابن", "تهران", "جیرفت", "چابهار", "چالوس", "خرم آباد", "خرمشهر", "خوی", "دامغان", "دزفول", "رامسر", "رشت", "رفسنجان", "زاهدان", "زنجان", "ساری", "ساوه", "سبزوار", "سقز", "سنندج", "سیرجان", "شاهرود", "شاهین شهر", "شهرکرد", "شیراز", "قائم شهر", "قائن", "قزوین", "قم", "قشم", "کاشان", "کرج", "کرمان", "کرمانشاه", "کیش", "گرگان", "لاهیجان", "لنگرود", "محلات", "مراغه", "مرند", "مشهد", "ملایر", "مهاباد", "میناب", "نهاوند", "نیشابور", "همدان", "یزد", "یاسوج"],
    "کشور": ["آذربایجان", "آرژانتین", "آلمان", "آمریکا", "اتریش", "اردن", "ارمنستان", "استرالیا", "اسپانیا", "اسلواکی", "اسلوونی", "افغانستان", "امارات", "اندونزی", "انگلیس", "ایران", "ایتالیا", "ایسلند", "بحرین", "برزیل", "بلژیک", "بلغارستان", "بنگلادش", "بوتان", "بوتسوانا", "بوسنی", "پاکستان", "پرتغال", "پرو", "تاجیکستان", "تایلند", "ترکمنستان", "ترکیه", "چین", "دانمارک", "روسیه", "رومانی", "ژاپن", "سوریه", "سوئد", "سوئیس", "سنگال", "عراق", "عمان", "فرانسه", "فنلاند", "فیلیپین", "قطر", "قرقیزستان", "قزاقستان", "کانادا", "کامبوج", "کلمبیا", "کره", "کویت", "گرجستان", "لبنان", "لائوس", "لهستان", "ماداگاسکار", "مالزی", "مصر", "مکزیک", "مغولستان", "موزامبیک", "نروژ", "نپال", "نیوزیلند", "هلند", "هند", "ویتنام", "یمن", "یونان"],
    "شغل": ["آتش نشان", "آرایشگر", "آشپز", "استاد", "اقتصاددان", "بازیگر", "بازاریاب", "باغبان", "باستان شناس", "برنامه نویس", "برق کار", "پرستار", "پلیس", "پزشک", "تحلیلگر", "تدوینگر", "جراح", "خبرنگار", "خلبان", "خیاط", "داده کاو", "دامپزشک", "داروساز", "دندان پزشک", "راننده", "روان شناس", "روزنامه نگار", "زیست شناس", "ستاره شناس", "سرباز", "صندوقدار", "صدابردار", "طراح", "طراح تجربه کاربر", "عکاس", "فیلمبردار", "فروشنده", "قاضی", "کارآفرین", "کارگردان", "کارگر", "کارشناس امنیت", "کشاورز", "کتابدار", "گرافیست", "لوله کش", "مترجم", "مدیر", "مدیر محصول", "مربی", "ملوان", "منشی", "مهندس", "معمار", "معلم", "مکانیک", "نانوا", "نجار", "نقاش", "نگهبان", "نورپرداز", "نویسنده", "ورزشکار", "وکیل", "هواشناس"]
}


def seed_namefamily_words(clean_extra_categories=True):
    allowed = set(NAMEFAMILY_ALLOWED_CATEGORIES)
    with conn() as c:
        if clean_extra_categories:
            rows = c.execute("SELECT name FROM categories").fetchall()
            for r in rows:
                if r["name"] not in allowed:
                    c.execute("DELETE FROM categories WHERE name=%s", (r["name"],))
        for cat in NAMEFAMILY_ALLOWED_CATEGORIES:
            c.execute("INSERT INTO categories(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (cat,))
            cat_id = c.execute("SELECT id FROM categories WHERE name=%s", (cat,)).fetchone()["id"]
            for word in NAMEFAMILY_WORD_BANK.get(cat, []):
                w = (word or "").strip()
                if not w:
                    continue
                nw = normalize_word(w)
                exists = c.execute("SELECT 1 FROM words WHERE category_id=%s AND normalized_word=%s LIMIT 1", (cat_id, nw)).fetchone()
                if exists:
                    continue
                c.execute("""
                    INSERT INTO words(category_id, word, normalized_word, difficulty, rarity, points)
                    VALUES (%s, %s, %s, 1, 1, 10)
                    ON CONFLICT (category_id, word) DO NOTHING
                """, (cat_id, w, nw))
    return True


# ---------- garden (delegation) ----------
from core.garden_db import GardenAPI as _GardenAPI  # noqa: E402
_garden = _GardenAPI(conn)

def garden_ensure_starter(uid, name=""):        return _garden.ensure_starter(uid, name)
def garden_add_growth(uid, amount, source="", detail=""):
                                                return _garden.add_growth(uid, amount, source, detail)
def garden_add_seed(uid, seed_type=None, qty=1, source=""):
                                                return _garden.add_seed(uid, seed_type, qty, source)
def garden_random_seed_type():                  return _garden.random_seed_type()
def garden_daily_visit(uid):                    return _garden.daily_visit(uid)
def garden_seed_inventory(uid):                 return _garden.seed_inventory(uid)
def garden_plant_seed(uid, seed_type):          return _garden.plant_seed(uid, seed_type)
def garden_harvest(uid):                        return _garden.harvest(uid)
def garden_water_left(uid):                     return _garden.water_left(uid)
def garden_water(uid, target_id):               return _garden.water(uid, target_id)
def garden_public(uid):                         return _garden.public(uid)
def garden_friend_gardens(uid, limit=8):        return _garden.friend_gardens(uid, limit)

```


================================================================================
FILE: core\garden_db.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""لایه دیتابیس باغچه‌ی کلمو — نسخه‌ی PostgreSQL.

فقط از conn() هسته استفاده می‌کند. تمام امضاهای عمومی بدون تغییر مانده‌اند.

تغییرات مهاجرت نسبت به نسخه‌ی SQLite:
- executescript → execute (psycopg چند دستور در یک رشته را اجرا می‌کند).
- placeholder «?» → «%s».
- در UPSERT، ستون‌های جدول باید با نام جدول واجد شرایط شوند
  (مثلاً garden_seeds.qty) چون در Postgres «qty» به‌تنهایی مبهم است.
- AUTOINCREMENT وجود ندارد؛ این جدول‌ها کلید طبیعی دارند (نیازی نیست).
- BIGINT برای user_id (آی‌دی‌های تلگرام بزرگ هستند).
"""
import random
import time

SEED_TYPES = ["کلمو", "شکوفه", "یاقوت", "طلایی"]
RARITY_BY_SEED = {"کلمو": "normal", "شکوفه": "blossom", "یاقوت": "rare", "طلایی": "golden"}
DAILY_WATER_QUOTA = 5
HARVEST_AT = 100


def init_garden(c):
    """جدول‌ها را می‌سازد. c یک اتصال باز از conn() است."""
    c.execute("""
    CREATE TABLE IF NOT EXISTS garden_players (
        user_id     BIGINT PRIMARY KEY,
        name        TEXT DEFAULT '',
        last_visit  TEXT DEFAULT '',
        water_day   TEXT DEFAULT '',
        water_used  INTEGER DEFAULT 0,
        created_at  BIGINT
    );
    CREATE TABLE IF NOT EXISTS garden_trees (
        user_id        BIGINT PRIMARY KEY,
        seed_type      TEXT DEFAULT 'کلمو',
        rarity         TEXT DEFAULT 'normal',
        growth         INTEGER DEFAULT 0,
        pending_coins  INTEGER DEFAULT 0,
        pending_xp     INTEGER DEFAULT 0,
        pending_boxes  INTEGER DEFAULT 0,
        planted_at     BIGINT
    );
    CREATE TABLE IF NOT EXISTS garden_seeds (
        user_id    BIGINT,
        seed_type  TEXT,
        qty        INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, seed_type)
    );
    """)


def _today():
    import datetime
    return datetime.date.today().isoformat()


def random_seed_type():
    return random.choices(SEED_TYPES, weights=[60, 22, 13, 5], k=1)[0]


class GardenAPI:
    """با یک تابع conn (context manager) مقداردهی می‌شود."""
    def __init__(self, conn_factory):
        self._conn = conn_factory

    # ---- setup ----
    def ensure_starter(self, uid, name=""):
        with self._conn() as c:
            init_garden(c)
            row = c.execute("SELECT 1 FROM garden_players WHERE user_id=%s", (uid,)).fetchone()
            if not row:
                c.execute("INSERT INTO garden_players(user_id, name, created_at) VALUES (%s,%s,%s) "
                          "ON CONFLICT (user_id) DO NOTHING",
                          (uid, name or "", int(time.time())))
                # بذر شروع
                c.execute("""INSERT INTO garden_seeds(user_id, seed_type, qty) VALUES (%s, 'کلمو', 1)
                             ON CONFLICT(user_id, seed_type)
                             DO UPDATE SET qty = garden_seeds.qty + 1""", (uid,))
            elif name:
                c.execute("UPDATE garden_players SET name=%s WHERE user_id=%s", (name, uid))

    # ---- growth / seeds ----
    def add_growth(self, uid, amount, source="", detail=""):
        self.ensure_starter(uid)
        with self._conn() as c:
            tree = c.execute("SELECT growth FROM garden_trees WHERE user_id=%s", (uid,)).fetchone()
            if not tree:
                return  # درختی کاشته نشده
            new_growth = min(HARVEST_AT, int(tree["growth"]) + int(amount))
            grew = new_growth - int(tree["growth"])
            # هر واحد رشد → سکه/xp در انتظار برداشت
            c.execute("""UPDATE garden_trees
                         SET growth=%s, pending_coins=pending_coins+%s, pending_xp=pending_xp+%s
                         WHERE user_id=%s""",
                      (new_growth, grew * 2, grew, uid))

    def add_seed(self, uid, seed_type=None, qty=1, source=""):
        self.ensure_starter(uid)
        st = seed_type or random_seed_type()
        with self._conn() as c:
            c.execute("""INSERT INTO garden_seeds(user_id, seed_type, qty) VALUES (%s,%s,%s)
                         ON CONFLICT(user_id, seed_type)
                         DO UPDATE SET qty = garden_seeds.qty + %s""",
                      (uid, st, qty, qty))
        return st

    def random_seed_type(self):
        return random_seed_type()

    def daily_visit(self, uid):
        self.ensure_starter(uid)
        today = _today()
        with self._conn() as c:
            row = c.execute("SELECT last_visit FROM garden_players WHERE user_id=%s", (uid,)).fetchone()
            if row and row["last_visit"] == today:
                return False
            c.execute("UPDATE garden_players SET last_visit=%s WHERE user_id=%s", (today, uid))
        self.add_growth(uid, 5, source="daily_visit")
        return True

    # ---- inventory / planting ----
    def seed_inventory(self, uid):
        self.ensure_starter(uid)
        with self._conn() as c:
            rows = c.execute("""SELECT seed_type, qty FROM garden_seeds
                                WHERE user_id=%s AND qty>0 ORDER BY seed_type""", (uid,)).fetchall()
        return [dict(r) for r in rows]

    def plant_seed(self, uid, seed_type):
        self.ensure_starter(uid)
        with self._conn() as c:
            existing = c.execute("SELECT growth FROM garden_trees WHERE user_id=%s", (uid,)).fetchone()
            if existing and int(existing["growth"]) < HARVEST_AT:
                return False, "یه درخت در حال رشد داری. اول برداشتش کن."
            seed = c.execute("SELECT qty FROM garden_seeds WHERE user_id=%s AND seed_type=%s",
                             (uid, seed_type)).fetchone()
            if not seed or int(seed["qty"]) <= 0:
                return False, "این بذر رو نداری."
            c.execute("UPDATE garden_seeds SET qty=qty-1 WHERE user_id=%s AND seed_type=%s",
                      (uid, seed_type))
            rarity = RARITY_BY_SEED.get(seed_type, "normal")
            c.execute("""INSERT INTO garden_trees(user_id, seed_type, rarity, growth,
                         pending_coins, pending_xp, pending_boxes, planted_at)
                         VALUES (%s,%s,%s,0,0,0,0,%s)
                         ON CONFLICT(user_id) DO UPDATE SET
                            seed_type=excluded.seed_type, rarity=excluded.rarity,
                            growth=0, pending_coins=0, pending_xp=0, pending_boxes=0,
                            planted_at=excluded.planted_at""",
                      (uid, seed_type, rarity, int(time.time())))
        return True, f"بذر «{seed_type}» کاشته شد 🌱"

    def harvest(self, uid):
        self.ensure_starter(uid)
        with self._conn() as c:
            tree = c.execute("SELECT * FROM garden_trees WHERE user_id=%s", (uid,)).fetchone()
            if not tree:
                return False, "درختی برای برداشت نداری.", None
            if int(tree["growth"]) < HARVEST_AT:
                return False, f"درخت هنوز آماده نیست ({int(tree['growth'])}٪).", None
            coins = int(tree["pending_coins"])
            xp = int(tree["pending_xp"])
            boxes = int(tree["pending_boxes"])
            # جایزه به بازیکن اصلی (جدول players هسته)
            p = c.execute("SELECT level, xp, coins FROM players WHERE user_id=%s", (uid,)).fetchone()
            if p:
                from core.progression import add_xp
                nl, nx, _ = add_xp(p["level"], p["xp"], xp)
                c.execute("UPDATE players SET coins=%s, level=%s, xp=%s WHERE user_id=%s",
                          (p["coins"] + coins, nl, nx, uid))
            # درخت برداشت شد → پاک
            c.execute("DELETE FROM garden_trees WHERE user_id=%s", (uid,))
            # شانس بذر جایزه
            reward_seed = None
            if random.random() < 0.4:
                reward_seed = random_seed_type()
                c.execute("""INSERT INTO garden_seeds(user_id, seed_type, qty) VALUES (%s,%s,1)
                             ON CONFLICT(user_id, seed_type)
                             DO UPDATE SET qty = garden_seeds.qty + 1""",
                          (uid, reward_seed))
        return True, "برداشت شد!", {"coins": coins, "xp": xp, "boxes": boxes, "seed": reward_seed}

    # ---- watering ----
    def water_left(self, uid):
        self.ensure_starter(uid)
        today = _today()
        with self._conn() as c:
            row = c.execute("SELECT water_day, water_used FROM garden_players WHERE user_id=%s",
                            (uid,)).fetchone()
            if not row or row["water_day"] != today:
                return DAILY_WATER_QUOTA
            return max(0, DAILY_WATER_QUOTA - int(row["water_used"]))

    def water(self, uid, target_id):
        if uid == target_id:
            return False, "درخت خودت رو نمی‌تونی با سهمیه‌ی دوستان آبیاری کنی."
        self.ensure_starter(uid)
        self.ensure_starter(target_id)
        today = _today()
        with self._conn() as c:
            row = c.execute("SELECT water_day, water_used FROM garden_players WHERE user_id=%s",
                            (uid,)).fetchone()
            used = int(row["water_used"]) if row and row["water_day"] == today else 0
            if used >= DAILY_WATER_QUOTA:
                return False, "سهمیه‌ی آبیاری امروزت تموم شده."
            tree = c.execute("SELECT growth FROM garden_trees WHERE user_id=%s", (target_id,)).fetchone()
            if not tree:
                return False, "این باغ درختی نداره."
            c.execute("UPDATE garden_players SET water_day=%s, water_used=%s WHERE user_id=%s",
                      (today, used + 1, uid))
        self.add_growth(target_id, 3, source="friend_water")
        return True, "آبیاری شد 💧 (+۳٪ رشد)"

    # ---- views ----
    def public(self, uid):
        self.ensure_starter(uid)
        with self._conn() as c:
            gp = c.execute("SELECT name FROM garden_players WHERE user_id=%s", (uid,)).fetchone()
            tree = c.execute("SELECT * FROM garden_trees WHERE user_id=%s", (uid,)).fetchone()
            seeds = c.execute("SELECT seed_type, qty FROM garden_seeds WHERE user_id=%s AND qty>0",
                              (uid,)).fetchall()
        return {
            "name": (gp["name"] if gp and gp["name"] else f"کاربر {uid}"),
            "tree": dict(tree) if tree else None,
            "seeds": [dict(s) for s in seeds],
        }

    def friend_gardens(self, uid, limit=8):
        self.ensure_starter(uid)
        with self._conn() as c:
            rows = c.execute("""
                SELECT t.user_id, t.growth,
                       COALESCE(NULLIF(p.display_name,''), p.name, gp.name) AS shown_name
                FROM garden_trees t
                LEFT JOIN players p ON p.user_id = t.user_id
                LEFT JOIN garden_players gp ON gp.user_id = t.user_id
                WHERE t.user_id <> %s
                ORDER BY t.growth DESC
                LIMIT %s""", (uid, limit)).fetchall()
        return [dict(r) for r in rows]

```


================================================================================
FILE: core\missions.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""تعریف ماموریت‌های روزانه."""
import datetime

MISSIONS = [
    {"key": "play3",   "text": "۳ بازی انجام بده",            "goal": 3, "type": "games",   "coins": 50, "xp": 30},
    {"key": "win1",    "text": "۱ بازی ببر",                  "goal": 1, "type": "wins",    "coins": 60, "xp": 40},
    {"key": "answer10","text": "۱۰ جواب درست بده",            "goal": 10,"type": "answers", "coins": 70, "xp": 50},
    {"key": "suggest1","text": "۱ کلمه جدید پیشنهاد بده",     "goal": 1, "type": "suggest", "coins": 40, "xp": 25},
    {"key": "streak",  "text": "امروز هم سر بزن (ورود روزانه)","goal": 1, "type": "login",   "coins": 30, "xp": 15},
]

def mission_of_day(day_str=None):
    if day_str is None:
        day_str = datetime.date.today().isoformat()
    idx = sum(ord(c) for c in day_str) % len(MISSIONS)
    return MISSIONS[idx]

```


================================================================================
FILE: core\normalize.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""نرمال‌سازی مرکزی کلمات فارسی — همه‌ی مودها و دیتابیس از همین استفاده می‌کنند."""
import re

_AR_FA = str.maketrans({
    "ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ؤ": "و", "ئ": "ی",
})
# اعراب و علائم کوچک عربی
_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_SEPARATORS = re.compile(r"[\s\u200c\u200d\-ـ_]+")


def normalize_word(text):
    """برای مقایسه دقیق واژه‌ها: فاصله، نیم‌فاصله، خط فاصله و کشیده نادیده گرفته می‌شوند."""
    s = (text or "").strip().translate(_AR_FA)
    s = _DIACRITICS.sub("", s)
    s = _SEPARATORS.sub("", s)
    return s.lower()
```


================================================================================
FILE: core\progression.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""منطق پیشرفت بازیکن: XP، Level، استریک. خالص و قابل تست (بدون دیتابیس)."""

def xp_needed(level):
    return 100 + (level - 1) * 50

def add_xp(level, xp, gained):
    gained = max(0, int(gained or 0))      # هیچ‌وقت منفی نشود
    xp = max(0, int(xp or 0))
    level = max(1, int(level or 1))
    xp += gained
    gained_levels = 0
    while xp >= xp_needed(level):
        xp -= xp_needed(level)
        level += 1
        gained_levels += 1
    return level, xp, gained_levels

def levelup_reward(level):
    return 25 + level * 5

def streak_reward(streak_days):
    base = 20
    bonus = min(streak_days, 7) * 10
    return base + bonus

def update_streak(last_day, today, current_streak):
    import datetime
    if not last_day:
        return 1, False
    if last_day == today:
        return current_streak, False
    d_last = datetime.date.fromisoformat(last_day)
    d_today = datetime.date.fromisoformat(today)
    diff = (d_today - d_last).days
    if diff == 1:
        return current_streak + 1, False
    return 1, True

```


================================================================================
FILE: features\__init__.py
================================================================================

```py

```


================================================================================
FILE: features\admin_service.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""سرویس ادمین: تشخیص دسترسی + عملیات مدیریتی."""
import config
from core import db, progression as pr

def is_admin(uid):
    return uid in config.ADMIN_IDS or db.is_db_admin(uid)

def is_owner(uid):
    """فقط ادمین‌های اصلی config می‌توانند ادمین همکار اضافه/حذف کنند."""
    return uid in config.ADMIN_IDS

def stats_text():
    s = db.stats()
    cats = db.list_categories()
    nwords = sum(c for _, c in cats)
    return (
        "📊 <b>آمار کلی کلمو</b>\n━━━━━━━━━━━━━━\n"
        f"👤 بازیکن‌ها: <b>{s['players']}</b>\n"
        f"🔥 فعال (استریک‌دار): <b>{s['active']}</b>\n"
        f"🎮 کل بازی‌ها: <b>{s['games']}</b>\n"
        f"🏆 کل بردها: <b>{s['wins']}</b>\n"
        f"🪙 سکه در گردش: <b>{s['coins']:,}</b>\n"
        f"🗂 دسته‌ها: <b>{len(cats)}</b> | کلمات: <b>{nwords}</b>"
    )

def give(target_uid, coins=0, xp=0):
    p = db.get_player(target_uid)
    if not p:
        return None
    fields = {}
    if coins:
        fields["coins"] = p["coins"] + coins
    if xp:
        lvl, newxp, _ = pr.add_xp(p["level"], p["xp"], xp)
        fields["level"] = lvl
        fields["xp"] = newxp
    if fields:
        db.save_player(target_uid, **fields)
    return db.get_player(target_uid)

```


================================================================================
FILE: features\garden.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""🌱 باغچه‌ی کلمو (Kalemo Garden) — فاز آینده (Preview).

این ماژول فقط طرح اولیه‌ی یک سیستم «پیشرفت غیرفعال» (Idle Progression) است
برای افزایش بازگشت روزانه‌ی کاربران. فعلاً پیاده‌سازی نمی‌شود و در جریان
بازی نقشی ندارد — صرفاً به‌عنوان نقطه‌ی توسعه‌ی آینده اینجا لحاظ شده است.

ایده‌ی کلی:
- هر بازیکن یک باغچه دارد که با سکه/XP بازی رشد می‌کند.
- گیاهان به‌مرور زمان (حتی وقتی کاربر آفلاین است) رشد می‌کنند.
- برداشت روزانه → سکه‌ی اضافی → انگیزه‌ی بازگشت هر روز.

طراحی ماژولار: وقتی فعال شد، فقط کافی است یک هندلر و چند جدول اضافه شود؛
هسته‌ی بازی نیازی به تغییر ندارد.
"""

ENABLED = False  # وقتی True شود، فاز باغچه فعال می‌شود.


def preview_card():
    return (
        "🌱 <b>باغچه‌ی کلمو — به‌زودی</b>\n"
        "━━━━━━━━━━━━━━\n"
        "یه باغچه برای خودت بساز که حتی وقتی نیستی رشد می‌کنه!\n"
        "هر روز برگرد و محصولتو برداشت کن 🪙"
    )

```


================================================================================
FILE: features\garden_service.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""رویدادهای سبک باغچه کلمو.

این فایل عمداً کوچک نگه داشته شده تا هر بخش ربات بتواند بدون وابستگی UI
به رشد درخت، بذر و پاداش‌های باغچه وصل شود.
"""

import random
from core import db


def on_correct_answer(uid):
    """پاسخ صحیح در مودهای سوالی: رشد کوچک اما فوری."""
    db.garden_add_growth(uid, 3, source="correct_answer", detail="پاسخ صحیح")


def on_match_played(uid):
    """پایان مسابقه برای همه شرکت‌کننده‌ها."""
    db.garden_add_growth(uid, 4, source="match_played", detail="حضور در مسابقه")
    if random.random() < 0.12:
        db.garden_add_seed(uid, source="match_seed")


def on_match_win(uid):
    """برد مسابقه: رشد بیشتر و شانس بذر."""
    db.garden_add_growth(uid, 12, source="match_win", detail="برد مسابقه")
    if random.random() < 0.35:
        db.garden_add_seed(uid, source="win_seed")


def on_lucky_box(uid, item=None):
    """باز شدن/گرفتن Lucky Box باعث رشد و گاهی بذر می‌شود."""
    db.garden_add_growth(uid, 7, source="lucky_box", detail="Lucky Box")
    if item and item.get("type") == "seed":
        seed_type = db.garden_random_seed_type()
        db.garden_add_seed(uid, seed_type, 1, source="lucky_box_seed")
        return seed_type
    if random.random() < 0.18:
        return db.garden_add_seed(uid, source="lucky_box_bonus_seed")
    return None


def on_daily_garden_visit(uid):
    return db.garden_daily_visit(uid)

```


================================================================================
FILE: features\lucky_box.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""🎁 جعبه شانس بعد از پایان مسابقه.

Phase 1: احتمال ظاهر شدن به‌طور محسوس کاهش یافت (۰٫۲۵ → ۰٫۱۰) و نام در همه‌جا
از «Lucky Box» به «🎁 جعبه شانس» تغییر کرد.
"""

import logging
import random
from core import db, progression as pr

logger = logging.getLogger("kalemo.luckybox")

# نام نمایشی واحد برای استفاده در UI/پیام‌ها/لاگ‌ها.
BOX_NAME = "🎁 جعبه شانس"

# احتمال ظاهر شدن جعبه شانس بعد از هر مسابقه (به‌ازای هر بازیکن).
DROP_CHANCE = 0.10

ITEMS = [
    {"type": "coin", "value": 30, "rarity": "common", "weight": 40},
    {"type": "coin", "value": 80, "rarity": "common", "weight": 25},
    {"type": "xp", "value": 40, "rarity": "common", "weight": 25},
    {"type": "xp", "value": 100, "rarity": "rare", "weight": 8},
    {"type": "title", "value": "کلمه‌باز", "rarity": "rare", "weight": 4},
    {"type": "badge", "value": "برق ذهن", "rarity": "rare", "weight": 3},
    {"type": "profile_frame", "value": "طلایی", "rarity": "epic", "weight": 2},
    {"type": "seed", "value": 1, "rarity": "future", "weight": 5},
]


def _pick_item():
    weights = [i["weight"] for i in ITEMS]
    return random.choices(ITEMS, weights=weights, k=1)[0]


def try_grant(uid, match_id=None):
    """با احتمال DROP_CHANCE یک جعبه شانس به بازیکن می‌دهد.

    خطاها لاگ می‌شوند و None برمی‌گردد تا هرگز جریان پایان بازی را نشکنند.
    """
    try:
        if random.random() > DROP_CHANCE:
            return None

        item = _pick_item()
        p = db.get_player(uid)
        if not p:
            return None

        if item["type"] == "coin":
            db.save_player(uid, coins=p["coins"] + int(item["value"]))
        elif item["type"] == "xp":
            lvl, xp, _ = pr.add_xp(p["level"], p["xp"], int(item["value"]))
            db.save_player(uid, level=lvl, xp=xp)

        # title/badge/profile_frame/seed فعلاً فقط در جدول ذخیره می‌شوند.
        db.add_lucky_box(
            user_id=uid,
            match_id=match_id,
            item_type=item["type"],
            item_value=item["value"],
            rarity=item["rarity"],
        )
        logger.info("جعبه شانس به کاربر %s داده شد: %s", uid, item)
        return item
    except Exception:
        logger.exception("خطا در اعطای جعبه شانس به کاربر %s", uid)
        return None


def item_text(item):
    if item["type"] == "coin":
        return f"🪙 {item['value']} سکه"
    if item["type"] == "xp":
        return f"⚡️ {item['value']} XP"
    if item["type"] == "title":
        return f"🏷 عنوان: {item['value']}"
    if item["type"] == "badge":
        return f"🏅 نشان: {item['value']}"
    if item["type"] == "profile_frame":
        return f"🖼 قاب پروفایل: {item['value']}"
    if item["type"] == "seed":
        return f"🌱 بذر: {item['value']}"
    return str(item["value"])

```


================================================================================
FILE: features\player_service.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""سرویس بازیکن: منطق پیشرفت/استریک/ماموریت + مدیریت نام نمایشی."""
import datetime
import time
import config
from core import db, progression as pr, missions as ms
from ui import cards


def today():
    return datetime.date.today().isoformat()


def register(uid, name):
    existed = db.get_player(uid) is not None
    p = db.ensure_player(uid, name)
    return p, (not existed)


def display_name(uid, fallback=""):
    return db.get_display_name(uid) or fallback


# ---- نام نمایشی ----
def validate_name(name):
    """قوانین: ۳ تا ۲۰ کاراکتر. برمی‌گرداند (ok, msg)."""
    name = (name or "").strip()
    if len(name) < 3:
        return False, "نام باید حداقل ۳ کاراکتر باشه."
    if len(name) > 20:
        return False, "نام باید حداکثر ۲۰ کاراکتر باشه."
    return True, None


def name_cooldown_left(uid):
    """ثانیه‌های باقی‌مانده تا اجازه‌ی تغییر نام (۰ یعنی آزاد)."""
    p = db.get_player(uid)
    if not p:
        return 0
    last = p.get("name_changed_at", 0)
    if last == 0:
        return 0
    elapsed = int(time.time()) - last
    left = config.NAME_CHANGE_COOLDOWN - elapsed
    return max(0, left)


def set_name(uid, fallback, name):
    """تلاش برای ثبت نام نمایشی. برمی‌گرداند (ok, msg)."""
    db.ensure_player(uid, fallback)
    ok, msg = validate_name(name)
    if not ok:
        return False, msg
    name = name.strip()
    # اگر همین نام فعلی است
    current = db.get_display_name(uid)
    if current and current != name:
        left = name_cooldown_left(uid)
        if left > 0:
            days = left // 86400
            hours = (left % 86400) // 3600
            when = f"{days} روز و {hours} ساعت" if days else f"{hours} ساعت"
            return False, f"تا تغییر بعدی باید {when} صبر کنی."
    if db.is_display_name_taken(name, exclude_uid=uid):
        return False, "این نام قبلاً گرفته شده، یکی دیگه انتخاب کن."
    db.set_display_name(uid, name)
    return True, None


def daily_login(uid, name):
    p = db.ensure_player(uid, name)
    if p["last_login"] == today():
