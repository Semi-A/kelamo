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

=== یادداشت رفع‌باگ (categories/words) ===
مشکل قبلی: هنگام seed کردن دسته‌های کاملاً جدید هم‌زمان با اجرای ربات
روی Render، اضافه‌کردن کلمه برای یک دسته‌ی تازه گاهی شکست می‌خورد در
حالی‌که کلمات دسته‌های قبلاً موجود بدون مشکل اضافه می‌شدند.

علت: add_word برای هر عملیات (خواندن دسته، ساخت دسته، خواندن دوباره‌ی
دسته، بررسی تکراری بودن کلمه، درج نهایی) از conn() جداگانه استفاده
می‌کرد — یعنی هر کدام یک transaction/connection مستقل از pool می‌گرفتند.
وقتی seeds.py و ربات هم‌زمان روی یک دیتابیس فعال بودند، این چند
round-trip جدا مستعد race condition بود: اگر بین «ساخت دسته» و «خواندن
دوباره‌ی id دسته» یک خطای گذرا (تصادم، قفل موقت و...) رخ می‌داد،
add_category خطا را می‌بلعید (فقط print می‌کرد) و مقدار True/False
برمی‌گرداند بدون این‌که add_word این خطا را واقعاً بررسی کند؛ نتیجه
این می‌شد که category همچنان None می‌ماند و کل عملیات با پیام
"CATEGORY CREATION FAILED" بی‌سروصدا شکست می‌خورد.

راه‌حل: add_category حالا با INSERT ... ON CONFLICT (name) DO UPDATE
... RETURNING id اجرا می‌شود — یک عملیات اتمیک که چه دسته تازه ساخته
شود چه از قبل موجود باشد، همیشه id را در همان query برمی‌گرداند. دیگر
نیازی به SELECT جداگانه‌ی بعد از INSERT نیست. add_word هم به همین
ترتیب round-tripها را به حداقل رسانده است.
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
#
# نکته‌ی مهم: add_category قبلاً True/False برمی‌گرداند. حالا dict
# {"id":..., "name":...} یا None برمی‌گرداند تا در همان query که دسته
# را می‌سازد، id را هم اتمیک بگیریم و نیاز به SELECT جداگانه (که منشا
# race condition بود) از بین برود. هر جای دیگر پروژه که از خروجی
# add_category به‌صورت boolean استفاده می‌کرد اینجا اصلاح شده (seed_defaults
# در همین فایل). اگر فایل دیگری در پروژه مستقیماً add_category را با
# انتظار True/False صدا می‌زند، باید همان‌جا هم به `is not None` تغییر کند.

def add_category(name):
    """دسته را اضافه می‌کند (اگر نبود) و در هر صورت اطلاعات آن را برمی‌گرداند.

    خروجی: dict {"id": ..., "name": ...} در صورت موفقیت، یا None در صورت خطا.

    این تابع اتمیک است: با یک INSERT ... ON CONFLICT ... DO UPDATE ...
    RETURNING، چه دسته تازه ساخته شود چه از قبل موجود باشد، id در همان
    query برمی‌گردد. این باعث می‌شود دیگر نیازی به یک SELECT جداگانه‌ی
    بعد از INSERT نباشد که در حضور ترافیک هم‌زمان (مثلاً ربات + seeds.py
    که هم‌زمان روی Render اجرا می‌شوند) مستعد race condition بود.
    """
    name = (name or "").strip()
    if not name:
        return None

    try:
        with conn() as c:
            cur = c.execute(
                """
                INSERT INTO categories(name)
                VALUES (%s)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id, name
                """,
                (name,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        print("add_category error:", repr(e), "| name:", name)
        return None


def del_category(name):
    with conn() as c:
        cur = c.execute("DELETE FROM categories WHERE name=%s", ((name or "").strip(),))
        return cur.rowcount > 0


def get_category(name):
    with conn() as c:
        cur = c.cursor()
        cur.execute(
            "SELECT id, name FROM categories WHERE name=%s",
            (name,)
        )
        return cur.fetchone()


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
    """کلمه را به دسته اضافه می‌کند؛ دسته را در صورت نبود می‌سازد.

    نسخه‌ی اصلاح‌شده: به‌جای چند round-trip جدا (get_category → add_category
    → get_category دوباره → find_word که خودش دوباره get_category صدا
    می‌زند → insert نهایی)، مسیر به دو مرحله کاهش یافته:
      1) add_category که اتمیک است و همیشه id معتبر برمی‌گرداند (یا None
         اگر واقعاً خطایی رخ دهد که این‌بار بی‌سروصدا بلعیده نمی‌شود).
      2) یک conn() واحد که هم بررسی تکراری‌بودن کلمه و هم درج نهایی را
         در یک تراکنش انجام می‌دهد.
    """
    category = (category or "").strip()
    word = (word or "").strip()

    if not category or not word:
        return False

    cat = add_category(category)  # idempotent: می‌سازد یا موجود را برمی‌گرداند
    if not cat:
        print("CATEGORY CREATION FAILED:", category)
        return False

    if isinstance(synonyms, (list, tuple, set)):
        synonyms = "،".join(str(x).strip() for x in synonyms if str(x).strip())

    nw = normalize_word(word)

    try:
        with conn() as c:
            dup = c.execute(
                """
                SELECT 1 FROM words
                WHERE category_id=%s AND normalized_word=%s
                LIMIT 1
                """,
                (cat["id"], nw),
            ).fetchone()
            if dup:
                return False  # از قبل موجود است

            c.execute(
                """
                INSERT INTO words(
                    category_id, word, normalized_word,
                    difficulty, rarity, points, synonyms, clue
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    cat["id"],
                    word,
                    nw,
                    int(difficulty or 1),
                    int(rarity or 1),
                    int(points or 10),
                    synonyms or "",
                    clue or "",
                ),
            )
        return True
    except IntegrityError:
        return False
    except Exception as e:
        print("add_word error:", repr(e), "| category:", category, "| word:", word)
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
                    cat_row = c.execute(
                        """
                        INSERT INTO categories(name) VALUES (%s)
                        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                        RETURNING id
                        """,
                        (category,),
                    ).fetchone()
                    cat_id = cat_row["id"]
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
            cat_row = c.execute(
                """
                INSERT INTO categories(name) VALUES (%s)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                (category,),
            ).fetchone()
            cat_id = cat_row["id"]
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
FILE: DBSEEDS.py
================================================================================

```py
from supabase import create_client
import json

# ======================
# 1. تنظیمات Supabase
# ======================
SUPABASE_URL = "https://pzalhcdhctrzesxqsyvz.supabase.co"
SUPABASE_KEY = "sb_publishable_FizQJiVlc7peHhEpo-Q1qQ_yynxXkAD"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ======================
# 2. داده‌ها (JSON تو)
# ======================
data = [
    {
        "category": "فوتبال",
        "word": "کریستیانو رونالدو",
        "difficulty": 1,
        "rarity": 1,
        "points": 10,
        "synonyms": "",
        "clue": "ستاره پرتغالی فوتبال"
    },
    {
        "category": "فوتبال",
        "word": "نیمار",
        "difficulty": 1,
        "rarity": 1,
        "points": 10,
        "synonyms": "",
        "clue": "بازیکن مشهور برزیلی"
    }
    # 👇 همینجا بقیه دیتاها رو هم اضافه کن
]

# ======================
# 3. ارسال به Supabase
# ======================
def upload():
    try:
        response = supabase.table("words").insert(data).execute()

        if response.data:
            print("✅ Upload successful!")
        else:
            print("⚠️ No data inserted")

    except Exception as e:
        print("❌ Error:", e)


if __name__ == "__main__":
    upload()
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
DROP_CHANCE = 0.05

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
from .classic import ClassicRandomMode, ClassicChoiceMode
from .chain import ChainMode
from .variable import VariableMode
from .namefamily import NameFamilyMode
from .clue import ClueMode

from .classic import ClassicRandomMode, ClassicChoiceMode
from .chain import ChainMode
from .variable import VariableMode
from .namefamily import NameFamilyMode
from .clue import ClueMode

MODE_ORDER = ["classic_random", "classic_choice", "chain", "namefamily", "variable", "clue"]

REGISTRY = {
    ClassicRandomMode.id: ClassicRandomMode,
    ClassicChoiceMode.id: ClassicChoiceMode,
    ChainMode.id: ChainMode,
    VariableMode.id: VariableMode,
    NameFamilyMode.id: NameFamilyMode,
    ClueMode.id: ClueMode,
}

# فقط این دسته‌ها وارد مود زنجیره می‌شن
CHAIN_CATEGORIES = ["میوه‌ها", "حیوانات", "کشورها"]


def _merge_categories(all_categories: dict, names: list) -> list:
    merged, seen = [], set()
    for name in names:
        for w in all_categories.get(name, []):
            n = (w or "").strip()
            if n and n not in seen:
                seen.add(n)
                merged.append(w)
    return merged

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
    "chain": {
        "name": "زنجیره",
        "emoji": "⛓",
        "desc": "هر کلمه با حرف آخرِ کلمه‌ی قبلی شروع می‌شود.",
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
FILE: game\modes\chain.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""مود زنجیره (⛓): هر کلمه باید با حرف آخرِ کلمه‌ی قبلی شروع شود
و در دسته‌ی همان بازی معتبر باشد. اولین حرف را ربات می‌دهد."""
import random
from .base import BaseMode


def _last_letter(word):
    w = (word or "").strip()
    return w[-1] if w else ""


class ChainMode(BaseMode):
    id = "chain"; name = "زنجیره"; emoji = "⛓"

    def __init__(self, words, category="", ruleset=None):
        super().__init__(words, ruleset)
        self.category = category
        self.current_letter = None   # حرفی که کلمه‌ی بعدی باید با آن شروع شود

    def tutorial(self):
        return ("⛓ <b>مود زنجیره</b>\n"
                f"دسته: <b>{self.category}</b>\n"
                "هر کلمه باید با <b>حرف آخرِ</b> کلمه‌ی قبلی شروع بشه.\n"
                "مثال: سیب ← بادام ← ماه …\nآماده باشید...")

    def _valid_words(self):
        return [w for w in self.words if (w or "").strip()]

    def new_question(self):
        pool = self._valid_words()
        if not pool:
            return {"prompt": "کلمه‌ای برای این دسته ثبت نشده 😅",
                    "answers": set(), "letter": None}
        # حرف شروع را از یک کلمه‌ی تصادفی بگیر (اولین حلقه‌ی زنجیره)
        if self.current_letter is None:
            self.current_letter = self.norm(random.choice(pool))[0]

        letter = self.current_letter
        answers = {
            self.norm(w) for w in pool
            if self.norm(w).startswith(letter)
        }
        return {
            "prompt": (f"⛓ <b>زنجیره — دسته {self.category}</b>\n\n"
                       f"کلمه‌ای بگو که با <b>«{letter}»</b> شروع بشه."),
            "answers": answers,
            "letter": letter,
        }

    def check_answer(self, question, text):
        w = self.norm(text)
        if w not in question.get("answers", set()):
            return False, "نامعتبر"
        # زنجیره را جلو ببر: حرفِ بعدی = حرف آخرِ همین کلمه
        self.current_letter = _last_letter(w)
        return True, None
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
        self._pool = None      # کش تنبل کلمات دارای سرنخ معتبر
        self._used = set()      # کلماتی که همین بازی به‌عنوان سوال آمده‌اند

    def _load_pool(self):
        from core import db
        # فقط کلماتی با clue معتبر و غیرخالی، مستقیم از دیتابیس
        rows = [r for r in db.clue_pool() if (r.get("clue") or "").strip()]
        random.shuffle(rows)   # هر بازی کاملاً تصادفی
        self._pool = rows

    def tutorial(self):
        return ("🕵️ <b>مود سرنخ</b>\n"
                "من یه سرنخ می‌دم، تو جواب درست رو حدس بزن!\n"
                "مثال: <code>سلطان جنگل</code> ← <b>شیر</b>\nآماده باشید...")

    def new_question(self):
        if self._pool is None:
            self._load_pool()

        # کلمه‌ای انتخاب کن که هنوز به‌عنوان سوال استفاده نشده
        candidates = [r for r in self._pool
                      if r["word"] not in self._used]
        if not candidates:
            # همه سرنخ‌ها مصرف شده‌اند → دوره جدید
            self._used.clear()
            candidates = list(self._pool)
        if not candidates:
            return {"prompt": "سرنخی برای این بازی ثبت نشده 😅", "answers": set()}

        row = random.choice(candidates)
        self._used.add(row["word"])
        return {
            "prompt": (f"🕵️ <b>سرنخ:</b>\n\n<b>{row['clue']}</b>\n\n"
                       f"<i>جواب رو حدس بزن!</i>"),
            "answers": {self.norm(row["word"])},
        }

    def check_answer(self, question, text):
        if self.norm(text) in question.get("answers", set()):
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

    def __init__(
        self,
        words=None,
        ruleset=None,
        categories=None,
        num_categories=None,
        **kw
    ):        
        self.words = list(words or [])
        self.ruleset = ruleset
        self.letter = random.choice(PERSIAN_LETTERS)

        # فقط دسته‌های استاندارد اسم‌وفامیل
        if categories:
            self.cats = categories
        else:
            cats = load_db_categories()
            self.cats = random.sample(
                cats,
                min(6, len(cats))
            )
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


class NoDisturb(Rule):
    id = "no_disturb"; label = "بدون مزاحمت"
    def describe(self): return "حذف پیام‌های نامرتبط بازیکنان"

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
    MustContain, MustNotContain, TimeLimit, BonusScore, NoDisturb,
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

CHAIN_CATEGORIES = ["میوه", "رنگ", "کشور","شهر","حیوان","غذا","اشیا"]  # دسته‌های مجاز خودت رو بذار


def _merge_categories(all_categories: dict, names: list) -> list:
    merged, seen = [], set()
    for name in names:
        for w in all_categories.get(name, []):
            n = (w or "").strip()
            if n and n not in seen:
                seen.add(n)
                merged.append(w)
    return merged


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
        self.namefamily_categories = []
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
        # ---- کنترل نرخ ویرایش پیام زنده (rate limit) ----
        self.live_lock = None            # asyncio.Lock (تنبل ساخته می‌شود)
        self.live_last_edit = 0.0        # زمان آخرین edit موفق
        self.live_dirty = False          # آپدیت معلق هست؟
        self.live_flusher = None         # task فلش تأخیری
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

        if self.mode_id in ("classic_random", "classic_choice", "chain"):
            kwargs["category"] = self.category

        if self.mode_id == "chain":
            # به‌جای دسته‌ی انتخابی کاربر، فقط دسته‌های مجاز رو قاطی کن
            kwargs["words"] = _merge_categories(self.all_categories, CHAIN_CATEGORIES)
            kwargs["category"] = " / ".join(CHAIN_CATEGORIES)


        elif self.mode_id == "namefamily":
            kwargs["categories"] = self.namefamily_categories

        self.mode = cls(self.words, **kwargs)
        return self.mode    # ---- gameplay ----
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

=== یادداشت رفع‌باگ (categories/words) ===
مشکل قبلی: هنگام seed کردن دسته‌های کاملاً جدید هم‌زمان با اجرای ربات
روی Render، اضافه‌کردن کلمه برای یک دسته‌ی تازه گاهی شکست می‌خورد در
حالی‌که کلمات دسته‌های قبلاً موجود بدون مشکل اضافه می‌شدند.

علت: add_word برای هر عملیات (خواندن دسته، ساخت دسته، خواندن دوباره‌ی
دسته، بررسی تکراری بودن کلمه، درج نهایی) از conn() جداگانه استفاده
می‌کرد — یعنی هر کدام یک transaction/connection مستقل از pool می‌گرفتند.
وقتی seeds.py و ربات هم‌زمان روی یک دیتابیس فعال بودند، این چند
round-trip جدا مستعد race condition بود: اگر بین «ساخت دسته» و «خواندن
دوباره‌ی id دسته» یک خطای گذرا (تصادم، قفل موقت و...) رخ می‌داد،
add_category خطا را می‌بلعید (فقط print می‌کرد) و مقدار True/False
برمی‌گرداند بدون این‌که add_word این خطا را واقعاً بررسی کند؛ نتیجه
این می‌شد که category همچنان None می‌ماند و کل عملیات با پیام
"CATEGORY CREATION FAILED" بی‌سروصدا شکست می‌خورد.

راه‌حل: add_category حالا با INSERT ... ON CONFLICT (name) DO UPDATE
... RETURNING id اجرا می‌شود — یک عملیات اتمیک که چه دسته تازه ساخته
شود چه از قبل موجود باشد، همیشه id را در همان query برمی‌گرداند. دیگر
نیازی به SELECT جداگانه‌ی بعد از INSERT نیست. add_word هم به همین
ترتیب round-tripها را به حداقل رسانده است.
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
#
# نکته‌ی مهم: add_category قبلاً True/False برمی‌گرداند. حالا dict
# {"id":..., "name":...} یا None برمی‌گرداند تا در همان query که دسته
# را می‌سازد، id را هم اتمیک بگیریم و نیاز به SELECT جداگانه (که منشا
# race condition بود) از بین برود. هر جای دیگر پروژه که از خروجی
# add_category به‌صورت boolean استفاده می‌کرد اینجا اصلاح شده (seed_defaults
# در همین فایل). اگر فایل دیگری در پروژه مستقیماً add_category را با
# انتظار True/False صدا می‌زند، باید همان‌جا هم به `is not None` تغییر کند.

def add_category(name):
    """دسته را اضافه می‌کند (اگر نبود) و در هر صورت اطلاعات آن را برمی‌گرداند.

    خروجی: dict {"id": ..., "name": ...} در صورت موفقیت، یا None در صورت خطا.

    این تابع اتمیک است: با یک INSERT ... ON CONFLICT ... DO UPDATE ...
    RETURNING، چه دسته تازه ساخته شود چه از قبل موجود باشد، id در همان
    query برمی‌گردد. این باعث می‌شود دیگر نیازی به یک SELECT جداگانه‌ی
    بعد از INSERT نباشد که در حضور ترافیک هم‌زمان (مثلاً ربات + seeds.py
    که هم‌زمان روی Render اجرا می‌شوند) مستعد race condition بود.
    """
    name = (name or "").strip()
    if not name:
        return None

    try:
        with conn() as c:
            cur = c.execute(
                """
                INSERT INTO categories(name)
                VALUES (%s)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id, name
                """,
                (name,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        print("add_category error:", repr(e), "| name:", name)
        return None


def del_category(name):
    with conn() as c:
        cur = c.execute("DELETE FROM categories WHERE name=%s", ((name or "").strip(),))
        return cur.rowcount > 0


def get_category(name):
    with conn() as c:
        cur = c.cursor()
        cur.execute(
            "SELECT id, name FROM categories WHERE name=%s",
            (name,)
        )
        return cur.fetchone()


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
    """کلمه را به دسته اضافه می‌کند؛ دسته را در صورت نبود می‌سازد.

    نسخه‌ی اصلاح‌شده: به‌جای چند round-trip جدا (get_category → add_category
    → get_category دوباره → find_word که خودش دوباره get_category صدا
    می‌زند → insert نهایی)، مسیر به دو مرحله کاهش یافته:
      1) add_category که اتمیک است و همیشه id معتبر برمی‌گرداند (یا None
         اگر واقعاً خطایی رخ دهد که این‌بار بی‌سروصدا بلعیده نمی‌شود).
      2) یک conn() واحد که هم بررسی تکراری‌بودن کلمه و هم درج نهایی را
         در یک تراکنش انجام می‌دهد.
    """
    category = (category or "").strip()
    word = (word or "").strip()

    if not category or not word:
        return False

    cat = add_category(category)  # idempotent: می‌سازد یا موجود را برمی‌گرداند
    if not cat:
        print("CATEGORY CREATION FAILED:", category)
        return False

    if isinstance(synonyms, (list, tuple, set)):
        synonyms = "،".join(str(x).strip() for x in synonyms if str(x).strip())

    nw = normalize_word(word)

    try:
        with conn() as c:
            dup = c.execute(
                """
                SELECT 1 FROM words
                WHERE category_id=%s AND normalized_word=%s
                LIMIT 1
                """,
                (cat["id"], nw),
            ).fetchone()
            if dup:
                return False  # از قبل موجود است

            c.execute(
                """
                INSERT INTO words(
                    category_id, word, normalized_word,
                    difficulty, rarity, points, synonyms, clue
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    cat["id"],
                    word,
                    nw,
                    int(difficulty or 1),
                    int(rarity or 1),
                    int(points or 10),
                    synonyms or "",
                    clue or "",
                ),
            )
        return True
    except IntegrityError:
        return False
    except Exception as e:
        print("add_word error:", repr(e), "| category:", category, "| word:", word)
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
                    cat_row = c.execute(
                        """
                        INSERT INTO categories(name) VALUES (%s)
                        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                        RETURNING id
                        """,
                        (category,),
                    ).fetchone()
                    cat_id = cat_row["id"]
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
            cat_row = c.execute(
                """
                INSERT INTO categories(name) VALUES (%s)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                (category,),
            ).fetchone()
            cat_id = cat_row["id"]
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
FILE: DBSEEDS.py
================================================================================

```py
from supabase import create_client
import json

# ======================
# 1. تنظیمات Supabase
# ======================
SUPABASE_URL = "https://pzalhcdhctrzesxqsyvz.supabase.co"
SUPABASE_KEY = "sb_publishable_FizQJiVlc7peHhEpo-Q1qQ_yynxXkAD"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ======================
# 2. داده‌ها (JSON تو)
# ======================
data = [
    {
        "category": "فوتبال",
        "word": "کریستیانو رونالدو",
        "difficulty": 1,
        "rarity": 1,
        "points": 10,
        "synonyms": "",
        "clue": "ستاره پرتغالی فوتبال"
    },
    {
        "category": "فوتبال",
        "word": "نیمار",
        "difficulty": 1,
        "rarity": 1,
        "points": 10,
        "synonyms": "",
        "clue": "بازیکن مشهور برزیلی"
    }
    # 👇 همینجا بقیه دیتاها رو هم اضافه کن
]

# ======================
# 3. ارسال به Supabase
# ======================
def upload():
    try:
        response = supabase.table("words").insert(data).execute()

        if response.data:
            print("✅ Upload successful!")
        else:
            print("⚠️ No data inserted")

    except Exception as e:
        print("❌ Error:", e)


if __name__ == "__main__":
    upload()
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
DROP_CHANCE = 0.05

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
from .classic import ClassicRandomMode, ClassicChoiceMode
from .chain import ChainMode
from .variable import VariableMode
from .namefamily import NameFamilyMode
from .clue import ClueMode

from .classic import ClassicRandomMode, ClassicChoiceMode
from .chain import ChainMode
from .variable import VariableMode
from .namefamily import NameFamilyMode
from .clue import ClueMode

MODE_ORDER = ["classic_random", "classic_choice", "chain", "namefamily", "variable", "clue"]

REGISTRY = {
    ClassicRandomMode.id: ClassicRandomMode,
    ClassicChoiceMode.id: ClassicChoiceMode,
    ChainMode.id: ChainMode,
    VariableMode.id: VariableMode,
    NameFamilyMode.id: NameFamilyMode,
    ClueMode.id: ClueMode,
}

# فقط این دسته‌ها وارد مود زنجیره می‌شن
CHAIN_CATEGORIES = ["میوه‌ها", "حیوانات", "کشورها"]


def _merge_categories(all_categories: dict, names: list) -> list:
    merged, seen = [], set()
    for name in names:
        for w in all_categories.get(name, []):
            n = (w or "").strip()
            if n and n not in seen:
                seen.add(n)
                merged.append(w)
    return merged

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
    "chain": {
        "name": "زنجیره",
        "emoji": "⛓",
        "desc": "هر کلمه با حرف آخرِ کلمه‌ی قبلی شروع می‌شود.",
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
FILE: game\modes\chain.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""مود زنجیره (⛓): هر کلمه باید با حرف آخرِ کلمه‌ی قبلی شروع شود
و در دسته‌ی همان بازی معتبر باشد. اولین حرف را ربات می‌دهد."""
import random
from .base import BaseMode


def _last_letter(word):
    w = (word or "").strip()
    return w[-1] if w else ""


class ChainMode(BaseMode):
    id = "chain"; name = "زنجیره"; emoji = "⛓"

    def __init__(self, words, category="", ruleset=None):
        super().__init__(words, ruleset)
        self.category = category
        self.current_letter = None   # حرفی که کلمه‌ی بعدی باید با آن شروع شود

    def tutorial(self):
        return ("⛓ <b>مود زنجیره</b>\n"
                f"دسته: <b>{self.category}</b>\n"
                "هر کلمه باید با <b>حرف آخرِ</b> کلمه‌ی قبلی شروع بشه.\n"
                "مثال: سیب ← بادام ← ماه …\nآماده باشید...")

    def _valid_words(self):
        return [w for w in self.words if (w or "").strip()]

    def new_question(self):
        pool = self._valid_words()
        if not pool:
            return {"prompt": "کلمه‌ای برای این دسته ثبت نشده 😅",
                    "answers": set(), "letter": None}
        # حرف شروع را از یک کلمه‌ی تصادفی بگیر (اولین حلقه‌ی زنجیره)
        if self.current_letter is None:
            self.current_letter = self.norm(random.choice(pool))[0]

        letter = self.current_letter
        answers = {
            self.norm(w) for w in pool
            if self.norm(w).startswith(letter)
        }
        return {
            "prompt": (f"⛓ <b>زنجیره — دسته {self.category}</b>\n\n"
                       f"کلمه‌ای بگو که با <b>«{letter}»</b> شروع بشه."),
            "answers": answers,
            "letter": letter,
        }

    def check_answer(self, question, text):
        w = self.norm(text)
        if w not in question.get("answers", set()):
            return False, "نامعتبر"
        # زنجیره را جلو ببر: حرفِ بعدی = حرف آخرِ همین کلمه
        self.current_letter = _last_letter(w)
        return True, None
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
        self._pool = None      # کش تنبل کلمات دارای سرنخ معتبر
        self._used = set()      # کلماتی که همین بازی به‌عنوان سوال آمده‌اند

    def _load_pool(self):
        from core import db
        # فقط کلماتی با clue معتبر و غیرخالی، مستقیم از دیتابیس
        rows = [r for r in db.clue_pool() if (r.get("clue") or "").strip()]
        random.shuffle(rows)   # هر بازی کاملاً تصادفی
        self._pool = rows

    def tutorial(self):
        return ("🕵️ <b>مود سرنخ</b>\n"
                "من یه سرنخ می‌دم، تو جواب درست رو حدس بزن!\n"
                "مثال: <code>سلطان جنگل</code> ← <b>شیر</b>\nآماده باشید...")

    def new_question(self):
        if self._pool is None:
            self._load_pool()

        # کلمه‌ای انتخاب کن که هنوز به‌عنوان سوال استفاده نشده
        candidates = [r for r in self._pool
                      if r["word"] not in self._used]
        if not candidates:
            # همه سرنخ‌ها مصرف شده‌اند → دوره جدید
            self._used.clear()
            candidates = list(self._pool)
        if not candidates:
            return {"prompt": "سرنخی برای این بازی ثبت نشده 😅", "answers": set()}

        row = random.choice(candidates)
        self._used.add(row["word"])
        return {
            "prompt": (f"🕵️ <b>سرنخ:</b>\n\n<b>{row['clue']}</b>\n\n"
                       f"<i>جواب رو حدس بزن!</i>"),
            "answers": {self.norm(row["word"])},
        }

    def check_answer(self, question, text):
        if self.norm(text) in question.get("answers", set()):
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

    def __init__(
        self,
        words=None,
        ruleset=None,
        categories=None,
        num_categories=None,
        **kw
    ):        
        self.words = list(words or [])
        self.ruleset = ruleset
        self.letter = random.choice(PERSIAN_LETTERS)

        # فقط دسته‌های استاندارد اسم‌وفامیل
        if categories:
            self.cats = categories
        else:
            cats = load_db_categories()
            self.cats = random.sample(
                cats,
                min(6, len(cats))
            )
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


class NoDisturb(Rule):
    id = "no_disturb"; label = "بدون مزاحمت"
    def describe(self): return "حذف پیام‌های نامرتبط بازیکنان"

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
    MustContain, MustNotContain, TimeLimit, BonusScore, NoDisturb,
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

CHAIN_CATEGORIES = ["میوه", "رنگ", "کشور","شهر","حیوان","غذا","اشیا"]  # دسته‌های مجاز خودت رو بذار


def _merge_categories(all_categories: dict, names: list) -> list:
    merged, seen = [], set()
    for name in names:
        for w in all_categories.get(name, []):
            n = (w or "").strip()
            if n and n not in seen:
                seen.add(n)
                merged.append(w)
    return merged


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
        self.namefamily_categories = []
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
        # ---- کنترل نرخ ویرایش پیام زنده (rate limit) ----
        self.live_lock = None            # asyncio.Lock (تنبل ساخته می‌شود)
        self.live_last_edit = 0.0        # زمان آخرین edit موفق
        self.live_dirty = False          # آپدیت معلق هست؟
        self.live_flusher = None         # task فلش تأخیری
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

        if self.mode_id in ("classic_random", "classic_choice", "chain"):
            kwargs["category"] = self.category

        if self.mode_id == "chain":
            # به‌جای دسته‌ی انتخابی کاربر، فقط دسته‌های مجاز رو قاطی کن
            kwargs["words"] = _merge_categories(self.all_categories, CHAIN_CATEGORIES)
            kwargs["category"] = " / ".join(CHAIN_CATEGORIES)


        elif self.mode_id == "namefamily":
            kwargs["categories"] = self.namefamily_categories

        self.mode = cls(self.words, **kwargs)
        return self.mode    # ---- gameplay ----
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

```


================================================================================
FILE: project_dump_2.md.txt
================================================================================

```txt
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

```


================================================================================
FILE: README.md
================================================================================

```md
# Kalemo Telegram Game Bot

run: set env vars then `python main.py`

```


================================================================================
FILE: requirements.txt
================================================================================

```txt
python-telegram-bot==21.7
python-dotenv==1.0.1
httpx==0.27.2
flask
psutil
psycopg[binary]==3.2.3
psycopg-pool==3.2.4

```


================================================================================
FILE: runtime.txt
================================================================================

```txt
python-3.12.7
```


================================================================================
FILE: seeds.py
================================================================================

```py
# -*- coding: utf-8 -*-

from core.db import add_word
import os

if os.getenv("RUN_SEEDS") != "true":
    print("Seeds skipped")
    exit()

def import_words():
    added = 0

    words =[
        {"category":"حیوانات","word":"ببر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"گربه‌سان بزرگ راه راه"},
        {"category":"حیوانات","word":"فیل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دارای خرطوم بلند"},
        {"category":"حیوانات","word":"زرافه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"قدبلندترین حیوان خشکی"},
        {"category":"حیوانات","word":"خرگوش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"عاشق هویج و پرش"},
        {"category":"حیوانات","word":"سگ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بهترین دوست انسان"},
        {"category":"حیوانات","word":"گربه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حیوان خانگی ملوس"},
        {"category":"حیوانات","word":"اسب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حیوانی نجیب برای سواری"},
        {"category":"حیوانات","word":"میمون","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"عاشق موز و بالا رفتن از درخت"},
        {"category":"حیوانات","word":"گورخر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شبیه اسب با خطوط سیاه و سفید"},
        {"category":"حیوانات","word":"کانگورو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"دارای کیسه روی شکم"},
        {"category":"حیوانات","word":"پاندا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"خرس سیاه و سفید عاشق بامبو"},
        {"category":"حیوانات","word":"خرس","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"حیوان قوی هیکل که در زمستان می‌خوابد"},
        {"category":"حیوانات","word":"گرگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"زوزه کش شبانه"},
        {"category":"حیوانات","word":"روباه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"معروف به حیله‌گری"},
        {"category":"حیوانات","word":"عقاب","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پرنده شکاری تیزبین"},
        {"category":"حیوانات","word":"جغد","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پرنده بیدار در شب"},
        {"category":"حیوانات","word":"طوطی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پرنده‌ای که تقلید صدا می‌کند"},
        {"category":"حیوانات","word":"مار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"خزنده بدون پا"},
        {"category":"حیوانات","word":"تمساح","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"خزنده خطرناک در آب"},
        {"category":"حیوانات","word":"لاک‌پشت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دارای لاک سخت بر پشت"},
        {"category":"حیوانات","word":"دلفین","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پستاندار بسیار باهوش دریایی"},
        {"category":"حیوانات","word":"کوسه","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"ماهی درنده اقیانوس"},
        {"category":"حیوانات","word":"نهنگ","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"بزرگترین پستاندار زمین"},
        {"category":"حیوانات","word":"هشت‌پا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"دارای ۸ بازو در اعماق دریا"},
        {"category":"حیوانات","word":"شتر","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"کشتی کویر"},
        {"category":"حیوانات","word":"کرگدن","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"دارای شاخ بزرگ روی بینی"},
        {"category":"حیوانات","word":"گوزن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شاخ‌های شاخه‌دار و زیبا"},
        {"category":"حیوانات","word":"سنجاب","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"عاشق جمع کردن فندق و گردو"},
        {"category":"حیوانات","word":"جوجه‌تیغی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بدنی پوشیده از تیغ"},
        {"category":"حیوانات","word":"قورباغه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دوزیست پرش‌کننده"},
        {"category":"حیوانات","word":"مارمولک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"خزنده دیوار‌رو"},
        {"category":"حیوانات","word":"پروانه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"حشره‌ای با بال‌های رنگارنگ"},
        {"category":"حیوانات","word":"زنبور عسل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"تولید کننده شهد و عسل"},
        {"category":"حیوانات","word":"مورچه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حشره‌ای سخت‌کوش و کوچک"},
        {"category":"حیوانات","word":"عنکبوت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بافنده تار"},
        {"category":"حیوانات","word":"کبوتر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نماد صلح"},
        {"category":"حیوانات","word":"گنجشک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پرنده کوچک خانگی"},
        {"category":"حیوانات","word":"طاووس","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"پرنده‌ای با دم بسیار زیبا"},
        {"category":"حیوانات","word":"اردک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پرنده شناگر"},
        {"category":"حیوانات","word":"غاز","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"شبیه اردک اما بزرگتر"},
        {"category":"حیوانات","word":"شترمرغ","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"سریع‌ترین پرنده روی زمین"},
        {"category":"حیوانات","word":"پنگوئن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پرنده قطبی که پرواز نمی‌کند"},
        {"category":"حیوانات","word":"ماهی قرمز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حیوان خانگی داخل تنگ"},
        {"category":"حیوانات","word":"اسب دریایی","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"کوچک و شبیه مهره شطرنج"},
        {"category":"حیوانات","word":"خرچنگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دارای دو چنگک"},
        {"category":"حیوانات","word":"عروس دریایی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"موجود شفاف ژله‌ای"},
        {"category":"حیوانات","word":"کوسه سرچکشی","difficulty":4,"rarity":4,"points":20,"synonyms":"","clue":"گونه خاصی از کوسه"},
        {"category":"حیوانات","word":"فک","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پستاندار دریایی دلقک‌گونه"},
        {"category":"حیوانات","word":"موش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"جونده کوچک"},
        {"category":"حیوانات","word":"پلنگ","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"گربه‌سان خال‌دار"},
        {"category":"حیوانات","word":"یوزپلنگ","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"سریع‌ترین حیوان خشکی"},
        {"category":"حیوانات","word":"کفتار","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"معروف به خنده‌های عجیب"},
        {"category":"حیوانات","word":"بز کوهی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ساکن صخره‌های مرتفع"},
        {"category":"حیوانات","word":"گوسفند","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"منبع اصلی پشم"},
        {"category":"حیوانات","word":"گاو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"منبع شیر و گوشت"},
        {"category":"حیوانات","word":"الاغ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حیوان معروف به باربری"},
        {"category":"حیوانات","word":"خوک","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"حیوانی با بینی تخت"},
        {"category":"حیوانات","word":"همستر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"جونده کوچک خانگی"},
        {"category":"حیوانات","word":"لاک‌پشت دریایی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"خزنده بزرگ شناگر"},
        {"category":"حیوانات","word":"مار کبرا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"خزنده سمی با گردن پهن"},
        {"category":"حیوانات","word":"عقرب","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"بندپای دارای نیش"},
        {"category":"حیوانات","word":"مگس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حشره مزاحم"},
        {"category":"حیوانات","word":"پشه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حشره خون‌خوار"},
        {"category":"حیوانات","word":"کفشدوزک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"حشره قرمز با خال‌های سیاه"},
        {"category":"حیوانات","word":"سنجاقک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"حشره پرنده کنار آب"},
        {"category":"حیوانات","word":"کرم ابریشم","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"تولیدکننده نخ ابریشم"},
        {"category":"حیوانات","word":"ملخ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"حشره جهنده مزارع"},
        {"category":"حیوانات","word":"خفاش","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"تنها پستاندار پرنده"},
        {"category":"حیوانات","word":"موش کور","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"حفار زیرزمینی"},
        {"category":"حیوانات","word":"سمور","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شناگر رودخانه‌ای"},
        {"category":"حیوانات","word":"راکون","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"دارای صورت ماسک‌دار"},
        {"category":"حیوانات","word":"بوفالو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"گاو وحشی بزرگ"},
        {"category":"حیوانات","word":"گراز","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"خوک وحشی"},
        {"category":"حیوانات","word":"زالو","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"کرم خون‌خوار"},
        {"category":"حیوانات","word":"گراز دریایی","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"دارای عاج‌های بلند و هیکل بزرگ"},
        {"category":"حیوانات","word":"سمندر","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"دوزیست شبیه مارمولک"},
        {"category":"حیوانات","word":"مرغ مگس‌خوار","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"کوچکترین پرنده جهان"},
        {"category":"حیوانات","word":"دارکوب","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پرنده تنه درخت کوب"},
        {"category":"حیوانات","word":"شاهین","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شکاری تیز پرواز"},
        {"category":"حیوانات","word":"کلاغ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پرنده سیاه و باهوش"},
        {"category":"حیوانات","word":"پرستو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"نوید دهنده بهار"},
        {"category":"حیوانات","word":"فلامینگو","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"پرنده صورتی پا بلند"},
        {"category":"حیوانات","word":"قو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پرنده نماد زیبایی و وفاداری"},
        {"category":"حیوانات","word":"زنبور سرخ","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"حشره‌ای با نیش دردناک"},
        {"category":"حیوانات","word":"هزارپا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"حشره‌ای با پاهای زیاد"},
        {"category":"حیوانات","word":"حلزون","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حرکت کند با خانه سنگی روی دوش"},
        {"category":"حیوانات","word":"ستاره دریایی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"موجود دریایی شبیه ستاره"},
        {"category":"حیوانات","word":"سفره‌ماهی","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"ماهی پهن و شناور"},
        {"category":"حیوانات","word":"میگو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"سخت‌پوست کوچک دریایی"},
        {"category":"حیوانات","word":"کرم خاکی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کمک به حاصلخیزی خاک"},
        {"category":"حیوانات","word":"آخوندک","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"حشره‌ای با دست‌های نیایشگر"},
        {"category":"حیوانات","word":"سوسک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حشره‌ای مقاوم"},
        {"category":"حیوانات","word":"ایگوآنا","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"خزنده خاص درختی"},
        {"category":"حیوانات","word":"بلبل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"خوش‌خوان گل‌ها"},
        {"category":"حیوانات","word":"قناری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پرنده کوچک آوازه‌خوان"},
        {"category":"حیوانات","word":"تشی","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"جونده‌ای با تیغ‌های بسیار بلند"},
        {"category":"حیوانات","word":"آفتاب‌پرست","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"خزنده تغییر رنگ دهنده"},
        {"category":"حیوانات","word":"بزمجه","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"مارمولک بزرگ بیابانی"},
        {"category":"حیوانات","word":"کوالا","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"حیوان استرالیایی عاشق اکالیپتوس"},  {"category":"اشیا","word":"قاشق","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای غذا خوردن"},
        {"category":"اشیا","word":"صندلی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای نشستن استفاده می‌شود"},
        {"category":"اشیا","word":"مداد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای نوشتن روی کاغذ"},
        {"category":"اشیا","word":"ساعت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"زمان را نشان می‌دهد"},
        {"category":"اشیا","word":"کتاب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مجموعه‌ای از صفحات خواندنی"},
        {"category":"اشیا","word":"لیوان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرف نوشیدنی"},
        {"category":"اشیا","word":"تخت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"جای خواب"},
        {"category":"اشیا","word":"آینه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"تصویر را منعکس می‌کند"},
        {"category":"اشیا","word":"چتر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"محافظ در برابر باران"},
        {"category":"اشیا","word":"کفش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پوشش پا"},
        {"category":"اشیا","word":"گلدان","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"ظرف نگهداری گل"},
        {"category":"اشیا","word":"لامپ","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"منبع روشنایی"},
        {"category":"اشیا","word":"جعبه","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"محفظه نگهداری اشیا"},
        {"category":"اشیا","word":"سوزن","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"ابزار تیز خیاطی"},
        {"category":"اشیا","word":"ساعت مچی","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"ساعت متصل به دست"},
        {"category":"اشیا","word":"کلاه","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"پوشش سر"},
        {"category":"اشیا","word":"چکش","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"ابزار ضربه زدن"},
        {"category":"اشیا","word":"ترازو","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"وسیله اندازه‌گیری وزن"},
        {"category":"اشیا","word":"گیره","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"برای اتصال دو چیز به هم"},
        {"category":"اشیا","word":"آهن‌ربا","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"جذب‌کننده آهن"},
        {"category":"اشیا","word":"پیچ‌گوشتی","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"ابزار بستن پیچ"},
        {"category":"اشیا","word":"آچار","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"ابزار تعمیرات"},
        {"category":"اشیا","word":"میکروسکوپ","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"برای دیدن ذرات ریز"},
        {"category":"اشیا","word":"تلسکوپ","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"برای دیدن اجرام آسمانی"},
        {"category":"اشیا","word":"کارت حافظه","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"ذخیره‌کننده اطلاعات"},
        {"category":"اشیا","word":"دماسنج","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"ابزار سنجش گرما"},
        {"category":"اشیا","word":"قطب‌نما","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"جهت‌یاب"},
        {"category":"اشیا","word":"متر","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"ابزار اندازه‌گیری طول"},
        {"category":"اشیا","word":"ماشین‌حساب","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"ابزار محاسبات"},
        {"category":"اشیا","word":"کنسول بازی","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"دستگاه سرگرمی"},
        {"category":"اشیا","word":"گلدان سفالی","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"گلدان گلی"},
        {"category":"اشیا","word":"کفش کتانی","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"کفش ورزشی"},
        {"category":"اشیا","word":"ساعت دیواری","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"ساعت نصب شده روی دیوار"},
        {"category":"اشیا","word":"پریز برق","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"منبع الکتریسیته"},
        {"category":"اشیا","word":"آباژور","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"چراغ کنار تخت"},
        {"category":"اشیا","word":"کمد","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"جای لباس"},
        {"category":"اشیا","word":"مبل","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"صندلی راحتی"},
        {"category":"اشیا","word":"پرده","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"پوشش پنجره"},
        {"category":"اشیا","word":"گلدان چینی","difficulty":4,"rarity":4,"points":35,"synonyms":"","clue":"ظرف گران‌قیمت"},
        {"category":"اشیا","word":"کنتور","difficulty":4,"rarity":3,"points":30,"synonyms":"","clue":"اندازه‌گیر مصرف"},
        {"category":"اشیا","word":"اسپیکر","difficulty":4,"rarity":3,"points":30,"synonyms":"","clue":"پخش‌کننده صدا"},
        {"category":"اشیا","word":"هارد اکسترنال","difficulty":4,"rarity":3,"points":30,"synonyms":"","clue":"حافظه جانبی"},
        {"category":"اشیا","word":"پروژکتور","difficulty":4,"rarity":4,"points":35,"synonyms":"","clue":"نمایشگر تصویر"},
        {"category":"اشیا","word":"گیتار","difficulty":4,"rarity":3,"points":30,"synonyms":"","clue":"ساز زهی"},
        {"category":"اشیا","word":"ویولن","difficulty":4,"rarity":4,"points":35,"synonyms":"","clue":"ساز کششی"},
        {"category":"اشیا","word":"پیانو","difficulty":4,"rarity":4,"points":35,"synonyms":"","clue":"ساز بزرگ کلاویه‌ای"},
        {"category":"اشیا","word":"گرامافون","difficulty":4,"rarity":4,"points":35,"synonyms":"","clue":"پخش‌کننده صفحه قدیمی"},
        {"category":"اشیا","word":"اسکنر","difficulty":4,"rarity":3,"points":30,"synonyms":"","clue":"دیجیتالی‌کننده کاغذ"},
        {"category":"اشیا","word":"فلاسک","difficulty":4,"rarity":3,"points":30,"synonyms":"","clue":"نگهدارنده دمای مایعات"},
        {"category":"اشیا","word":"چراغ قوه","difficulty":4,"rarity":3,"points":30,"synonyms":"","clue":"نور جیبی"},
        {"category":"اشیا","word":"جعبه سیاه","difficulty":5,"rarity":5,"points":50,"synonyms":"","clue":"ثبت‌کننده وقایع هواپیما"},
        {"category":"اشیا","word":"آنتن","difficulty":5,"rarity":4,"points":40,"synonyms":"","clue":"گیرنده امواج"},
        {"category":"اشیا","word":"ساعت خورشیدی","difficulty":5,"rarity":5,"points":50,"synonyms":"","clue":"زمان‌سنج باستانی"},
        {"category":"اشیا","word":"اسطرلاب","difficulty":5,"rarity":5,"points":50,"synonyms":"","clue":"ابزار نجوم قدیم"},
        {"category":"اشیا","word":"تخته‌سیاه","difficulty":5,"rarity":4,"points":40,"synonyms":"","clue":"ابزار آموزشی قدیم"},
        {"category":"اشیا","word":"گوشواره","difficulty":5,"rarity":4,"points":40,"synonyms":"","clue":"زیورآلات گوش"},
        {"category":"اشیا","word":"دستبند","difficulty":5,"rarity":4,"points":40,"synonyms":"","clue":"زیورآلات دست"},
        {"category":"اشیا","word":"انگشتر","difficulty":5,"rarity":4,"points":40,"synonyms":"","clue":"حلقه انگشت"},
        {"category":"اشیا","word":"گردنبند","difficulty":5,"rarity":4,"points":40,"synonyms":"","clue":"آویز گردن"},
        {"category":"اشیا","word":"قفل","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"بست ایمنی"},
        {"category":"اشیا","word":"کلید","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"ابزار باز کردن قفل"},
        {"category":"اشیا","word":"قلم‌مو","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"ابزار نقاشی"},
        {"category":"اشیا","word":"دفتر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"محل نوشتن مشق"},
        {"category":"اشیا","word":"پاک‌کن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار اصلاح نوشتن"},
        {"category":"اشیا","word":"تراش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"تیزکننده مداد"},
        {"category":"اشیا","word":"میز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سطح کار"},
        {"category":"اشیا","word":"فرش","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"کف‌پوش خانه"},
        {"category":"اشیا","word":"بالش","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"تکیه‌گاه سر در خواب"},
        {"category":"اشیا","word":"پتو","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"روانداز گرم"},
        {"category":"اشیا","word":"شانه","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"مرتب‌کننده مو"},
        {"category":"اشیا","word":"مسواک","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"پاک‌کننده دندان"},
        {"category":"اشیا","word":"صابون","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"شوینده دست"},
        {"category":"اشیا","word":"حوله","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"خشک‌کننده بدن"},
        {"category":"اشیا","word":"کبریت","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"ابزار آتش‌زنه"},
        {"category":"اشیا","word":"فندک","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"شعله‌ساز کوچک"},
        {"category":"اشیا","word":"سطل","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"ظرف حمل مایعات یا زباله"},
        {"category":"اشیا","word":"جارو","difficulty":2,"rarity":1,"points":12,"synonyms":"","clue":"ابزار نظافت"},
        {"category":"اشیا","word":"تلفن","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"ابزار ارتباطی"},
        {"category":"اشیا","word":"رادیو","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"گیرنده امواج صوتی"},
        {"category":"اشیا","word":"تلویزیون","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"نمایشگر تصویر و صدا"},
        {"category":"اشیا","word":"پنکه","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"خنک‌کننده هوا"},
        {"category":"اشیا","word":"بخاری","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"گرم‌کننده اتاق"},
        {"category":"اشیا","word":"یخچال","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"سردکننده غذا"},
        {"category":"اشیا","word":"اجاق‌گاز","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"وسیله پخت‌وپز"},
        {"category":"اشیا","word":"ماشین لباسشویی","difficulty":4,"rarity":3,"points":30,"synonyms":"","clue":"شورنده لباس"},
        {"category":"اشیا","word":"اتو","difficulty":3,"rarity":2,"points":20,"synonyms":"","clue":"صاف‌کننده لباس"},
        {"category":"اشیا","word":"چتر نجات","difficulty":5,"rarity":5,"points":50,"synonyms":"","clue":"فرود ایمن از آسمان"},
        {"category":"اشیا","word":"کپسول آتش‌نشانی","difficulty":4,"rarity":4,"points":35,"synonyms":"","clue":"خاموش‌کننده حریق"},
        {"category":"اشیا","word":"نقشه","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"راهنمای مسیر"},
        {"category":"اشیا","word":"پرچم","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"نماد کشور"},
        {"category":"اشیا","word":"تنبک","difficulty":4,"rarity":4,"points":35,"synonyms":"","clue":"ساز کوبه‌ای"},
        {"category":"اشیا","word":"سه‌تار","difficulty":4,"rarity":4,"points":35,"synonyms":"","clue":"ساز ایرانی"},
        {"category":"اشیا","word":"شطرنج","difficulty":4,"rarity":3,"points":30,"synonyms":"","clue":"بازی فکری"},
        {"category":"اشیا","word":"تاس","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"مکعب بازی"},
        {"category":"اشیا","word":"بادبادک","difficulty":3,"rarity":3,"points":25,"synonyms":"","clue":"پرنده کاغذی"},
        {"category":"اشیا","word":"توپ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله بازی"},
        {"category":"اشیا","word":"عروسک","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"اسباب‌بازی انسان‌نما"},
        {"category":"اشیا","word":"ماشین اسباب‌بازی","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"مدل کوچک خودرو"},
        {"category":"اشیا","word":"کوله پشتی","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"کیف حمل وسایل"},
        {"category":"اشیا","word":"ذره بین","difficulty":2,"rarity":2,"points":15,"synonyms":"","clue":"وسیله‌ای برای بزرگ‌نمایی اشیاء ریز"},  {"category":"اشیا","word":"چنگال","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای خوردن غذا استفاده می‌شود"},
        {"category":"اشیا","word":"کارد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای تیز برای بریدن"},
        {"category":"اشیا","word":"بشقاب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"غذا را روی آن می‌گذارند"},
        {"category":"اشیا","word":"کاسه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی گود برای غذا"},
        {"category":"اشیا","word":"قوری","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"برای دم کردن چای"},
        {"category":"اشیا","word":"سماور","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای جوشاندن آب چای"},
        {"category":"اشیا","word":"کتری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای جوشاندن آب"},
        {"category":"اشیا","word":"ماهیتابه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای سرخ کردن غذا"},
        {"category":"اشیا","word":"قابلمه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای پخت غذا"},
        {"category":"اشیا","word":"ملاقه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای برداشتن سوپ"},
        {"category":"اشیا","word":"رنده","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای ریز کردن مواد غذایی"},
        {"category":"اشیا","word":"آبکش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای جدا کردن آب از غذا"},
        {"category":"اشیا","word":"تخته نان","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"سطحی برای آماده کردن نان یا غذا"},
        {"category":"اشیا","word":"سینی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای حمل کردن چای یا غذا"},
        {"category":"اشیا","word":"ساعت زنگ‌دار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ساعتی که زنگ می‌زند"},
        {"category":"اشیا","word":"ماشین تحریر","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"وسیله‌ای قدیمی برای تایپ"},
        {"category":"اشیا","word":"خودکار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای نوشتن"},{"category":"اشیا","word":"ماژیک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قلمی با رنگ پررنگ"},
        {"category":"اشیا","word":"پرگار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"برای رسم دایره"},
        {"category":"اشیا","word":"نقاله","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"برای اندازه‌گیری زاویه"},
        {"category":"اشیا","word":"گونیا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار هندسی مدرسه"},
        {"category":"اشیا","word":"پاکت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای گذاشتن نامه"},
        {"category":"اشیا","word":"تمبر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"روی نامه چسبانده می‌شود"},
        {"category":"اشیا","word":"نامه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای پیام نوشتاری"},
        {"category":"اشیا","word":"جعبه ابزار","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"محفظه‌ای برای نگهداری ابزار"},
        {"category":"اشیا","word":"منگنه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"برای اتصال کاغذها"},
        {"category":"اشیا","word":"سوزن‌منگنه","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"فلز کوچک خم‌شونده برای اتصال"},
        {"category":"اشیا","word":"گیره کاغذ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای نگه داشتن ورقه‌ها کنار هم"},
        {"category":"اشیا","word":"چسب نواری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای شفاف برای چسباندن"},
        {"category":"اشیا","word":"قیچی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای بریدن کاغذ و پارچه"},
        {"category":"اشیا","word":"خط‌کش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای کشیدن خط صاف"},
        {"category":"اشیا","word":"میز اتو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"میزی برای صاف کردن لباس"},
        {"category":"اشیا","word":"چوب‌لباسی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای آویزان کردن لباس"},
        {"category":"اشیا","word":"کمد دیواری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"فضایی برای نگهداری لباس‌ها"},
        {"category":"اشیا","word":"دراور","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"میز دارای کشوهای متعدد"},
        {"category":"اشیا","word":"قفسه کتاب","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"محل چیدمان کتاب‌ها"},
        {"category":"اشیا","word":"کتابخانه","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مبلمانی برای نگهداری کتاب"},
        {"category":"اشیا","word":"بالشتک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بالش کوچک تزئینی"},
        {"category":"اشیا","word":"روتختی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پوشش تخت‌خواب"},
        {"category":"اشیا","word":"تشک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"لایه نرم روی تخت"},
        {"category":"اشیا","word":"پشه بند","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"تور محافظ در برابر حشرات"},
        {"category":"اشیا","word":"پرده کرکره","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"نوعی پرده قابل تنظیم"},
        {"category":"اشیا","word":"لوستر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"چراغ تزئینی آویزان از سقف"},
        {"category":"اشیا","word":"دیوارکوب","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"چراغ نصب شده روی دیوار"},
        {"category":"اشیا","word":"شمع","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای روشنایی قدیمی"},
        {"category":"اشیا","word":"شمعدان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایه‌ای برای نگه داشتن شمع"},
        {"category":"اشیا","word":"گلدان شیشه‌ای","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ظرف شفاف برای گل"},
        {"category":"اشیا","word":"قاب عکس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای نمایش عکس"},
        {"category":"اشیا","word":"مجسمه","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"اثر هنری حجم‌دار"},
        {"category":"اشیا","word":"گلدان مسی","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"ظرف فلزی تزئینی"},
        {"category":"اشیا","word":"دکمه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای بستن لباس"},
        {"category":"اشیا","word":"زیپ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار اتصال دو لبه پارچه"},
        {"category":"اشیا","word":"سنجاق قفلی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"سنجاق کوچک برای اتصال پارچه"},
        {"category":"اشیا","word":"کمربند","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای نگه داشتن شلوار"},
        {"category":"اشیا","word":"عینک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای بهبود بینایی"},
        {"category":"اشیا","word":"شال‌گردن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پوششی برای گرم نگه داشتن گردن"},
        {"category":"اشیا","word":"دستکش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پوشش محافظ برای دست"},
        {"category":"اشیا","word":"جوراب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پوشش پا در کفش"},
        {"category":"اشیا","word":"کیف پول","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"محلی برای نگهداری اسکناس"},
        {"category":"اشیا","word":"کولر گازی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"سیستم سرمایشی مدرن"},
        {"category":"اشیا","word":"آب‌میوه‌گیری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای گرفتن آب میوه"},
        {"category":"اشیا","word":"توستر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای برشته کردن نان"},
        {"category":"اشیا","word":"مایکروفر","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"دستگاهی برای گرم کردن سریع غذا"},
        {"category":"اشیا","word":"قهوه‌ساز","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"دستگاهی برای دم کردن قهوه"},
        {"category":"اشیا","word":"سشوار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای خشک کردن مو"},
        {"category":"اشیا","word":"ریش‌تراش","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاه اصلاح صورت آقایان"},
        {"category":"اشیا","word":"اتوی مو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای صاف کردن مو"},
        {"category":"اشیا","word":"نخ دندان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار بهداشتی برای تمیز کردن دندان"},
        {"category":"اشیا","word":"شامپو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مایع مخصوص شستشوی مو"},
        {"category":"اشیا","word":"ادکلن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مایع خوشبو کننده"},
        {"category":"اشیا","word":"دستمال کاغذی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برگه‌های نازک برای نظافت"},
        {"category":"اشیا","word":"چسب مایع","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ماده‌ای برای چسباندن سطوح"},
        {"category":"اشیا","word":"ترازوی دیجیتال","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"دستگاهی برای اندازه‌گیری دقیق وزن"},
        {"category":"اشیا","word":"جاروبرقی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله برقی برای نظافت کف"},
        {"category":"اشیا","word":"چرخ خیاطی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"دستگاهی برای دوخت و دوز"},
        {"category":"اشیا","word":"فلاسک مسافرتی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ظرفی برای گرم نگه داشتن چای"},
        {"category":"اشیا","word":"چتر آفتابی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"سایه بانی برای محافظت در برابر نور خورشید"},
        {"category":"اشیا","word":"چراغ مطالعه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"لامپ رومیزی مخصوص خواندن"},
        {"category":"اشیا","word":"ذره‌بین دستی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"عدسی دسته دار برای بزرگنمایی"},
        {"category":"اشیا","word":"کیف دستی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کیف کوچک برای استفاده روزانه"},
        {"category":"اشیا","word":"چمدان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کیف بزرگ چرخدار برای مسافرت"},
        {"category":"اشیا","word":"قفل دوچرخه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"زنجیر یا کابل برای ایمنی دوچرخه"},
        {"category":"اشیا","word":"پمپ باد","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"دستگاهی برای باد کردن لاستیک و توپ"},
        {"category":"اشیا","word":"تلمبه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزار دستی برای باد کردن چرخ دوچرخه"},
        {"category":"اشیا","word":"جک ماشین","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ابزاری برای بالا بردن خودرو هنگام تعویض چرخ"},
        {"category":"اشیا","word":"کپسول گاز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مخزن فلزی حاوی گاز مایع"},
        {"category":"اشیا","word":"پیک‌نیک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"اجاق گاز کوچک و قابل حمل مسافرتی"},
        {"category":"اشیا","word":"زیرسیگاری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای ریختن خاکستر"},
        {"category":"اشیا","word":"فندک اتمی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"وسیله ای با شعله قوی برای روشن کردن آتش"},
        {"category":"اشیا","word":"گیره لباس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"گیره کوچک برای نگه داشتن لباس روی بند"},
        {"category":"اشیا","word":"سبد خرید","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سبد حمل کالا در فروشگاه"},
        {"category":"اشیا","word":"نردبان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری پله‌دار برای رفتن به ارتفاع"},
        {"category":"اشیا","word":"پیچ‌گوشتی برقی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"ابزار الکترونیکی برای باز و بسته کردن پیچ"},
        {"category":"اشیا","word":"اره","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"ابزار فلزی دندانه‌دار برای بریدن چوب"},
        {"category":"اشیا","word":"انبردست","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای نگه داشتن یا بریدن سیم و مفتول"},
        {"category":"اشیا","word":"دم‌باریک","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"انبردستی با نوک دراز برای فضاهای تنگ"},
        {"category":"اشیا","word":"متر لیزری","difficulty":3,"rarity":4,"points":20,"synonyms":"","clue":"دستگاه مدرن برای اندازه‌گیری دقیق مسافت"},
        {"category":"اشیا","word":"تراز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ابزار سنجش صاف و افقی بودن سطوح"},
        {"category":"اشیا","word":"کلاه ایمنی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پوشش محافظ سر در کارگاه یا موتورسواری"},
        {"category":"اشیا","word":"دستکش کار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستکش ضخیم برای محافظت از دست‌ها حین کار"},  {"category":"اشیا","word":"سنجاق","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای کوچک برای وصل کردن پارچه یا کاغذ"},
        {"category":"اشیا","word":"چکش لاستیکی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"چکشی که سر نرم دارد"},
        {"category":"اشیا","word":"اره مویی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"برای بریدن دقیق چوب"},
        {"category":"اشیا","word":"پیستون","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"قطعه‌ای متحرک در موتور"},
        {"category":"اشیا","word":"رول‌پلاک","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"برای محکم کردن پیچ در دیوار"},
        {"category":"اشیا","word":"آچار فرانسه","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"آچاری با دهانه قابل تنظیم"},
        {"category":"اشیا","word":"سوهان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"برای صاف کردن سطح"},
        {"category":"اشیا","word":"تیشه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای تراشیدن و کندن"},
        {"category":"اشیا","word":"دریل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"برای سوراخ کردن"},
        {"category":"اشیا","word":"فرغون","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"وسیله‌ای برای جابه‌جایی خاک یا بار"},
        {"category":"اشیا","word":"بیل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کندن زمین"},
        {"category":"اشیا","word":"کلنگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای شکستن خاک یا سنگ"},
        {"category":"اشیا","word":"چرخ دستی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای حمل بار"},
        {"category":"اشیا","word":"سطل زباله","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"جایی برای دور ریختن زباله"},
        {"category":"اشیا","word":"خودنویس","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"نوعی قلم برای نوشتن"},
        {"category":"اشیا","word":"روان‌نویس","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"قلمی با جوهر روان"},
        {"category":"اشیا","word":"مغار","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"ابزاری برای کنده‌کاری"},
        {"category":"اشیا","word":"چرخ‌گوشت","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"وسیله‌ای برای خرد کردن گوشت"},
        {"category":"اشیا","word":"جاروبرقی رباتیک","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"جاروبرقی‌ای که خودش حرکت می‌کند"},
        {"category":"اشیا","word":"چراغ‌قوه پیشانی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"چراغی که روی پیشانی بسته می‌شود"},
        {"category":"اشیا","word":"دماسنج جیوه‌ای","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"وسیله‌ای برای اندازه‌گیری دما"},
        {"category":"اشیا","word":"کاغذ سنباده","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"برای زبر کردن یا صاف کردن سطح"},
        {"category":"اشیا","word":"چسب پهن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"چسبی نواری و عریض"},
        {"category":"اشیا","word":"پانچ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"برای سوراخ کردن کاغذ"},
        {"category":"اشیا","word":"آویز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"چیزی که از جایی آویزان می‌شود"},
        {"category":"اشیا","word":"سه‌پایه دوربین","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"پایه‌ای برای ثابت نگه داشتن دوربین"},{"category":"اشیا","word":"بوم نقاشی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پارچه‌ای برای نقاشی کردن روی آن"},
        {"category":"اشیا","word":"پالت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صفحه‌ای برای ترکیب رنگ‌ها"},
        {"category":"اشیا","word":"مداد نوکی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قلمی که نوک آن تعویض می‌شود"},
        {"category":"اشیا","word":"غلط‌گیر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای پوشاندن اشتباه نوشتاری"},
        {"category":"اشیا","word":"کیسه خواب","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"جای خواب مسافرتی"},
        {"category":"اشیا","word":"کپسول اکسیژن","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"مخزن گاز برای تنفس"},
        {"category":"اشیا","word":"ماسک تنفسی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پوشش صورت برای محافظت از تنفس"},
        {"category":"اشیا","word":"عینک شنا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"محافظ چشم در زیر آب"},
        {"category":"اشیا","word":"مایو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"لباس مخصوص شنا"},
        {"category":"اشیا","word":"کلاه شنا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پوشش سر برای استخر"},
        {"category":"اشیا","word":"دمپایی ابری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پاپوش نرم برای محیط‌های خیس"},
        {"category":"اشیا","word":"شن‌کش","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"ابزار باغبانی برای جمع کردن برگ"},
        {"category":"اشیا","word":"آب‌پاش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای آبیاری گلدان"},
        {"category":"اشیا","word":"گلدان فلزی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"ظرف فلزی برای نگهداری گل"},
        {"category":"اشیا","word":"چراغ تزئینی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"وسیله‌ای برای نورپردازی محیط"},
        {"category":"اشیا","word":"ریسه نوری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"سیمی پر از چراغ‌های کوچک"},
        {"category":"اشیا","word":"مهر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای زدن امضای رسمی"},
        {"category":"اشیا","word":"زیرلیوانی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"صفحه‌ای برای قرار دادن لیوان"},
        {"category":"اشیا","word":"سفره","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پارچه‌ای برای پهن کردن سر غذا"},
        {"category":"اشیا","word":"رومیزی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پارچه‌ای روی میز"},
        {"category":"اشیا","word":"پیش‌بند","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"لباس کار آشپزی"},
        {"category":"اشیا","word":"کلاه آشپزی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"پوشش سر مخصوص سرآشپز"},
        {"category":"اشیا","word":"دستگیره آشپزخانه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پارچه‌ای برای برداشتن ظروف داغ"},
        {"category":"اشیا","word":"وردنه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"استوانه‌ای برای پهن کردن خمیر"},
        {"category":"اشیا","word":"ترازو آشپزخانه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"برای وزن کردن مواد غذایی"},
        {"category":"اشیا","word":"همزن دستی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای مخلوط کردن مواد"},
        {"category":"اشیا","word":"کف‌گیر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کشیدن پلو"},
        {"category":"اشیا","word":"گوشت‌کوب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای له کردن مواد غذایی"},
        {"category":"اشیا","word":"رنده برقی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"وسیله‌ای برقی برای خرد کردن"},
        {"category":"اشیا","word":"صافی چای","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای جدا کردن تفاله چای"},
        {"category":"اشیا","word":"ظرف فریزری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای نگهداری غذا در فریزر"},
        {"category":"اشیا","word":"قالب کیک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ظرفی برای شکل‌دهی به کیک"},
        {"category":"اشیا","word":"توری کباب‌پز","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"وسیله‌ای فلزی برای کباب کردن"},
        {"category":"اشیا","word":"انبر ذغال","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"وسیله‌ای برای جابه‌جایی ذغال"},
        {"category":"اشیا","word":"منقل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"جایی برای روشن کردن آتش کباب"},
        {"category":"اشیا","word":"سینی چای","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برای حمل استکان‌های چای"},
        {"category":"اشیا","word":"قندان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرف مخصوص قند"},
        {"category":"اشیا","word":"نمکدان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرف مخصوص نمک"},
        {"category":"اشیا","word":"فلفل‌پاش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای پاشیدن فلفل"},
        {"category":"اشیا","word":"کره‌خوری","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"ظرفی برای سرو کره"},
        {"category":"اشیا","word":"شیرجوش","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ظرفی دسته‌دار برای جوشاندن شیر"},
        {"category":"اشیا","word":"قهوه‌جوش","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای دم کردن قهوه"},
        {"category":"اشیا","word":"نعلبکی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بشقاب کوچک زیر استکان"},
        {"category":"اشیا","word":"استکان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرف مخصوص نوشیدن چای"},
        {"category":"اشیا","word":"جام","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"ظرفی گران‌بها برای نوشیدنی"},
        {"category":"اشیا","word":"دیس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرف بزرگ برای چیدن غذا"},
        {"category":"اشیا","word":"کاسه سالاد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی بزرگ برای سرو سالاد"},
        {"category":"اشیا","word":"سبد میوه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای چیدن میوه"},
        {"category":"اشیا","word":"جعبه دستمال","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"محفظه‌ای برای جای دستمال"},
        {"category":"اشیا","word":"سفره‌دان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"محفظه‌ای برای نگهداری سفره"},
        {"category":"اشیا","word":"بشقاب‌گیر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای برداشتن بشقاب داغ"},
        {"category":"اشیا","word":"آب‌پاش دستی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای کوچک برای پاشیدن آب"},
        {"category":"اشیا","word":"میز آرایش","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"میزی مخصوص برای آرایش کردن"},
        {"category":"اشیا","word":"آینه دست","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"آینه‌ای کوچک که با دست گرفته می‌شود"},
        {"category":"اشیا","word":"پنکیک‌ساز","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"دستگاه برقی برای درست کردن پنکیک"},
        {"category":"اشیا","word":"وافل‌ساز","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"دستگاه برقی برای تهیه وافل"},
        {"category":"اشیا","word":"ترازو دیجیتال","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای دقیق برای وزن کردن"},
        {"category":"اشیا","word":"مینی‌گیتار","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"یک ساز موسیقی کوچک"},
        {"category":"اشیا","word":"ساز کاغذی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای که از کاغذ ساخته شده"},
        {"category":"اشیا","word":"کارت پستال","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کارت کوچک برای فرستادن پیام"},
        {"category":"اشیا","word":"پاک‌کن خودکار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای پاک کردن نوشته"},
        {"category":"اشیا","word":"مداد رنگی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نوعی مداد برای نقاشی"},
        {"category":"اشیا","word":"گواش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نوعی رنگ برای نقاشی"},
        {"category":"اشیا","word":"آبرنگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"رنگی که با آب ترکیب می‌شود"},
        {"category":"اشیا","word":"قلم‌مو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای رنگ کردن"},
        {"category":"اشیا","word":"پالت رنگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صفحه‌ای برای چیدن رنگ‌ها"},
        {"category":"اشیا","word":"پیکسل","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"کوچک‌ترین واحد یک تصویر دیجیتال"},
        {"category":"اشیا","word":"کابل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رشته‌ای برای انتقال برق یا داده"},
        {"category":"اشیا","word":"پریز برق","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"جایی که دوشاخه در آن قرار می‌گیرد"},
        {"category":"اشیا","word":"لامپ رشته‌ای","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"نوعی لامپ قدیمی که گرم می‌شود"},
        {"category":"اشیا","word":"باتری قلمی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"منبع انرژی کوچک برای ساعت یا ریموت"},
        {"category":"اشیا","word":"اسپیکر بلوتوثی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"دستگاهی برای پخش بی‌سیم موسیقی"},
        {"category":"اشیا","word":"هدفون","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای گوش دادن بی‌سیم"},
        {"category":"اشیا","word":"ماوس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای حرکت نشانگر کامپیوتر"},  {"category":"اشیا","word":"کیبورد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای تایپ کردن با کامپیوتر"},
        {"category":"اشیا","word":"کیس کامپیوتر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بدنه اصلی سیستم رایانه‌ای"},
        {"category":"اشیا","word":"مانیتور","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"صفحه نمایش کامپیوتر"},
        {"category":"اشیا","word":"مودم","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای اتصال به اینترنت"},
        {"category":"اشیا","word":"فلش مموری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای کوچک برای ذخیره داده"},
        {"category":"اشیا","word":"هارد دیسک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"قطعه‌ای برای ذخیره‌سازی اطلاعات کامپیوتر"},
        {"category":"اشیا","word":"وب‌کم","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"دوربین کوچک برای ارتباط تصویری"},
        {"category":"اشیا","word":"میکروفون","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای ضبط یا تقویت صدا"},
        {"category":"اشیا","word":"پد ماوس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"زیرانداز مخصوص ماوس"},
        {"category":"اشیا","word":"هندزفری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای شنیدن صدا بدون مزاحمت دیگران"},
        {"category":"اشیا","word":"تبلت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رایانه‌ای باریک و قابل حمل"},
        {"category":"اشیا","word":"قلم نوری","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"قلمی مخصوص برای طراحی دیجیتال"},
        {"category":"اشیا","word":"پرینتر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای چاپ اسناد روی کاغذ"},
        {"category":"اشیا","word":"اسکنر","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"دستگاهی برای دیجیتالی کردن عکس‌ها"},
        {"category":"اشیا","word":"فتوکپی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای تکثیر اسناد"},
        {"category":"اشیا","word":"تلفن رومیزی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دستگاه تلفن ثابت معمولی"},
        {"category":"اشیا","word":"تلفن بی‌سیم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"تلفنی که بدون سیم کار می‌کند"},
        {"category":"اشیا","word":"فکس","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"دستگاهی برای ارسال اسناد از راه دور"},
        {"category":"اشیا","word":"ماشین حساب مهندسی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"ماشین حسابی با قابلیت‌های محاسباتی پیچیده"},
        {"category":"اشیا","word":"چرتکه","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"وسیله‌ای قدیمی برای محاسبه"},
        {"category":"اشیا","word":"گیره اسناد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای نگه داشتن کاغذها کنار هم"},
        {"category":"اشیا","word":"زونکن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پوشه بزرگ برای بایگانی اسناد"},
        {"category":"اشیا","word":"کاور کاغذ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پلاستیک محافظ برای برگه‌های کاغذ"},
        {"category":"اشیا","word":"منگنه کوب","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"ابزار صنعتی برای اتصال با منگنه بزرگ"},
        {"category":"اشیا","word":"چسب حرارتی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی که چسب جامد را ذوب می‌کند"},
        {"category":"اشیا","word":"تفنگ چسب","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای چسباندن با حرارت"},
        {"category":"اشیا","word":"شابلون","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صفحه‌ای برای کشیدن اشکال دقیق"},
        {"category":"اشیا","word":"پرگار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای کشیدن دایره"},
        {"category":"اشیا","word":"نقاله","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری نیم‌دایره برای اندازه‌گیری زاویه"},
        {"category":"اشیا","word":"گونیا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای رسم زوایای قائمه"},
        {"category":"اشیا","word":"خط‌کش فلزی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"ابزار اندازه‌گیری مستحکم"},
        {"category":"اشیا","word":"کاتر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"تیغ مخصوص برای بریدن کاغذ"},
        {"category":"اشیا","word":"تخته شاسی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"صفحه‌ای برای زیردستی و نوشتن"},
        {"category":"اشیا","word":"مداد تراش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای تیز کردن مداد"},
        {"category":"اشیا","word":"پایه چسب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"جایگاهی برای نوار چسب"},
        {"category":"اشیا","word":"خودکار چندرنگ","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"خودکاری با جوهرهای مختلف"},
        {"category":"اشیا","word":"دفتر یادداشت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کتابچه‌ای کوچک برای ثبت مطالب"},
        {"category":"اشیا","word":"کلاسور","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پوشه‌ای برای دسته‌بندی ورق‌ها"},
        {"category":"اشیا","word":"سررسید","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دفتر مخصوص ثبت تاریخ و قرارها"},
        {"category":"اشیا","word":"کتاب‌خوان الکترونیکی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"دستگاهی برای خواندن کتاب دیجیتال"},
        {"category":"اشیا","word":"پاوربانک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شارژر همراه موبایل"},
        {"category":"اشیا","word":"کابل شارژ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سیم برای شارژ کردن دستگاه"},
        {"category":"اشیا","word":"آداپتور","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای تبدیل ولتاژ برق"},
        {"category":"اشیا","word":"هاب یو‌اس‌بی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"دستگاهی برای افزایش پورت‌های کامپیوتر"},
        {"category":"اشیا","word":"سی‌دی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"لوح فشرده برای ذخیره اطلاعات"},
        {"category":"اشیا","word":"دی‌وی‌دی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"لوح فشرده با ظرفیت بالا"},
        {"category":"اشیا","word":"کیسه یو‌اس‌بی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"کیفی برای نظم‌دهی کابل‌ها"},
        {"category":"اشیا","word":"موس پد گیمینگ","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"پد بزرگ مخصوص بازی"},
        {"category":"اشیا","word":"کنسول بازی دستی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"دستگاه بازی قابل حمل"},
        {"category":"اشیا","word":"دسته بازی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای کنترل بازی"},
        {"category":"اشیا","word":"پروژکتور خانگی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای نمایش تصویر روی دیوار"},
        {"category":"اشیا","word":"اسکرین","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پرده مخصوص نمایش تصویر دیجیتال"},
        {"category":"اشیا","word":"کابل HDMI","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"سیم برای انتقال تصویر و صدا"},
        {"category":"اشیا","word":"پریز چندکاره","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای اضافه کردن چندین پریز"},
        {"category":"اشیا","word":"چراغ مطالعه رومیزی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"لامپی که مخصوص مطالعه در میز است"},
        {"category":"اشیا","word":"کتاب‌خانه کوچک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"قفسه‌ای کوچک برای نگهداری کتاب"},
        {"category":"اشیا","word":"گلدان خودکار","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"گلدانی که نیاز به آبیاری دستی ندارد"},
        {"category":"اشیا","word":"اسپری گیاهان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای پاشیدن آب روی برگ‌ها"},
        {"category":"اشیا","word":"گلدان سرامیکی","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"ظرف گل با جنس سفال پخته شده"},
        {"category":"اشیا","word":"گلدان سفالی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرف گل با بافت سنتی"},
        {"category":"اشیا","word":"آتش‌زنه","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"وسیله‌ای قدیمی برای ایجاد جرقه"},
        {"category":"اشیا","word":"تراش دستی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای تیز کردن مداد"},
        {"category":"اشیا","word":"پاک‌کن پلاستیکی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای پاک کردن نوشته‌های مداد"},
        {"category":"اشیا","word":"مداد رنگی آب‌رو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مدادی که با آب رنگ می‌دهد"},
        {"category":"اشیا","word":"قلم‌ساز","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"دستگاهی برای ساخت یا اصلاح قلم‌ها"},
        {"category":"اشیا","word":"گونیا مهندسی","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ابزار دقیق برای رسم زوایا در نقشه"},
        {"category":"اشیا","word":"پرگار مهندسی","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ابزار دقیق برای رسم دایره در نقشه"},
        {"category":"اشیا","word":"تخته مهندسی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"صفحه‌ای بزرگ برای طراحی نقشه‌ها"},
        {"category":"اشیا","word":"میز مهندسی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"میزی با قابلیت تنظیم زاویه برای طراحی"},
        {"category":"اشیا","word":"کالای دیجیتال","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاه‌های الکترونیکی مدرن"},
        {"category":"اشیا","word":"گوشی هوشمند","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"موبایلی با قابلیت‌های کامپیوتری"},
        {"category":"اشیا","word":"شارژر بی‌سیم","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"وسیله‌ای برای شارژ گوشی بدون سیم"},
        {"category":"اشیا","word":"کابل اپتیک","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"فیبر نوری برای انتقال سریع داده"},
        {"category":"اشیا","word":"تراشه الکترونیکی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"قطعه بسیار کوچک داخل دستگاه‌ها"},
        {"category":"اشیا","word":"برد الکترونیکی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"صفحه‌ای سبز که قطعات روی آن هستند"},
        {"category":"اشیا","word":"آچار فرانسه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"آچاری که اندازه دهانه آن قابل تغییر است"},
        {"category":"اشیا","word":"پیچ‌گوشتی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای بستن یا باز کردن پیچ‌ها"},
        {"category":"اشیا","word":"انبردست","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای نگه داشتن یا قطع کردن سیم"},
        {"category":"اشیا","word":"آچار آلن","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"آچاری با مقطع شش‌ضلعی برای پیچ‌های خاص"},
        {"category":"اشیا","word":"چکش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کوبیدن میخ یا اجسام سخت"},
        {"category":"اشیا","word":"متر نواری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای اندازه‌گیری طول اجسام"},
        {"category":"اشیا","word":"تراز","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ابزاری برای اطمینان از صاف بودن سطح"},
        {"category":"اشیا","word":"دریل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برقی برای ایجاد سوراخ در دیوار"},
        {"category":"اشیا","word":"سنباده","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کاغذی زبر برای صاف کردن سطح چوب یا فلز"},
        {"category":"اشیا","word":"چسب دوطرفه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نوعی چسب که از هر دو طرف آن چسبندگی وجود دارد"},
        {"category":"اشیا","word":"چسب قطره‌ای","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"چسب بسیار سریع و قوی برای اشیاء کوچک"},
        {"category":"اشیا","word":"اسفنج تمیزکننده","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای نرم برای شستشو و تمیزکاری"},
        {"category":"اشیا","word":"دستمال میکروفایبر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستمالی نرم و مخصوص برای تمیز کردن لنز و مانیتور"},
        {"category":"اشیا","word":"مایع شیشه‌شوی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"محلولی برای شفاف کردن شیشه‌ها"},
        {"category":"اشیا","word":"دستکش کار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای محافظت از دست در کارهای سخت"},
        {"category":"اشیا","word":"کلاه ایمنی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کلاهی برای محافظت از سر در محیط‌های صنعتی"},
        {"category":"اشیا","word":"کوله پشتی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کیفی که روی پشت حمل می‌شود"},
        {"category":"اشیا","word":"کیف لپ‌تاپ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کیفی مخصوص برای حمل کامپیوتر قابل حمل"},
        {"category":"اشیا","word":"باکس ذخیره","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"جعبه‌ای برای نظم دادن به وسایل"},
        {"category":"اشیا","word":"چتر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای محافظت در برابر باران یا آفتاب"},
        {"category":"اشیا","word":"عینک آفتابی","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"عینکی برای محافظت از چشم در برابر نور شدید"},
        {"category":"اشیا","word":"ساعت مچی","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای کوچک برای نمایش زمان روی دست"},
        {"category":"اشیا","word":"قاب گوشی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"محافظی که دور گوشی قرار می‌گیرد"},
        {"category":"اشیا","word":"هدفون بلوتوثی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"دستگاهی بی‌سیم برای گوش دادن به موسیقی"},
        {"category":"اشیا","word":"اسپیکر قابل حمل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای پخش صدای بلند و جابه‌جا شدنی"},  {"category":"اشیا","word":"فلاسک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای نگه داشتن دمای نوشیدنی"},
        {"category":"اشیا","word":"ترازو دیجیتال","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای اندازه‌گیری دقیق وزن"},
        {"category":"اشیا","word":"دماسنج اتاق","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای نمایش دمای محیط"},
        {"category":"اشیا","word":"رطوبت‌سنج","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"وسیله‌ای برای اندازه‌گیری میزان رطوبت هوا"},
        {"category":"اشیا","word":"چراغ‌قوه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای تولید نور در تاریکی"},
        {"category":"اشیا","word":"باتری قلمی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"منبع انرژی کوچک برای وسایل الکترونیکی"},
        {"category":"اشیا","word":"ریموت کنترل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای کنترل از راه دور تلویزیون"},
        {"category":"اشیا","word":"پنکه رومیزی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای خنک کردن هوای اطراف"},
        {"category":"اشیا","word":"بخاری برقی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای گرم کردن فضای کوچک"},
        {"category":"اشیا","word":"اتو بخار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای صاف کردن چروک لباس"},
        {"category":"اشیا","word":"میز اتو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صفحه‌ای مخصوص برای قرار دادن لباس جهت اتوکشی"},
        {"category":"اشیا","word":"جاروبرقی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای مکش گرد و غبار از زمین"},
        {"category":"اشیا","word":"سطل زباله","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای جمع‌آوری ضایعات"},
        {"category":"اشیا","word":"آینه دیواری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شیشه‌ای بازتابنده برای دیدن تصویر خود"},
        {"category":"اشیا","word":"قاب عکس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"چارچوبی برای نگهداری و نمایش عکس"},
        {"category":"اشیا","word":"گلدان شیشه‌ای","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"ظرف شفاف برای نگهداری شاخه گل"},
        {"category":"اشیا","word":"شمع‌دان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایه‌ای برای نگه داشتن شمع روشن"},
        {"category":"اشیا","word":"عود‌سوز","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"ظرفی مخصوص برای سوزاندن عود خوشبو"},
        {"category":"اشیا","word":"بالش طبی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بالشی با طراحی خاص برای سلامت گردن"},
        {"category":"اشیا","word":"پتو مسافرتی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"روانداز سبک و کم‌حجم برای سفر"},
        {"category":"اشیا","word":"روتختی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پارچه‌ای که روی تشک و تخت کشیده می‌شود"},
        {"category":"اشیا","word":"کوسن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بالش‌های کوچک تزیینی برای مبل"},
        {"category":"اشیا","word":"فرش دستباف","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"زیراندازی هنری که با دست بافته شده"},
        {"category":"اشیا","word":"پرده توری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پرده‌ای نازک برای عبور نور"},
        {"category":"اشیا","word":"لوستر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"چراغ‌آویز تزیینی برای سقف"},
        {"category":"اشیا","word":"تلفن رومیزی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دستگاهی قدیمی برای برقراری تماس تلفنی"},
        {"category":"اشیا","word":"مودم وای‌فای","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دستگاهی برای اتصال به اینترنت بی‌سیم"},
        {"category":"اشیا","word":"کیبورد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"صفحه‌کلید کامپیوتر"},
        {"category":"اشیا","word":"ماوس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار حرکت‌دهنده نشانگر در کامپیوتر"},
        {"category":"اشیا","word":"مانیتور","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"صفحه نمایش کامپیوتر"},
        {"category":"اشیا","word":"کیس کامپیوتر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"جعبه اصلی که قطعات کامپیوتر داخل آن است"},
        {"category":"اشیا","word":"لپ‌تاپ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کامپیوتر قابل حمل"},
        {"category":"اشیا","word":"تبلت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رایانه مسطح و لمسی"},
        {"category":"اشیا","word":"هندزفری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"گوشی کوچکی که داخل گوش قرار می‌گیرد"},
        {"category":"اشیا","word":"هدفون","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دستگاهی که روی گوش قرار می‌گیرد برای شنیدن صدا"},
        {"category":"اشیا","word":"وب‌کم","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دوربین کوچک برای تماس ویدیویی کامپیوتر"},
        {"category":"اشیا","word":"میکروفون","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای ضبط صدا"},
        {"category":"اشیا","word":"فلش مموری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حافظه کوچک قابل حمل"},
        {"category":"اشیا","word":"هارد اکسترنال","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"حافظه بزرگ که بیرون کامپیوتر وصل می‌شود"}, {"category":"اشیا","word":"زونکن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پوشه بزرگ برای بایگانی اسناد"},
        {"category":"اشیا","word":"منگنه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای اتصال ورق‌های کاغذ"},
        {"category":"اشیا","word":"سوزن منگنه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"تکه‌های فلزی کوچک برای دستگاه منگنه"},
        {"category":"اشیا","word":"گیره کاغذ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای فلزی برای نگه داشتن چند برگ"},
        {"category":"اشیا","word":"پانچ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای سوراخ کردن کاغذ"},
        {"category":"اشیا","word":"پاکت نامه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کیسه کاغذی برای قرار دادن نامه"},
        {"category":"اشیا","word":"تمبر","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"برچسب کوچک برای پست کردن نامه"},
        {"category":"اشیا","word":"کارت ویزیت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کارت کوچک با مشخصات تماس فرد"},
        {"category":"اشیا","word":"راکت تنیس","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای ضربه زدن به توپ تنیس"},
        {"category":"اشیا","word":"توپ فوتبال","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"توپ مخصوص ورزش پرطرفدار فوتبال"},
        {"category":"اشیا","word":"شطرنج","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بازی فکری دونفره روی صفحه ۶۴ خانه"},
        {"category":"اشیا","word":"گیتار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ساز زهی معروف با سیم‌های نایلونی یا فلزی"},
        {"category":"اشیا","word":"ویولن","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ساز زهی آرشه‌ای کوچک و خوش‌صدا"},
        {"category":"اشیا","word":"فلوت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ساز بادی ساده و چوبی یا فلزی"},
        {"category":"اشیا","word":"سه‌تار","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ساز اصیل ایرانی با کاسه گلابی‌شکل"},
        {"category":"اشیا","word":"پیانو","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"ساز کلاویه‌ای بزرگ و پیچیده"},
        {"category":"اشیا","word":"بوم نقاشی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پارچه کشیده شده روی قاب برای نقاشی"},
        {"category":"اشیا","word":"پالت رنگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صفحه‌ای برای ترکیب کردن رنگ‌های نقاشی"},
        {"category":"اشیا","word":"قلم‌مو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای آغشته کردن رنگ به بوم"},
        {"category":"اشیا","word":"سه‌پایه نقاشی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"پایه‌ای برای نگه داشتن بوم در زمان نقاشی"}, {"category":"اشیا","word":"چرخ سفالگری","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"دستگاهی برای فرم دادن به گل رس"},
        {"category":"اشیا","word":"قلاب بافتنی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای گره زدن کاموا و بافتن"},
        {"category":"اشیا","word":"چرخ خیاطی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای دوختن پارچه"},
        {"category":"اشیا","word":"انگشتانه","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"محافظ فلزی برای نوک انگشت هنگام خیاطی"},
        {"category":"اشیا","word":"متر خیاطی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نوار انعطاف‌پذیر برای اندازه‌گیری بدن"},
        {"category":"اشیا","word":"اتو مسافرتی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"اتو کوچک و تاشو برای سفر"},
        {"category":"اشیا","word":"آب‌میوه‌گیری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای گرفتن عصاره میوه‌ها"},
        {"category":"اشیا","word":"همزن برقی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای مخلوط کردن مواد کیک"},
        {"category":"اشیا","word":"گوشت‌کوب برقی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای له کردن سریع مواد غذایی"},
        {"category":"اشیا","word":"توستر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای برشته کردن نان"},
        {"category":"اشیا","word":"ساندویچ‌ساز","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای پختن ساندویچ‌های گرم"},
        {"category":"اشیا","word":"قهوه‌جوش","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ظرف یا دستگاهی برای دم کردن قهوه"},
        {"category":"اشیا","word":"کتری برقی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای جوشاندن سریع آب با برق"},
        {"category":"اشیا","word":"ظروف ادویه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرف‌های کوچک برای نگهداری ادویه‌جات"},
        {"category":"اشیا","word":"رنده دستی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای خرد کردن یا پودر کردن مواد غذایی"},
        {"category":"اشیا","word":"همزن دستی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری ساده برای مخلوط کردن مواد مایع"},
        {"category":"اشیا","word":"وردنه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"استوانه‌ای برای پهن کردن خمیر نان یا شیرینی"},{"category":"اشیا","word":"کفگیر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری پهن برای جابه‌جایی غذا در تابه"},
        {"category":"اشیا","word":"ملاقه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری گود برای کشیدن سوپ و خورشت"},
        {"category":"اشیا","word":"پوست‌کن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کندن پوست میوه‌ها و سبزیجات"},
        {"category":"اشیا","word":"در بازکن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای باز کردن درب بطری یا کنسرو"},
        {"category":"اشیا","word":"قیچی آشپزخانه","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"قیچی مخصوص برای بریدن مواد غذایی"},
        {"category":"اشیا","word":"زیرلیوانی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"صفحه کوچک برای قرار دادن لیوان روی میز"},
        {"category":"اشیا","word":"پیش‌بند","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"لباسی که هنگام آشپزی برای جلوگیری از لک استفاده می‌شود"},
        {"category":"اشیا","word":"دستکش فر","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"دستکش ضخیم برای برداشتن سینی داغ از فر"},
        {"category":"اشیا","word":"سفره یکبار مصرف","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پوشش پلاستیکی برای روی میز غذاخوری"},{"category":"اشیا","word":"جاظرفی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"قفسه‌ای برای قرار دادن ظروف پس از شستن"},
        {"category":"اشیا","word":"سبد آبکش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی مشبک برای شستن برنج یا سبزیجات"},
        {"category":"اشیا","word":"ترازو آشپزخانه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای دقیق برای اندازه‌گیری وزن مواد غذایی"},
        {"category":"اشیا","word":"دماسنج گوشت","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"ابزاری برای سنجش دمای پخت داخل گوشت"},
        {"category":"اشیا","word":"تایمر آشپزخانه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ساعتی برای تنظیم زمان پخت غذا"},
        {"category":"اشیا","word":"قالب کیک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ظرفی که خمیر را داخل آن می‌ریزند تا کیک شود"},{"category":"اشیا","word":"آبپاش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای آبیاری گلدان‌ها"},
        {"category":"اشیا","word":"قیچی باغبانی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای هرس کردن شاخه‌های درخت"},
        {"category":"اشیا","word":"شن‌کش","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای جمع‌آوری برگ‌ها از روی خاک"},
        {"category":"اشیا","word":"شلنگ آب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"لوله انعطاف‌پذیر برای انتقال آب"},
        {"category":"اشیا","word":"نازل آبیاری","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"سری مخصوص برای تنظیم فشار آب شلنگ"},
        {"category":"اشیا","word":"گلدان آویز","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"گلدانی که از سقف یا دیوار آویزان می‌شود"},
        {"category":"اشیا","word":"کیسه خاک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بسته‌بندی حاوی خاک گلدان"},
        {"category":"اشیا","word":"چادر مسافرتی","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"پناهگاه پارچه‌ای قابل حمل برای سفر"},
        {"category":"اشیا","word":"کیسه خواب","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"کیسه‌ای گرم برای خوابیدن در طبیعت"},
        {"category":"اشیا","word":"زیرانداز سفری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پارچه‌ای ضخیم برای نشستن در فضای باز"},{"category":"اشیا","word":"چراغ کمپینگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"چراغی که برای روشنایی در چادر استفاده می‌شود"},
        {"category":"اشیا","word":"کوله کوهنوردی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کوله‌پشتی بزرگ مخصوص حمل وسایل کوه"},
        {"category":"اشیا","word":"عصای کوهنوردی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"چوبی برای تعادل در پیاده‌روی کوهستان"},
        {"category":"اشیا","word":"قطب‌نما","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ابزاری برای تشخیص جهت‌های جغرافیایی"},
        {"category":"اشیا","word":"سوت نجات","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری کوچک برای ایجاد صدای بلند در شرایط اضطراری"},
        {"category":"اشیا","word":"نقشه کاغذی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"راهنمای تصویری مسیرها روی کاغذ"},
        {"category":"اشیا","word":"کلاه لبه‌دار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کلاهی برای محافظت از صورت در برابر آفتاب"},
        {"category":"اشیا","word":"عینک ایمنی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"عینکی برای محافظت چشم در برابر ذرات معلق"},
        {"category":"اشیا","word":"گوش‌گیر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کاهش صدای محیط در گوش"},
        {"category":"اشیا","word":"ماسک تنفسی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پوششی برای فیلتر کردن هوای تنفسی"},
        {"category":"اشیا","word":"دستکش یکبار مصرف","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دستکش نازک برای کارهای بهداشتی"},
        {"category":"اشیا","word":"پیش‌بند کار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"لباسی برای محافظت از لباس اصلی هنگام کار"},
        {"category":"اشیا","word":"تی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای زمین‌شوی کردن"},
        {"category":"اشیا","word":"جارو دستی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری سنتی برای جمع کردن آشغال‌های زمین"},
        {"category":"اشیا","word":"دماسنج پزشکی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای اندازه‌گیری دمای بدن"},
        {"category":"اشیا","word":"باند کشی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نوار پارچه‌ای برای بستن محل آسیب‌دیده"},
        {"category":"اشیا","word":"چسب زخم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"چسب کوچک برای پوشاندن زخم‌های سطحی"},
        {"category":"اشیا","word":"پنس","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری کوچک برای برداشتن یا نگه داشتن اشیاء ریز"},
        {"category":"اشیا","word":"مسواک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای شستشوی دندان‌ها"},
        {"category":"اشیا","word":"خمیردندان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ماده‌ای خمیری برای تمیز کردن دندان"},
        {"category":"اشیا","word":"صابون قالبی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ماده جامد برای شستشوی دست و صورت"},
        {"category":"اشیا","word":"شامپو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مایع مخصوص شستشوی موی سر"},
        {"category":"اشیا","word":"حوله دستی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پارچه‌ای برای خشک کردن دست و صورت"},
        {"category":"اشیا","word":"برس مو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای مرتب کردن و شانه زدن مو"},
        {"category":"اشیا","word":"شانه چوبی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری ساده برای مرتب کردن مو با جنس چوب"},
        {"category":"اشیا","word":"ناخن‌گیر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کوتاه کردن ناخن"},
        {"category":"اشیا","word":"سوهان ناخن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای صاف کردن لبه ناخن"},
        {"category":"اشیا","word":"آینه جیبی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"آینه‌ای کوچک برای حمل در کیف"},
        {"category":"اشیا","word":"ادکلن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مایع خوشبو برای بدن"},
        {"category":"اشیا","word":"کرم مرطوب‌کننده","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ماده‌ای برای جلوگیری از خشکی پوست"},
        {"category":"اشیا","word":"سشوار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای خشک کردن مو"},
        {"category":"اشیا","word":"اتوی مو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای صاف کردن موی سر"},
        {"category":"اشیا","word":"پادری حمام","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"فرشچه کوچک برای قرار دادن جلوی در حمام"},
        {"category":"اشیا","word":"جاصابونی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ظرفی برای نگهداری صابون"},
        {"category":"اشیا","word":"لیف","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پارچه‌ای زبر برای شستشوی بدن"},
        {"category":"اشیا","word":"کیسه آب گرم","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ظرفی پلاستیکی برای تسکین درد با آب گرم"},
        {"category":"اشیا","word":"پماد سوختگی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کرمی برای بهبود زخم‌های سطحی"},
        {"category":"اشیا","word":"قطره‌چکان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای ریختن مایع به صورت قطره‌ای"},
        {"category":"اشیا","word":"استند گوشی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایه‌ای برای نگه داشتن تلفن همراه روی میز"},
        {"category":"اشیا","word":"منگنه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای اتصال کاغذها به هم"},
        {"category":"اشیا","word":"سوزن منگنه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قطعات فلزی ریز برای دستگاه منگنه"},
        {"category":"اشیا","word":"پانچ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای سوراخ کردن کاغذ"},
        {"category":"اشیا","word":"گیره کاغذ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سیم فلزی خمیده برای موقت نگه داشتن برگه‌ها"},
        {"category":"اشیا","word":"سنجاق قفلی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سنجاقی با محافظ برای اتصال پارچه"},
        {"category":"اشیا","word":"چسب نواری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نواری چسبنده برای چسباندن کاغذ"},
        {"category":"اشیا","word":"پایه چسب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای نگهداری و برش آسان چسب نواری"},
        {"category":"اشیا","word":"خط‌کش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کشیدن خط راست و اندازه‌گیری"},
        {"category":"اشیا","word":"پاک‌کن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قطعه‌ای برای پاک کردن اثر مداد"},
        {"category":"اشیا","word":"تراش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای تیز کردن مداد"},
        {"category":"اشیا","word":"خودکار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار نوشتاری با جوهر روان"},
        {"category":"اشیا","word":"مداد مشکی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار نوشتن با مغز گرافیت"},
        {"category":"اشیا","word":"ماژیک هایلایت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ماژیک با رنگ روشن برای برجسته کردن متن"},
        {"category":"اشیا","word":"زونکن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پوشه بزرگ برای بایگانی اسناد"},
        {"category":"اشیا","word":"کازیه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"طبقه‌ای روی میز برای دسته‌بندی نامه‌ها"},
        {"category":"اشیا","word":"پوشه دکمه‌دار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کیف پلاستیکی کوچک برای حمل مدارک"},
        {"category":"اشیا","word":"کاور کاغذ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نایلون شفاف برای محافظت از برگه"},
        {"category":"اشیا","word":"دفترچه یادداشت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کتابچه کوچک برای نوشتن کارهای روزانه"},
        {"category":"اشیا","word":"تقویم رومیزی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برگه‌های تاریخ‌دار روی پایه برای میز کار"},
        {"category":"اشیا","word":"پد موس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"زیردستی برای حرکت روان‌تر موس کامپیوتر"},
        {"category":"اشیا","word":"فلش مموری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزار کوچک برای ذخیره و انتقال داده"},
        {"category":"اشیا","word":"منگنه کش","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"ابزاری برای بیرون کشیدن سوزن منگنه"},
        {"category":"اشیا","word":"غلط‌گیر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مایعی برای پوشاندن اشتباهات نوشتاری"},
        {"category":"اشیا","word":"مداد نوکی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مدادی که نیازی به تراشیدن ندارد"},
        {"category":"اشیا","word":"مغز مداد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قطعات باریک گرافیت برای مداد نوکی"},
        {"category":"اشیا","word":"منگنه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای اتصال کاغذها به هم"},
        {"category":"اشیا","word":"سوزن منگنه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قطعات فلزی ریز برای دستگاه منگنه"},
        {"category":"اشیا","word":"پانچ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای سوراخ کردن کاغذ"},
        {"category":"اشیا","word":"گیره کاغذ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سیم فلزی خمیده برای موقت نگه داشتن برگه‌ها"},
        {"category":"اشیا","word":"سنجاق قفلی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سنجاقی با محافظ برای اتصال پارچه"},
        {"category":"اشیا","word":"چسب نواری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نواری چسبنده برای چسباندن کاغذ"},
        {"category":"اشیا","word":"پایه چسب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"وسیله‌ای برای نگهداری و برش آسان چسب نواری"},
        {"category":"اشیا","word":"خط‌کش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کشیدن خط راست و اندازه‌گیری"},
        {"category":"اشیا","word":"پاک‌کن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قطعه‌ای برای پاک کردن اثر مداد"},
        {"category":"اشیا","word":"تراش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای تیز کردن مداد"},
        {"category":"اشیا","word":"خودکار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار نوشتاری با جوهر روان"},
        {"category":"اشیا","word":"مداد مشکی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار نوشتن با مغز گرافیت"},
        {"category":"اشیا","word":"ماژیک هایلایت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ماژیک با رنگ روشن برای برجسته کردن متن"},
        {"category":"اشیا","word":"زونکن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پوشه بزرگ برای بایگانی اسناد"},
        {"category":"اشیا","word":"کازیه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"طبقه‌ای روی میز برای دسته‌بندی نامه‌ها"},
        {"category":"اشیا","word":"پوشه دکمه‌دار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کیف پلاستیکی کوچک برای حمل مدارک"},
        {"category":"اشیا","word":"کاور کاغذ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نایلون شفاف برای محافظت از برگه"},
        {"category":"اشیا","word":"دفترچه یادداشت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کتابچه کوچک برای نوشتن کارهای روزانه"},
        {"category":"اشیا","word":"تقویم رومیزی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برگه‌های تاریخ‌دار روی پایه برای میز کار"},
        {"category":"اشیا","word":"پد موس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"زیردستی برای حرکت روان‌تر موس کامپیوتر"},
        {"category":"اشیا","word":"فلش مموری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزار کوچک برای ذخیره و انتقال داده"},
        {"category":"اشیا","word":"منگنه کش","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"ابزاری برای بیرون کشیدن سوزن منگنه"},
        {"category":"اشیا","word":"غلط‌گیر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مایعی برای پوشاندن اشتباهات نوشتاری"},
        {"category":"اشیا","word":"مداد نوکی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مدادی که نیازی به تراشیدن ندارد"},
        {"category":"اشیا","word":"مغز مداد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قطعات باریک گرافیت برای مداد نوکی"},
        {"category":"اشیا","word":"هندزفری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"گوشی‌های کوچک برای شنیدن موسیقی"},
        {"category":"اشیا","word":"شارژر دیواری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"آداپتور برای تبدیل برق به انرژی موبایل"},
        {"category":"اشیا","word":"کابل تبدیل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سیمی برای اتصال دو دستگاه به یکدیگر"},
        {"category":"اشیا","word":"پاوربانک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"باتری قابل حمل برای شارژ گوشی"},
        {"category":"اشیا","word":"موس کامپیوتر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کنترل اشاره‌گر روی مانیتور"},
        {"category":"اشیا","word":"کیبورد","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صفحه‌کلید برای تایپ کردن"},
        {"category":"اشیا","word":"هدفون","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"گوشی‌های بزرگ برای شنیدن صدا"},
        {"category":"اشیا","word":"میکروفون","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ابزاری برای ضبط صدا"},
        {"category":"اشیا","word":"وب‌کم","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"دوربین کوچک برای تماس تصویری"},
        {"category":"اشیا","word":"اسپیکر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای پخش صدای بلند"},
        {"category":"اشیا","word":"تبلت","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"رایانه‌ای باریک و بدون کیبورد"},
        {"category":"اشیا","word":"قلم نوری","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"ابزاری برای طراحی دیجیتال"},
        {"category":"اشیا","word":"هاب یو‌اس‌بی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"دستگاهی برای افزایش پورت‌های کامپیوتر"},
        {"category":"اشیا","word":"فن خنک‌کننده","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای کاهش دمای قطعات دیجیتال"},
        {"category":"اشیا","word":"مودم","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای اتصال به اینترنت"},
        {"category":"اشیا","word":"روتر","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"وسیله‌ای برای توزیع اینترنت بی‌سیم"},
        {"category":"اشیا","word":"کارت حافظه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"قطعه‌ای کوچک برای ذخیره اطلاعات دوربین یا موبایل"},
        {"category":"اشیا","word":"هارد اکسترنال","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"حافظه جانبی بزرگ برای ذخیره فایل‌ها"},
        {"category":"اشیا","word":"چراغ مطالعه یو‌اس‌بی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"چراغ کوچکی که برق خود را از پورت کامپیوتر می‌گیرد"},
        {"category":"اشیا","word":"محافظ برق","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"چندراهی برای جلوگیری از آسیب نوسانات برق"},
        {"category":"اشیا","word":"باتری قلمی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"منبع انرژی استوانه‌ای برای وسایل الکترونیک"},
        {"category":"اشیا","word":"باتری نیم‌قلمی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"منبع انرژی استوانه‌ای کوچک"},
        {"category":"اشیا","word":"ریموت کنترل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای کنترل از راه دور تلویزیون"},
        {"category":"اشیا","word":"کیس کامپیوتر","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"جعبه اصلی نگهداری قطعات رایانه"},
        {"category":"اشیا","word":"مانیتور","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صفحه‌نمایش برای دیدن تصاویر کامپیوتر"},  {"category":"اشیا","word":"هندزفری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"گوشی‌های کوچک برای شنیدن موسیقی"},
        {"category":"اشیا","word":"شارژر دیواری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"آداپتور برای تبدیل برق به انرژی موبایل"},
        {"category":"اشیا","word":"کابل تبدیل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سیمی برای اتصال دو دستگاه به یکدیگر"},
        {"category":"اشیا","word":"پاوربانک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"باتری قابل حمل برای شارژ گوشی"},
        {"category":"اشیا","word":"موس کامپیوتر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزاری برای کنترل اشاره‌گر روی مانیتور"},
        {"category":"اشیا","word":"کیبورد","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صفحه‌کلید برای تایپ کردن"},
        {"category":"اشیا","word":"هدفون","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"گوشی‌های بزرگ برای شنیدن صدا"},
        {"category":"اشیا","word":"میکروفون","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ابزاری برای ضبط صدا"},
        {"category":"اشیا","word":"وب‌کم","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"دوربین کوچک برای تماس تصویری"},
        {"category":"اشیا","word":"اسپیکر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای پخش صدای بلند"},
        {"category":"اشیا","word":"تبلت","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"رایانه‌ای باریک و بدون کیبورد"},
        {"category":"اشیا","word":"قلم نوری","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"ابزاری برای طراحی دیجیتال"},
        {"category":"اشیا","word":"هاب یو‌اس‌بی","difficulty":2,"rarity":3,"points":14,"synonyms":"","clue":"دستگاهی برای افزایش پورت‌های کامپیوتر"},
        {"category":"اشیا","word":"فن خنک‌کننده","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"وسیله‌ای برای کاهش دمای قطعات دیجیتال"},
        {"category":"اشیا","word":"مودم","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دستگاهی برای اتصال به اینترنت"},
        {"category":"اشیا","word":"روتر","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"وسیله‌ای برای توزیع اینترنت بی‌سیم"},
        {"category":"اشیا","word":"کارت حافظه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"قطعه‌ای کوچک برای ذخیره اطلاعات دوربین یا موبایل"},
        {"category":"اشیا","word":"هارد اکسترنال","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"حافظه جانبی بزرگ برای ذخیره فایل‌ها"},
        {"category":"اشیا","word":"چراغ مطالعه یو‌اس‌بی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"چراغ کوچکی که برق خود را از پورت کامپیوتر می‌گیرد"},
        {"category":"اشیا","word":"محافظ برق","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"چندراهی برای جلوگیری از آسیب نوسانات برق"},
        {"category":"اشیا","word":"باتری قلمی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"منبع انرژی استوانه‌ای برای وسایل الکترونیک"},
        {"category":"اشیا","word":"باتری نیم‌قلمی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"منبع انرژی استوانه‌ای کوچک"},
        {"category":"اشیا","word":"ریموت کنترل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزاری برای کنترل از راه دور تلویزیون"},
        {"category":"اشیا","word":"کیس کامپیوتر","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"جعبه اصلی نگهداری قطعات رایانه"},
        {"category":"اشیا","word":"مانیتور","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صفحه‌نمایش برای دیدن تصاویر کامپیوتر"},
        {"category":"اشیا","word":"گیتار","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ساز زهی که با انگشت یا پیک نواخته می‌شود"},
        {"category":"اشیا","word":"ویولن","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ساز زهی که با آرشه نواخته می‌شود"},
        {"category":"اشیا","word":"پیانو","difficulty":3,"rarity":4,"points":16,"synonyms":"","clue":"ساز کلیدی بزرگ با صدای دلنشین"},
        {"category":"اشیا","word":"فلوت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ساز بادی چوبی یا فلزی"},
        {"category":"اشیا","word":"سنتور","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ساز زهی ایرانی که با مضراب نواخته می‌شود"},
        {"category":"اشیا","word":"سه‌تار","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ساز زهی ایرانی با کاسه کوچک"},
        {"category":"اشیا","word":"درامز","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"مجموعه‌ای از طبل‌ها برای ریتم"},
        {"category":"اشیا","word":"ترمپت","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ساز بادی برنجی"},
        {"category":"اشیا","word":"ساکسیفون","difficulty":3,"rarity":3,"points":14,"synonyms":"","clue":"ساز بادی خوش‌صدا در سبک جاز"},
        {"category":"اشیا","word":"هارمونیکا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ساز دهنی کوچک"},
        {"category":"اشیا","word":"بوم نقاشی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پارچه کشیده شده روی قاب چوبی برای نقاشی"},
        {"category":"اشیا","word":"قلم‌مو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار اصلی برای پخش رنگ روی بوم"},
        {"category":"اشیا","word":"رنگ روغن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"رنگ‌های غلیظ مخصوص نقاشی هنری"},
        {"category":"اشیا","word":"آبرنگ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ‌های شفاف که با آب ترکیب می‌شوند"},
        {"category":"اشیا","word":"پالت رنگ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"صفحه‌ای برای مخلوط کردن رنگ‌ها"},
        {"category":"اشیا","word":"مداد رنگی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار رنگ‌آمیزی با مغز رنگی"},
        {"category":"اشیا","word":"پاستل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مدادهای رنگی نرم و پودری"},
        {"category":"اشیا","word":"کاردک نقاشی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزار فلزی برای گذاشتن رنگ ضخیم روی بوم"},
        {"category":"اشیا","word":"پیش‌بند نقاشی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پوشش محافظ برای جلوگیری از لک شدن لباس"},
        {"category":"اشیا","word":"سه پایه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایه‌ای برای نگه داشتن بوم در ارتفاع مناسب"},    ]

    for item in words:
        if add_word(
            item["category"],
            item["word"],
            item["difficulty"],
            item["rarity"],
            item["points"],
            item["synonyms"],
            item["clue"],
        ):
            added += 1

    print(f"Added {added} words.")


if __name__ == "__main__":
    import_words()
```


================================================================================
FILE: story\story.py
================================================================================

```py

```


================================================================================
FILE: tex.py
================================================================================

```py
from pathlib import Path

# مسیر پروژه
PROJECT_DIR = Path(r"C:\Users\Nima\Desktop\kalemo")  # ← تغییر بده

OUTPUT_FILE = "project_dump.md"

# پسوندهایی که می‌خواهیم
INCLUDE_EXTENSIONS = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".sql",
    ".html",
    ".css",
    ".js",
    ".xml",
    ".csv"
}

# پوشه‌هایی که نباید بررسی شوند
EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "venv",
    ".venv",
    "env",
    "node_modules",
    "dist",
    "build"
}

with open(OUTPUT_FILE, "w", encoding="utf-8") as out:

    out.write("# Project Dump\n\n")

    for file in sorted(PROJECT_DIR.rglob("*")):

        if not file.is_file():
            continue

        if any(part in EXCLUDE_DIRS for part in file.parts):
            continue

        if file.suffix.lower() not in INCLUDE_EXTENSIONS:
            continue

        relative = file.relative_to(PROJECT_DIR)

        out.write("\n")
        out.write("=" * 80 + "\n")
        out.write(f"FILE: {relative}\n")
        out.write("=" * 80 + "\n\n")

        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = file.read_text(encoding="utf-8-sig")
            except:
                text = file.read_text(errors="ignore")

        out.write("```")
        out.write(file.suffix[1:] if file.suffix else "text")
        out.write("\n")
        out.write(text)
        out.write("\n```\n\n")

print("Done!")
print(f"Saved to: {OUTPUT_FILE}")
```


================================================================================
FILE: tools\import_words.py
================================================================================

```py
"""ابزار Import واژگان از JSON یا CSV به دیتابیس کلمو.

استفاده:
    python -m tools.import_words words.json
    python -m tools.import_words words.csv

فرمت JSON: لیستی از آبجکت‌ها:
    [{"word":"سیب","category":"میوه","difficulty":1,"rarity":1,
      "points":10,"synonyms":"","clue":""}, ...]

فرمت CSV: سطر اول هدر با ستون‌های word,category و اختیاری
    difficulty,rarity,points,synonyms,clue
فقط word و category الزامی‌اند؛ بقیه پیش‌فرض دارند.
"""
import sys, os, json, csv

def load_json(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):  # {"category":[words...]}
        recs = []
        for cat, words in data.items():
            for w in words:
                recs.append({"word": w, "category": cat})
        return recs
    return data

def load_csv(path):
    recs = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            recs.append(row)
    return recs

def main(argv):
    if len(argv) < 2:
        print("usage: python -m tools.import_words <file.json|file.csv>")
        return 1
    path = argv[1]
    if not os.path.exists(path):
        print("file not found:", path); return 1
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from core import db
    db.init()
    recs = load_json(path) if path.lower().endswith(".json") else load_csv(path)
    added, skipped = db.import_words(recs)
    print(f"✅ added: {added} | skipped (duplicate/invalid): {skipped}")
    print("categories now:", db.list_categories())
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```


================================================================================
FILE: ui\__init__.py
================================================================================

```py

```


================================================================================
FILE: ui\cards.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""کارت‌های گرافیکی متنی (HTML) برای تلگرام."""
DIV = "━━━━━━━━━━━━━━"

def _bar(value, maximum, width=10):
    if maximum <= 0:
        maximum = 1
    filled = int(round(width * min(value, maximum) / maximum))
    return "▰" * filled + "▱" * (width - filled)

def profile_card(p):
    pct_bar = _bar(p["xp"], p["xp_needed"])
    winrate = int(100 * p["wins"] / p["games"]) if p["games"] else 0
    return (
        f"<b>🪪 پروفایل {p['name']}</b>\n{DIV}\n"
        f"🏅 <b>سطح {p['level']}</b>\n"
        f"⚡️ XP: {pct_bar}  <code>{p['xp']}/{p['xp_needed']}</code>\n{DIV}\n"
        f"🪙 سکه: <b>{p['coins']:,}</b>\n"
        f"🔥 استریک: <b>{p['streak']} روز</b>\n{DIV}\n"
        f"🎮 بازی‌ها: <b>{p['games']}</b>\n"
        f"🏆 بردها: <b>{p['wins']}</b>  (<b>{winrate}%</b>)\n"
        f"⭐️ بهترین امتیاز: <b>{p['best']}</b>"
    )

def levelup_card(level, reward_coins):
    return (
        f"<b>🎚 لِوِل آپ!</b>\n{DIV}\n"
        f"رسیدی به <b>سطح {level}</b> 🎉\n"
        f"جایزه: <b>+{reward_coins} سکه</b> 🪙\n{DIV}\n"
        f"<i>یه پله بالاتر، یه ذره خفن‌تر 🔝</i>"
    )

def game_over_card(title, rows, ad=None):
    lines = "\n".join(f"{r[0]} <b>{r[1]}</b> — {r[2]} امتیاز" for r in rows)
    card = f"<b>🏁 {title}</b>\n{DIV}\n{lines}\n{DIV}"
    if ad:
        card += f"\n\n📣 <i>{ad}</i>"
    return card

def leaderboard_card(title, rows):
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, score) in enumerate(rows):
        badge = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{badge} <b>{name}</b> — {score}")
    return f"<b>🏆 {title}</b>\n{DIV}\n" + ("\n".join(lines) if lines else "هنوز کسی نیست!")

def daily_card(coins, streak, mission_text):
    return (
        f"<b>🎁 جایزه روزانه</b>\n{DIV}\n"
        f"🪙 <b>+{coins} سکه</b>\n"
        f"🔥 استریک: <b>{streak} روز</b>\n{DIV}\n"
        f"<b>🎯 مأموریت امروز:</b>\n{mission_text}"
    )

```


================================================================================
FILE: ui\keyboards.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""کیبوردهای اینلاین یکپارچه Kalemo."""
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
import config


def main_menu():
    return M([
        [B("🎮 ایجاد بازی", callback_data="m:play")],
        [B("🌳 باغچه", callback_data="m:garden"),
         B("💡 پیشنهاد کلمه", callback_data="m:suggest")],
        [B("👤 پروفایل", callback_data="m:profile"),
         B("🎯 ماموریت روزانه", callback_data="m:mission")],
        [B("🏆 لیدربورد", callback_data="m:lb"),
         B("🎁 جایزه روزانه", callback_data="m:daily")],
        [B("⚙ تنظیمات", callback_data="m:settings"),
         B("❓ راهنما", callback_data="m:help")],
    ])

def back_menu():
    return M([[B("🔙 منوی اصلی", callback_data="m:home")]])


def settings_menu():
    return M([
        [B("✏️ تغییر نام نمایشی", callback_data="m:rename")],
        [B("🔙 منوی اصلی", callback_data="m:home")],
    ])


def profile_menu():
    return M([
        [B("✏️ تغییر نام نمایشی", callback_data="m:rename")],
        [B("🔙 منوی اصلی", callback_data="m:home")],
    ])


def onboarding(step):
    if step < 3:
        return M([[B("بعدی ➡️", callback_data=f"ob:{step+1}")]])
    return M([[B("🎮 انتخاب نام و شروع", callback_data="ob:name")]])


def cancel_rename():
    return M([[B("🔙 بی‌خیال", callback_data="m:home")]])


def mission_claim(can_claim):
    rows = []
    if can_claim:
        rows.append([B("🎁 دریافت جایزه", callback_data="mission:claim")])
    rows.append([B("🔙 منوی اصلی", callback_data="m:home")])
    return M(rows)


def play_in_group():
    url = f"https://t.me/{config.BOT_USERNAME}?startgroup=true"
    return M([[B("➕ افزودن کلمو به گروه", url=url)],
             [B("🔙 منوی اصلی", callback_data="m:home")]])


# ---- پنل ادمین ----
def admin_panel():
    return M([
        [B("📊 آمار کلی", callback_data="a:stats")],
        [B("🪙 دادن سکه/XP", callback_data="a:give"),
         B("🔎 پروفایل کاربر", callback_data="a:find")],
        [B("📣 پیام همگانی", callback_data="a:bcast")],
        [B("🗂 مدیریت دسته/کلمه", callback_data="a:words")],
        [B("👥 ادمین‌های همکار", callback_data="a:admins")],
        [B("❌ بستن", callback_data="a:close")],
    ])


def admin_back():
    return M([[B("🔙 پنل ادمین", callback_data="a:home")]])


def admin_words_menu():
    return M([
        [B("📋 لیست دسته‌ها", callback_data="a:wlist")],
        [B("💡 پیشنهادها", callback_data="a:suggests")],
        [B("🔙 پنل ادمین", callback_data="a:home")],
    ])
def suggest_menu():
    return M([
        [B("💡 ثبت پیشنهاد", callback_data="m:suggest")],
        [B("🔙 منوی اصلی", callback_data="m:home")],
    ])
def admin_suggestion_kb(sid):
    return M([
        [
            B("✅ تایید", callback_data=f"a:sapp:{sid}"),
            B("❌ رد", callback_data=f"a:srej:{sid}")
        ],
        [
            B("✏️ ویرایش", callback_data=f"a:sedit:{sid}"),
            B("📂 تغییر دسته", callback_data=f"a:scat:{sid}")
        ],
        [
            B("➡ بعدی", callback_data="a:suggests"),
            B("🔙 پنل ادمین", callback_data="a:home")
        ],
    ])
```


================================================================================
FILE: ui\onboarding.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""آنبوردینگ تعاملی برای کاربر جدید."""
STEPS = {
    1: ("<b>👋 سلام! من کلمو‌ام</b>\n━━━━━━━━━━━━━━\n"
        "یه بازی کلمه‌ایِ گروهی که <b>هیچ‌وقت تکراری نمی‌شه</b>!\n"
        "من وسط بازی قانونا رو عوض می‌کنم تا حواست جمع بمونه 😏"),
    2: ("<b>🎮 بازی چطوریه؟</b>\n━━━━━━━━━━━━━━\n"
        "پنج مود داریم: کلاسیک، جای خالی، اسم‌وفامیل، قوانین متغیر و سرنخ.\n"
        "تو گروه «شروع کلمو» بنویس، دوستاتو دعوت کن و بترکونید! 🔥"),
    3: ("<b>🏅 چی گیرت میاد؟</b>\n━━━━━━━━━━━━━━\n"
        "🪙 سکه، ⚡️ XP، 🔥 استریک روزانه و رتبه تو لیدربورد!\n"
        "آخرین قدم: یه نام نمایشی برای خودت انتخاب کن 👇"),
}


def step_text(n):
    return STEPS.get(n, STEPS[3])

```


================================================================================
FILE: ui\panels.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""پنل‌های متنی + کیبوردهای بازی گروهی کلمو (همه با EditMessage)."""
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
from game.modes import MODE_ORDER, mode_meta
from game.rules import REGISTRY
from game.session import TIME_OPTIONS, DIFFICULTY_OPTIONS, time_label, difficulty_label

DIV = "────────────────"


# ---- پنل اصلی لابی ----
def lobby_text(s):
    meta = mode_meta(s.mode_id)
    ready = "آماده‌ی شروع ✅" if s.count() >= 2 else "منتظر بازیکن… (حداقل ۲ نفر)"
    return (
        "🎮 <b>ایجاد مسابقه کلمو</b>\n"
        f"{DIV}\n"
        f"🎲 مود: <b>{meta['emoji']} {meta['name']}</b>\n"
        f"<i>{meta['desc']}</i>\n\n"
        f"⏱ زمان: <b>{time_label(s.time_limit)}</b>\n"
        f"👑 سازنده: <b>{s.host_name}</b>\n\n"
        f"👥 بازیکنان: <b>{s.count()} نفر</b>\n"
        f"{s.player_lines()}\n\n"
        f"📜 قوانین:\n{s.ruleset.describe()}\n"
        f"{DIV}\n"
        f"وضعیت: <b>{ready}</b>"
    )


def lobby_kb(s):
    return M([
        [B("🎲 انتخاب مود", callback_data="k:mode"),
         B("⏱ زمان", callback_data="k:time")],
        [B("⚙ قوانین", callback_data="k:rules")],
        [B(f"👥 عضویت ({s.count()})", callback_data="k:join")],
        [B("▶ شروع بازی", callback_data="k:start"),
         B("❌ لغو", callback_data="k:cancel")],
    ])


# ---- انتخاب مود ----
def mode_text():
    return "🎲 <b>انتخاب مود بازی</b>\n" + DIV + "\nیکی رو انتخاب کن:"


def mode_kb(current):
    rows = []
    for mid in MODE_ORDER:
        meta = mode_meta(mid)
        mark = "◉" if mid == current else "◯"
        rows.append([B(f"{mark} {meta['emoji']} {meta['name']}",
                       callback_data=f"k:setmode:{mid}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- انتخاب زمان ----
def time_text(s):
    return ("⏱ <b>زمان مسابقه</b>\n" + DIV +
            "\nمسابقه بعد از پایان این زمان خودکار تموم می‌شه.")


def time_kb(s):
    rows = []
    for sec, lbl in TIME_OPTIONS:
        mark = "◉" if sec == s.time_limit else "◯"
        rows.append([B(f"{mark} {lbl}", callback_data=f"k:settime:{sec}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- سختی (جای خالی) ----
def difficulty_kb(s):
    rows = []
    for key, lbl in DIFFICULTY_OPTIONS:
        mark = "◉" if key == s.difficulty else "◯"
        rows.append([B(f"{mark} {lbl}", callback_data=f"k:setdiff:{key}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- قوانین ----
TOGGLEABLE = ["min_len", "max_len", "exact_len", "starts_with", "ends_with",
              "must_contain", "must_not_contain", "time_limit", "bonus"]


def rules_text():
    return ("⚙ <b>قوانین بازی</b>\n" + DIV +
            "\nقوانین دلخواه رو روشن/خاموش کن.\n"
            "<i>(در مود «قوانین متغیر» قوانین هر دور خودکار انتخاب می‌شن.)</i>")


def rules_kb(s):
    rows = []
    for rid in TOGGLEABLE:
        cls = REGISTRY[rid]
        on = s.ruleset.is_active(rid)
        mark = "✅" if on else "▫️"
        rows.append([B(f"{mark} {cls.label}", callback_data=f"k:toggle:{rid}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- نمایش زنده‌ی بازی ----
def live_text(s):
    meta = mode_meta(s.mode_id)
    leader = s.leader()
    leader_line = (f"🥇 صدرنشین: <b>{leader[0]}</b> — {leader[1]} امتیاز"
                   if leader else "🥇 صدرنشین: —")
    cat_line = (
        f"📂 دسته: <b>{s.category}</b>\n"
        if s.category and s.mode_id in ("classic_random", "classic_choice", "variable")
        else ""
    )

    rules_line = ""
    if s.ruleset.rules and s.mode_id in (
        "classic_random",
        "classic_choice",
        "blank",
    ):

        rules_line = f"📜 قوانین:\n{s.ruleset.describe()}\n"
    return (
        f"🎮 <b>مسابقه‌ی کلمو — {meta['emoji']} {meta['name']}</b>\n"
        f"{DIV}\n"
        f"{cat_line}{rules_line}"
        f"👥 بازیکنان: <b>{s.count()}</b>\n"
        f"⏱ باقی‌مانده: <b>{s.remaining_label()}</b>\n"
        f"{leader_line}\n"
        f"{DIV}\n"
        f"<b>{prompt_of(s)}</b>"
    )


def prompt_of(s):
    if s.question and "prompt" in s.question:
        return s.question["prompt"]
    return "در حال آماده‌سازی…"


def running_kb(s=None):
    return M([[B("🏁 پایان مسابقه", callback_data="k:end")]])


# ---- تنظیمات گروه (حالت تمرکز) ----
def settings_text(s):
    state = "روشن ✅" if s.focus_mode else "خاموش ▫️"
    return ("🧹 <b>تنظیمات مسابقه</b>\n" + DIV +
            f"\nحالت تمرکز: <b>{state}</b>\n"
            "<i>وقتی روشنه، پیام‌های نامرتبط هنگام مسابقه پاک می‌شن.</i>")


def settings_kb(s):
    label = "🧹 خاموش‌کردن حالت تمرکز" if s.focus_mode else "🧹 روشن‌کردن حالت تمرکز"
    return M([[B(label, callback_data="k:focus")],
             [B("🔙 برگشت", callback_data="k:back")]])


def category_text():
    return (
        "📂 <b>انتخاب دسته</b>\n"
        + DIV +
        "\nیک دسته برای مسابقه انتخاب کن."
    )

def category_kb(categories, current=None, page=0, per_page=8):
    total = len(categories)
    start = page * per_page
    shown = categories[start:start + per_page]

    rows = []

    for cat, cnt in shown:
        mark = "◉" if cat == current else "◯"
        rows.append([
            B(
                f"{mark} {cat} ({cnt})",
                callback_data=f"k:setcat:{page}:{cat}"
            )
        ])

    nav = []

    if page > 0:
        nav.append(
            B("⬅ قبلی", callback_data=f"k:catpage:{page-1}")
        )

    if start + per_page < total:
        nav.append(
            B("بعدی ➡", callback_data=f"k:catpage:{page+1}")
        )

    if nav:
        rows.append(nav)

    rows.append([
        B("🔙 برگشت", callback_data="k:back")
    ])

    return M(rows)

def answer_ok_text(score, found, total):
    total = max(1, int(total or 0))
    found = max(0, min(int(found or 0), total))

    pct = round(found * 100 / total)

    filled = round(pct / 100 * 16)

    bar = (
        "█" * filled +
        "░" * (16 - filled)
    )

    return (
        f"✅ درست\n"
        f"⭐ امتیاز: {score}\n\n"
        f"{bar} {pct}%\n\n"
        f"{found} / {total}"
    )


def finish_text(s, reason=None):
    import html
    import time

    ranking = s.ranking()

    duration = 0
    if s.started_at:
        duration = max(
            0,
            int(time.time() - s.started_at)
        )

    m, sec = divmod(duration, 60)

    lines = [
        "🏆 <b>رتبه‌بندی</b>",
        DIV,
    ]

    if not ranking:
        lines.append("امتیازی ثبت نشد.")

    for i, (uid, info) in enumerate(ranking, 1):
        name = html.escape(
            info.get("name", "بازیکن")
        )

        score = int(info.get("score", 0))
        ok = int(s.correct_by_user.get(uid, 0))
        bad = int(s.wrong_by_user.get(uid, 0))

        lines.append(f"{i}. <b>{name}</b>")
        lines.append(f"⭐ امتیاز: {score}")
        lines.append(f"📊 درست: {ok}   ❌ اشتباه: {bad}")

    lines += [
        DIV,
        f"⏱ مدت مسابقه: <b>{m:02d}:{sec:02d}</b>",
        f"📂 دسته مسابقه: <b>{html.escape(s.category or '—')}</b>",
    ]

    return "\n".join(lines)
```


================================================================================
FILE: ui\persona.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""شخصیت Kalemo (کلمو). همه متن‌ها از اینجا می‌آیند تا لحن یکپارچه بماند."""
import random

NAME = "کلمو"
LINES = {
    "welcome": [
        "سلام رفیق! 👋 من {name}‌ام، استاد بازی‌های کلمه‌ای.\n",
        "به‌به! یه بازیکن تازه‌نفس 🔥 من {name}‌ام؛ بزن بریم ببینم چند مرده حلاجی!",
    ],
    "win": [
        "🏆 ایوللل! بردی رفیق! مغزت داره دود می‌کنه از بس تیزه 🔥",
        "🏆 برنده شدی! اعتراف می‌کنم، ازت انتظار نداشتم انقدر بترکونی 😎",
        "🏆 قهرمااان! اسمتو با خط درشت می‌نویسم رو تابلوی افتخار ✨",
    ],
    "lose": [
        "😅 این دور رو باختی، ولی بین خودمون بمونه... نزدیک بود! دوباره؟",
        "💔 آخ! این یکی نشد. ولی قهرمان واقعی کسیه که پا می‌شه. یالا یه دور دیگه!",
        "🙃 باختی، ولی من بهت ایمان دارم. دفعه بعد جبران می‌کنی، مطمئنم.",
    ],
    "record": [
        "🚀 رکورد جدیییید! این بهترین اجرای تاریخته! غوغا کردی 🎉",
        "🌟 رکوردتو شکستی! انگار امروز روز توئه. حالا حالاها کسی بهت نمی‌رسه!",
    ],
    "levelup": [
        "🎚 لِوِل آپ! رسیدی به سطح {level}! داری حرفه‌ای می‌شی ها 😍",
        "✨ سطح {level} باز شد! یه پله بالاتر، یه ذره خفن‌تر 🔝",
    ],
    "daily_login": [
        "🎁 خوش اومدی! جایزه ورود امروزت: {coins} سکه 🪙\nاستریکت شد {streak} روز! 🔥",
        "☀️ سلام به روی ماهت! {coins} سکه گرفتی و {streak} روزه که پیداتو می‌کنی 🔥",
    ],
    "streak_break": ["😢 آخی، استریکت پاره شد! اشکال نداره، از امروز دوباره شروع می‌کنیم 💪"],
    "mission_done": [
        "✅ مأموریت انجام شد! بیا جایزتو بگیر: {reward} 🎉",
        "🎯 ماموریت تیک خورد! {reward} مال خودت. کارت درسته!",
    ],
    "error": [
        "🤖 اوپس! یه چیزی قاطی شد. دوباره امتحان کن رفیق.",
        "😬 یه لحظه قاطی کردم! یه بار دیگه بزن لطفاً.",
    ],
    "game_start": [
        "🎬 بازی شروع شد! کمربندا رو ببندین 🔥",
        "🚦 سه، دو، یک... بریییم! 🏁",
    ],
    "game_end": ["🎬 و... تمام! دمتون گرم، ترکوندین 👏"],
    "timeout": [
        "⏰ وقت تموم شد! این دفعه سریع‌تر، باشه؟ 😉",
        "⏰ زمان پرید رفت! اشکال نداره، دور بعد جبران کن.",
    ],
    "good_answer": [
        "✅ دمت گرم! +{pts}",
        "✅ آفرین! دقیقاً همینو می‌خواستم. +{pts}",
        "✅ نــایس! +{pts} 🔥",
    ],
}

def say(key, **kwargs):
    options = LINES.get(key, LINES["error"])
    text = random.choice(options)
    kwargs.setdefault("name", NAME)
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError):
        return text

```


================================================================================
FILE: web.py
================================================================================

```py
from flask import Flask
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "Kalemo Bot is alive!", 200

def run():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
```


```


================================================================================
FILE: README.md
================================================================================

```md
# Kalemo Telegram Game Bot

run: set env vars then `python main.py`

```


================================================================================
FILE: requirements.txt
================================================================================

```txt
python-telegram-bot==21.7
python-dotenv==1.0.1
httpx==0.27.2
flask
psutil
psycopg[binary]==3.2.3
psycopg-pool==3.2.4

```


================================================================================
FILE: runtime.txt
================================================================================

```txt
python-3.12.7
```


================================================================================
FILE: seeds.py
================================================================================

```py
# -*- coding: utf-8 -*-

from core.db import add_word
import os

if os.getenv("RUN_SEEDS") != "true":
    print("Seeds skipped")
    exit()

def import_words():
    added = 0
    words = [
        # ===== کشور =====
        {"category":"کشور","word":"ایران","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری در خاورمیانه با پایتخت تهران"},
        {"category":"کشور","word":"ترکیه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که بخشی از آن در اروپا و بخشی در آسیاست"},
        {"category":"کشور","word":"عراق","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشور همسایه غربی ایران"},
        {"category":"کشور","word":"افغانستان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشور همسایه شرقی ایران"},
        {"category":"کشور","word":"پاکستان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری در جنوب شرقی ایران"},
        {"category":"کشور","word":"آلمان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"یکی از قدرتمندترین کشورهای اروپا"},
        {"category":"کشور","word":"فرانسه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که برج ایفل در آن قرار دارد"},
        {"category":"کشور","word":"ایتالیا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری چکمه‌ای شکل در اروپا"},
        {"category":"کشور","word":"ژاپن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری جزیره‌ای در شرق آسیا"},
        {"category":"کشور","word":"چین","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پرجمعیت‌ترین کشور جهان"},
        {"category":"کشور","word":"هند","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری با تاج محل"},
        {"category":"کشور","word":"روسیه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بزرگ‌ترین کشور جهان از نظر مساحت"},
        {"category":"کشور","word":"کانادا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دومین کشور بزرگ جهان"},
        {"category":"کشور","word":"برزیل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بزرگ‌ترین کشور آمریکای جنوبی"},
        {"category":"کشور","word":"آرژانتین","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری که بوئنوس آیرس پایتخت آن است"},
        {"category":"کشور","word":"استرالیا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که هم‌زمان یک قاره نیز هست"},
        {"category":"کشور","word":"مصر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری با اهرام ثلاثه"},
        {"category":"کشور","word":"عربستان","difficulty":1,"rarity":1,"points":10,"synonyms":"عربستان سعودی","clue":"کشوری که مکه و مدینه در آن قرار دارند"},
        {"category":"کشور","word":"قطر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"میزبان جام جهانی فوتبال ۲۰۲۲"},
        {"category":"کشور","word":"امارات","difficulty":2,"rarity":2,"points":12,"synonyms":"امارات متحده عربی","clue":"کشوری که دبی در آن قرار دارد"},

        # ===== شهر =====
        {"category":"شهر","word":"تهران","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت ایران"},
        {"category":"شهر","word":"مشهد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهر حرم امام رضا (ع)"},
        {"category":"شهر","word":"اصفهان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهری که میدان نقش جهان در آن قرار دارد"},
        {"category":"شهر","word":"شیراز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهر حافظ و سعدی"},
        {"category":"شهر","word":"تبریز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان آذربایجان شرقی"},
        {"category":"شهر","word":"کرمان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"یکی از بزرگ‌ترین استان‌های ایران به همین نام"},
        {"category":"شهر","word":"رشت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان گیلان"},
        {"category":"شهر","word":"ساری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان مازندران"},
        {"category":"شهر","word":"اهواز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان خوزستان"},
        {"category":"شهر","word":"یزد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهر بادگیرها"},
        {"category":"شهر","word":"رم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت ایتالیا"},
        {"category":"شهر","word":"پاریس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت فرانسه"},
        {"category":"شهر","word":"لندن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت بریتانیا"},
        {"category":"شهر","word":"برلین","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت آلمان"},
        {"category":"شهر","word":"توکیو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت ژاپن"},
        {"category":"شهر","word":"پکن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت چین"},
        {"category":"شهر","word":"مسکو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت روسیه"},
        {"category":"شهر","word":"دبی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مشهورترین شهر امارات"},
        {"category":"شهر","word":"نیویورک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"یکی از مشهورترین شهرهای آمریکا"},
        {"category":"شهر","word":"استانبول","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بزرگ‌ترین شهر ترکیه"},{"category":"شهر","word":"قم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"یکی از مهم‌ترین شهرهای مذهبی ایران"},
        {"category":"شهر","word":"کرج","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان البرز"},
        {"category":"شهر","word":"ارومیه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان آذربایجان غربی"},
        {"category":"شهر","word":"زنجان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استانی با همین نام"},
        {"category":"شهر","word":"سنندج","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان کردستان"},
        {"category":"شهر","word":"همدان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"یکی از قدیمی‌ترین شهرهای ایران"},
        {"category":"شهر","word":"قزوین","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مدتی پایتخت ایران در دوره صفوی"},
        {"category":"شهر","word":"گرگان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان گلستان"},
        {"category":"شهر","word":"بندرعباس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بندر مهم جنوب ایران"},
        {"category":"شهر","word":"بوشهر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان بوشهر"},
        {"category":"شهر","word":"خرم‌آباد","difficulty":2,"rarity":2,"points":12,"synonyms":"خرم آباد","clue":"مرکز استان لرستان"},
        {"category":"شهر","word":"شهرکرد","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مرکز استان چهارمحال و بختیاری"},
        {"category":"شهر","word":"بیرجند","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مرکز استان خراسان جنوبی"},
        {"category":"شهر","word":"بجنورد","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مرکز استان خراسان شمالی"},
        {"category":"شهر","word":"زاهدان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان سیستان و بلوچستان"},
        {"category":"شهر","word":"ایلام","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان ایلام"},
        {"category":"شهر","word":"یاسوج","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مرکز استان کهگیلویه و بویراحمد"},
        {"category":"شهر","word":"اردبیل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان اردبیل"},
        {"category":"شهر","word":"سمنان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان سمنان"},
        {"category":"شهر","word":"کاشان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهری مشهور به خانه‌های تاریخی و گلاب"},
        {"category":"شهر","word":"نیشابور","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"زادگاه خیام"},
        {"category":"شهر","word":"سبزوار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"از شهرهای مهم خراسان رضوی"},
        {"category":"شهر","word":"جهرم","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"از شهرهای استان فارس"},
        {"category":"شهر","word":"مراغه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری با رصدخانه تاریخی"},
        {"category":"شهر","word":"مرند","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"از شهرهای آذربایجان شرقی"},
        {"category":"شهر","word":"بوکان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"از شهرهای آذربایجان غربی"},
        {"category":"شهر","word":"مهاباد","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در جنوب آذربایجان غربی"},
        {"category":"شهر","word":"ساوه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در استان مرکزی"},
        {"category":"شهر","word":"رفسنجان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری معروف به پسته"},
        {"category":"شهر","word":"سیرجان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"از شهرهای مهم استان کرمان"},
        {"category":"شهر","word":"بم","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری با ارگ تاریخی"},
        {"category":"شهر","word":"کیش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"جزیره گردشگری ایران"},
        {"category":"شهر","word":"قشم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بزرگ‌ترین جزیره ایران"},
        {"category":"شهر","word":"دوحه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت قطر"},
        {"category":"شهر","word":"ابوظبی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت امارات"},
        {"category":"شهر","word":"ریاض","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت عربستان"},
        {"category":"شهر","word":"بغداد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت عراق"},
        {"category":"شهر","word":"کابل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت افغانستان"},
        {"category":"شهر","word":"اسلام‌آباد","difficulty":2,"rarity":2,"points":12,"synonyms":"اسلام آباد","clue":"پایتخت پاکستان"},
        {"category":"شهر","word":"سئول","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت کره جنوبی"},
        {"category":"شهر","word":"بانکوک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت تایلند"},
        {"category":"شهر","word":"سنگاپور","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت و تنها شهر کشور سنگاپور"},
        {"category":"شهر","word":"مادرید","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت اسپانیا"},
        {"category":"شهر","word":"لیسبون","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت پرتغال"},
        {"category":"شهر","word":"آتن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت یونان"},
        {"category":"شهر","word":"وین","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت اتریش"},
        {"category":"شهر","word":"پراگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت جمهوری چک"},
        {"category":"شهر","word":"بوداپست","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت مجارستان"},
        {"category":"شهر","word":"ورشو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت لهستان"},
        {"category":"شهر","word":"کی‌یف","difficulty":2,"rarity":2,"points":12,"synonyms":"کیف","clue":"پایتخت اوکراین"},
        {"category":"شهر","word":"استکهلم","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت سوئد"},
        {"category":"شهر","word":"اسلو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت نروژ"},
        {"category":"شهر","word":"کپنهاگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت دانمارک"},
        {"category":"شهر","word":"هلسینکی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت فنلاند"},
        {"category":"شهر","word":"دوبلین","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت ایرلند"},
        {"category":"شهر","word":"بروکسل","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت بلژیک"},
        {"category":"شهر","word":"آمستردام","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت هلند"},
        {"category":"شهر","word":"ژنو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"یکی از مهم‌ترین شهرهای سوئیس"},
        {"category":"شهر","word":"زوریخ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بزرگ‌ترین شهر سوئیس"},{"category":"شهر","word":"ونکوور","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری ساحلی در کانادا"},
        {"category":"شهر","word":"تورنتو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بزرگ‌ترین شهر کانادا"},
        {"category":"شهر","word":"مونترال","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری فرانسوی‌زبان در کانادا"},
        {"category":"شهر","word":"اوتاوا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت کانادا"},
        {"category":"شهر","word":"لس‌آنجلس","difficulty":2,"rarity":2,"points":12,"synonyms":"لس آنجلس","clue":"شهری که هالیوود در آن قرار دارد"},
        {"category":"شهر","word":"شیکاگو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری بزرگ در ایالت ایلینوی"},
        {"category":"شهر","word":"واشنگتن","difficulty":1,"rarity":1,"points":10,"synonyms":"واشنگتن دی سی","clue":"پایتخت آمریکا"},
        {"category":"شهر","word":"سانفرانسیسکو","difficulty":2,"rarity":2,"points":12,"synonyms":"سان فرانسیسکو","clue":"شهری با پل گلدن گیت"},
        {"category":"شهر","word":"لاس‌وگاس","difficulty":2,"rarity":2,"points":12,"synonyms":"لاس وگاس","clue":"شهری مشهور به کازینوها"},
        {"category":"شهر","word":"میامی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری ساحلی در فلوریدا"},
        {"category":"شهر","word":"مکزیکوسیتی","difficulty":2,"rarity":3,"points":15,"synonyms":"مکزیکو سیتی","clue":"پایتخت مکزیک"},
        {"category":"شهر","word":"هاوانا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت کوبا"},
        {"category":"شهر","word":"لیما","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت پرو"},
        {"category":"شهر","word":"سانتیاگو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت شیلی"},
        {"category":"شهر","word":"بوئنوس‌آیرس","difficulty":2,"rarity":3,"points":15,"synonyms":"بوئنوس آیرس","clue":"پایتخت آرژانتین"},
        {"category":"شهر","word":"برازیلیا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت برزیل"},
        {"category":"شهر","word":"ژوهانسبورگ","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بزرگ‌ترین شهر آفریقای جنوبی"},
        {"category":"شهر","word":"کیپ‌تاون","difficulty":2,"rarity":3,"points":15,"synonyms":"کیپ تاون","clue":"یکی از پایتخت‌های آفریقای جنوبی"},
        {"category":"شهر","word":"نایروبی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت کنیا"},
        {"category":"شهر","word":"آدیس‌آبابا","difficulty":3,"rarity":4,"points":18,"synonyms":"آدیس آبابا","clue":"پایتخت اتیوپی"},
        {"category":"شهر","word":"رباط","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت مراکش"},
        {"category":"شهر","word":"کازابلانکا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بزرگ‌ترین شهر مراکش"},
        {"category":"شهر","word":"الجزیره","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت الجزایر"},
        {"category":"شهر","word":"تونس","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت تونس"},
        {"category":"شهر","word":"طرابلس","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت لیبی"},
        {"category":"شهر","word":"دمشق","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت سوریه"},
        {"category":"شهر","word":"بیروت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت لبنان"},
        {"category":"شهر","word":"عمان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت اردن"},
        {"category":"شهر","word":"مسقط","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت عمان"},
        {"category":"شهر","word":"صنعا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت یمن"},
        {"category":"شهر","word":"منامه","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت بحرین"},
        {"category":"شهر","word":"کویت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت کشور کویت"},
        {"category":"شهر","word":"تفلیس","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت گرجستان"},
        {"category":"شهر","word":"ایروان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت ارمنستان"},
        {"category":"شهر","word":"باکو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پایتخت جمهوری آذربایجان"},
        {"category":"شهر","word":"تاشکند","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت ازبکستان"},
        {"category":"شهر","word":"آستانه","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت قزاقستان"},
        {"category":"شهر","word":"بیشکک","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"پایتخت قرقیزستان"},
        {"category":"شهر","word":"دوشنبه","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت تاجیکستان"},
        {"category":"شهر","word":"عشق‌آباد","difficulty":3,"rarity":4,"points":18,"synonyms":"عشق آباد","clue":"پایتخت ترکمنستان"},
        {"category":"شهر","word":"هانوی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت ویتنام"},
        {"category":"شهر","word":"هوشی‌مین","difficulty":3,"rarity":4,"points":18,"synonyms":"هوشی مین","clue":"بزرگ‌ترین شهر ویتنام"},
        {"category":"شهر","word":"کوالالامپور","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت مالزی"},
        {"category":"شهر","word":"جاکارتا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت اندونزی"},
        {"category":"شهر","word":"مانیلا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت فیلیپین"},
        {"category":"شهر","word":"دهلی","difficulty":2,"rarity":2,"points":12,"synonyms":"دهلی نو","clue":"پایتخت هند"},
        {"category":"شهر","word":"بمبئی","difficulty":2,"rarity":2,"points":12,"synonyms":"مومبای","clue":"بزرگ‌ترین شهر هند"},
        {"category":"شهر","word":"کلکته","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شهری بزرگ در شرق هند"},
        {"category":"شهر","word":"شنزن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شهر فناوری در چین"},
        {"category":"شهر","word":"شانگهای","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بزرگ‌ترین شهر چین"},
        {"category":"شهر","word":"گوانگژو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شهری بزرگ در جنوب چین"},{"category":"شهر","word":"ناگویا","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"یکی از بزرگ‌ترین شهرهای ژاپن"},
        {"category":"شهر","word":"اوساکا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"دومین منطقه شهری بزرگ ژاپن"},
        {"category":"شهر","word":"کیوتو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پایتخت تاریخی ژاپن"},
        {"category":"شهر","word":"هیروشیما","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شهری که در جنگ جهانی دوم بمباران اتمی شد"},
        {"category":"شهر","word":"یوکوهاما","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بندر مهم ژاپن در نزدیکی توکیو"},
        {"category":"شهر","word":"ملبورن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"دومین شهر بزرگ استرالیا"},
        {"category":"شهر","word":"سیدنی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهر مشهور اپرای استرالیا"},
        {"category":"شهر","word":"بریزبین","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مرکز ایالت کوئینزلند استرالیا"},
        {"category":"شهر","word":"پرت","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بزرگ‌ترین شهر غرب استرالیا"},
        {"category":"شهر","word":"اوکلند","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بزرگ‌ترین شهر نیوزیلند"},
        {"category":"شهر","word":"ولینگتون","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پایتخت نیوزیلند"},
        {"category":"شهر","word":"ریکیاویک","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت ایسلند"},
        {"category":"شهر","word":"لوکزامبورگ","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت کشور لوکزامبورگ"},
        {"category":"شهر","word":"بلگراد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"پایتخت صربستان"},
        {"category":"شهر","word":"زاگرب","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"پایتخت کرواسی"},
        {"category":"شهر","word":"لیوبلیانا","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت اسلوونی"},
        {"category":"شهر","word":"براتیسلاوا","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت اسلواکی"},
        {"category":"شهر","word":"بخارست","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"پایتخت رومانی"},
        {"category":"شهر","word":"صوفیه","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"پایتخت بلغارستان"},
        {"category":"شهر","word":"تیرانا","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت آلبانی"},
        {"category":"شهر","word":"سارایوو","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت بوسنی و هرزگوین"},
        {"category":"شهر","word":"اسکوپیه","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت مقدونیه شمالی"},
        {"category":"شهر","word":"کیشیناو","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت مولداوی"},
        {"category":"شهر","word":"مینسک","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"پایتخت بلاروس"},
        {"category":"شهر","word":"ریگا","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت لتونی"},
        {"category":"شهر","word":"ویلنیوس","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت لیتوانی"},
        {"category":"شهر","word":"تالین","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت استونی"},
        {"category":"شهر","word":"داکا","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"پایتخت بنگلادش"},
        {"category":"شهر","word":"کلمبو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بزرگ‌ترین شهر سریلانکا"},
        {"category":"شهر","word":"کاتماندو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"پایتخت نپال"},
        {"category":"شهر","word":"تیمفو","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت بوتان"},
        {"category":"شهر","word":"ماله","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"پایتخت مالدیو"},
        {"category":"شهر","word":"اورشلیم","difficulty":3,"rarity":5,"points":20,"synonyms":"بیت المقدس","clue":"شهری مقدس برای سه دین ابراهیمی"},
        {"category":"شهر","word":"غزه","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شهری در نوار غزه"},
        {"category":"شهر","word":"نابلس","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"شهری تاریخی در کرانه باختری"},
        {"category":"شهر","word":"الخلیل","difficulty":3,"rarity":5,"points":20,"synonyms":"","clue":"شهری تاریخی در فلسطین"},
        {"category":"شهر","word":"مکه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مقدس‌ترین شهر مسلمانان"},
        {"category":"شهر","word":"مدینه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دومین شهر مقدس مسلمانان"},
        {"category":"شهر","word":"جده","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بندر مهم عربستان در دریای سرخ"},
        {"category":"شهر","word":"کراچی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بزرگ‌ترین شهر پاکستان"},
        {"category":"شهر","word":"لاهور","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"دومین شهر بزرگ پاکستان"},
        {"category":"شهر","word":"پشاور","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شهری در شمال غرب پاکستان"},
        {"category":"شهر","word":"قندهار","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"از شهرهای مهم افغانستان"},
        {"category":"شهر","word":"هرات","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شهری تاریخی در غرب افغانستان"},
        {"category":"شهر","word":"مزارشریف","difficulty":3,"rarity":4,"points":18,"synonyms":"مزار شریف","clue":"شهری در شمال افغانستان"},
        {"category":"شهر","word":"نجف","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"از شهرهای مقدس عراق"},
        {"category":"شهر","word":"کربلا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهری زیارتی در عراق"},
        {"category":"شهر","word":"بصره","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بندر مهم جنوب عراق"},
        {"category":"شهر","word":"موصل","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شهری در شمال عراق"},
        {"category":"شهر","word":"سلیمانیه","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شهری در اقلیم کردستان عراق"},{"category":"شهر","word":"تبریز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان آذربایجان شرقی"},
        {"category":"شهر","word":"مرند","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در آذربایجان شرقی"},
        {"category":"شهر","word":"اهر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در منطقه ارسباران"},
        {"category":"شهر","word":"سراب","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در شرق آذربایجان شرقی"},
        {"category":"شهر","word":"شبستر","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری در نزدیکی تبریز"},
        {"category":"شهر","word":"بناب","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در جنوب آذربایجان شرقی"},
        {"category":"شهر","word":"میانه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری میان تبریز و زنجان"},
        {"category":"شهر","word":"مراغه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری با رصدخانه تاریخی"},
        {"category":"شهر","word":"هشترود","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری در جنوب شرقی آذربایجان شرقی"},
        {"category":"شهر","word":"بستان‌آباد","difficulty":3,"rarity":3,"points":15,"synonyms":"بستان آباد","clue":"شهری در مسیر تبریز به تهران"},
        {"category":"شهر","word":"اندیمشک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در شمال خوزستان"},
        {"category":"شهر","word":"دزفول","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهری با پل تاریخی"},
        {"category":"شهر","word":"آبادان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهری معروف به پالایشگاه نفت"},
        {"category":"شهر","word":"خرمشهر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شهری نماد مقاومت در جنگ ایران و عراق"},
        {"category":"شهر","word":"ماهشهر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بندر مهم استان خوزستان"},
        {"category":"شهر","word":"شوش","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری باستانی ایران"},
        {"category":"شهر","word":"شوشتر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری با سازه‌های آبی تاریخی"},
        {"category":"شهر","word":"ایذه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در شرق خوزستان"},
        {"category":"شهر","word":"بهبهان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در جنوب شرق خوزستان"},
        {"category":"شهر","word":"رامهرمز","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری در استان خوزستان"},
        {"category":"شهر","word":"لار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در جنوب فارس"},
        {"category":"شهر","word":"داراب","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در شرق استان فارس"},
        {"category":"شهر","word":"فسا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در استان فارس"},
        {"category":"شهر","word":"کازرون","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در غرب فارس"},
        {"category":"شهر","word":"آباده","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری در شمال فارس"},
        {"category":"شهر","word":"اقلید","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری کوهستانی در فارس"},
        {"category":"شهر","word":"نورآباد","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مرکز شهرستان ممسنی"},
        {"category":"شهر","word":"لامرد","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری در جنوب فارس"},
        {"category":"شهر","word":"گراش","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری در جنوب استان فارس"},
        {"category":"شهر","word":"سپیدان","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری با طبیعت کوهستانی در فارس"},
        {"category":"شهر","word":"کرمانشاه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرکز استان کرمانشاه"},
        {"category":"شهر","word":"اسلام‌آبادغرب","difficulty":3,"rarity":3,"points":15,"synonyms":"اسلام آباد غرب","clue":"شهری در استان کرمانشاه"},
        {"category":"شهر","word":"پاوه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری پلکانی در استان کرمانشاه"},
        {"category":"شهر","word":"جوانرود","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری در منطقه اورامانات"},
        {"category":"شهر","word":"کنگاور","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهری با معبد تاریخی آناهیتا"},
        {"category":"شهر","word":"هرسین","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری در شرق کرمانشاه"},
        {"category":"شهر","word":"روانسر","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری در غرب کرمانشاه"},
        {"category":"شهر","word":"صحنه","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"شهری در استان کرمانشاه"},
        {"category":"شهر","word":"قصرشیرین","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شهر مرزی ایران و عراق"},
        {"category":"شهر","word":"گیلانغرب","difficulty":3,"rarity":3,"points":15,"synonyms":"گیلان غرب","clue":"شهری در غرب کرمانشاه"},{"category":"کشور","word":"اسپانیا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری در جنوب غربی اروپا"},
        {"category":"کشور","word":"پرتغال","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در غرب اسپانیا"},
        {"category":"کشور","word":"انگلستان","difficulty":1,"rarity":1,"points":10,"synonyms":"بریتانیا","clue":"کشوری که لندن پایتخت آن است"},
        {"category":"کشور","word":"هلند","difficulty":1,"rarity":1,"points":10,"synonyms":"نیدرلند","clue":"کشوری مشهور به گل لاله"},
        {"category":"کشور","word":"بلژیک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مقر اصلی اتحادیه اروپا"},
        {"category":"کشور","word":"سوئیس","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری مشهور به بانک‌ها و ساعت"},
        {"category":"کشور","word":"اتریش","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در مرکز اروپا"},
        {"category":"کشور","word":"لهستان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در اروپای مرکزی"},
        {"category":"کشور","word":"چک","difficulty":2,"rarity":2,"points":12,"synonyms":"جمهوری چک","clue":"کشوری که پراگ پایتخت آن است"},
        {"category":"کشور","word":"اسلواکی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری همسایه جمهوری چک"},
        {"category":"کشور","word":"مجارستان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری که بوداپست پایتخت آن است"},
        {"category":"کشور","word":"رومانی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شرق اروپا"},
        {"category":"کشور","word":"بلغارستان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شبه‌جزیره بالکان"},
        {"category":"کشور","word":"یونان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"زادگاه بازی‌های المپیک"},
        {"category":"کشور","word":"کرواسی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در ساحل دریای آدریاتیک"},
        {"category":"کشور","word":"صربستان","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در منطقه بالکان"},
        {"category":"کشور","word":"اسلوونی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری کوچک در اروپای مرکزی"},
        {"category":"کشور","word":"آلبانی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در جنوب شرقی اروپا"},
        {"category":"کشور","word":"اوکراین","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شرق اروپا"},
        {"category":"کشور","word":"بلاروس","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری همسایه روسیه"},
        {"category":"کشور","word":"فنلاند","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری با هزاران دریاچه"},
        {"category":"کشور","word":"سوئد","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شمال اروپا"},
        {"category":"کشور","word":"نروژ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری مشهور به آبدره‌ها"},
        {"category":"کشور","word":"دانمارک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری که کپنهاگ پایتخت آن است"},
        {"category":"کشور","word":"ایسلند","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کشوری جزیره‌ای با آتشفشان‌های فعال"},
        {"category":"کشور","word":"ایرلند","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در غرب بریتانیا"},
        {"category":"کشور","word":"نیوزیلند","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس آرام"},
        {"category":"کشور","word":"مالزی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در جنوب شرق آسیا"},
        {"category":"کشور","word":"اندونزی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بزرگ‌ترین کشور جزیره‌ای جهان"},
        {"category":"کشور","word":"سنگاپور","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشور-شهر ثروتمند آسیایی"},
        {"category":"کشور","word":"تایلند","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری با پایتخت بانکوک"},
        {"category":"کشور","word":"ویتنام","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در جنوب شرق آسیا"},
        {"category":"کشور","word":"فیلیپین","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری متشکل از هزاران جزیره"},
        {"category":"کشور","word":"کره جنوبی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که سئول پایتخت آن است"},
        {"category":"کشور","word":"کره شمالی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شمال شبه‌جزیره کره"},
        {"category":"کشور","word":"مغولستان","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشوری میان چین و روسیه"},
        {"category":"کشور","word":"قزاقستان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بزرگ‌ترین کشور محصور در خشکی"},
        {"category":"کشور","word":"ازبکستان","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در آسیای مرکزی"},
        {"category":"کشور","word":"تاجیکستان","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشوری فارسی‌زبان در آسیای مرکزی"},
        {"category":"کشور","word":"قرقیزستان","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری کوهستانی در آسیای مرکزی"},
        {"category":"کشور","word":"ترکمنستان","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشور همسایه شمال شرقی ایران"},
        {"category":"کشور","word":"سوریه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشور همسایه غربی عراق"},
        {"category":"کشور","word":"لبنان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری در ساحل دریای مدیترانه"},
        {"category":"کشور","word":"اردن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری که امان پایتخت آن است"},
        {"category":"کشور","word":"عمان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در جنوب شرقی شبه‌جزیره عربستان"},
        {"category":"کشور","word":"یمن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در جنوب عربستان"},
        {"category":"کشور","word":"بحرین","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشور جزیره‌ای در خلیج فارس"},
        {"category":"کشور","word":"کویت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری کوچک در شمال خلیج فارس"},
        {"category":"کشور","word":"آذربایجان","difficulty":2,"rarity":2,"points":12,"synonyms":"جمهوری آذربایجان","clue":"کشوری با پایتخت باکو"},
        {"category":"کشور","word":"ارمنستان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشور همسایه شمال غربی ایران"},
        {"category":"کشور","word":"گرجستان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری میان اروپا و آسیا"},{"category":"کشور","word":"روسیه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بزرگ‌ترین کشور جهان از نظر مساحت"},
        {"category":"کشور","word":"چین","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پرجمعیت‌ترین کشور جهان"},
        {"category":"کشور","word":"ژاپن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری جزیره‌ای در شرق آسیا"},
        {"category":"کشور","word":"هند","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری با پایتخت دهلی نو"},
        {"category":"کشور","word":"پاکستان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشور همسایه شرقی ایران"},
        {"category":"کشور","word":"افغانستان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشور همسایه شرقی ایران"},
        {"category":"کشور","word":"عراق","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشور همسایه غربی ایران"},
        {"category":"کشور","word":"ترکیه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری میان آسیا و اروپا"},
        {"category":"کشور","word":"فرانسه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که پاریس پایتخت آن است"},
        {"category":"کشور","word":"آلمان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که برلین پایتخت آن است"},
        {"category":"کشور","word":"ایتالیا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که رم پایتخت آن است"},
        {"category":"کشور","word":"کانادا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"دومین کشور بزرگ جهان"},
        {"category":"کشور","word":"آمریکا","difficulty":1,"rarity":1,"points":10,"synonyms":"ایالات متحده","clue":"کشوری که واشنگتن پایتخت آن است"},
        {"category":"کشور","word":"مکزیک","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در جنوب آمریکا"},
        {"category":"کشور","word":"برزیل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بزرگ‌ترین کشور آمریکای جنوبی"},
        {"category":"کشور","word":"آرژانتین","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری که بوئنوس آیرس پایتخت آن است"},
        {"category":"کشور","word":"شیلی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری باریک در غرب آمریکای جنوبی"},
        {"category":"کشور","word":"پرو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری که ماچوپیچو در آن قرار دارد"},
        {"category":"کشور","word":"بولیوی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری بدون دسترسی به دریا در آمریکای جنوبی"},
        {"category":"کشور","word":"کلمبیا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شمال غرب آمریکای جنوبی"},
        {"category":"کشور","word":"ونزوئلا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کشوری با ذخایر بزرگ نفت"},
        {"category":"کشور","word":"اکوادور","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری که نامش از خط استوا گرفته شده"},
        {"category":"کشور","word":"اروگوئه","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری کوچک در جنوب برزیل"},
        {"category":"کشور","word":"پاراگوئه","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری محصور در خشکی در آمریکای جنوبی"},
        {"category":"کشور","word":"کوبا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری جزیره‌ای در دریای کارائیب"},
        {"category":"کشور","word":"جامائیکا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری جزیره‌ای در دریای کارائیب"},
        {"category":"کشور","word":"مصر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که اهرام جیزه در آن قرار دارد"},
        {"category":"کشور","word":"الجزایر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بزرگ‌ترین کشور آفریقا"},
        {"category":"کشور","word":"مراکش","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شمال غرب آفریقا"},
        {"category":"کشور","word":"تونس","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شمال آفریقا"},
        {"category":"کشور","word":"لیبی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شمال آفریقا"},
        {"category":"کشور","word":"سودان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری در شمال شرق آفریقا"},
        {"category":"کشور","word":"اتیوپی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"یکی از قدیمی‌ترین کشورهای آفریقا"},
        {"category":"کشور","word":"کنیا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری مشهور به حیات وحش"},
        {"category":"کشور","word":"تانزانیا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری که کلیمانجارو در آن قرار دارد"},
        {"category":"کشور","word":"نیجریه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"پرجمعیت‌ترین کشور آفریقا"},
        {"category":"کشور","word":"آفریقای جنوبی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری با سه پایتخت"},
        {"category":"کشور","word":"نامیبیا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری با صحرای نامیب"},
        {"category":"کشور","word":"بوتسوانا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در جنوب آفریقا"},
        {"category":"کشور","word":"زامبیا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری که آبشار ویکتوریا در آن قرار دارد"},
        {"category":"کشور","word":"زیمبابوه","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در جنوب آفریقا"},
        {"category":"کشور","word":"استرالیا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که کانبرا پایتخت آن است"},
        {"category":"کشور","word":"پاپوآ گینه نو","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در شمال استرالیا"},
        {"category":"کشور","word":"فیجی","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس آرام"},
        {"category":"کشور","word":"ساموآ","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس آرام"},
        {"category":"کشور","word":"قطر","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کشوری که دوحه پایتخت آن است"},
        {"category":"کشور","word":"امارات","difficulty":1,"rarity":1,"points":10,"synonyms":"امارات متحده عربی","clue":"کشوری که ابوظبی پایتخت آن است"},
        {"category":"کشور","word":"عربستان","difficulty":1,"rarity":1,"points":10,"synonyms":"عربستان سعودی","clue":"بزرگ‌ترین کشور شبه‌جزیره عربستان"},
        {"category":"کشور","word":"نپال","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کشوری که اورست در مرز آن قرار دارد"},
        {"category":"کشور","word":"بوتان","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری کوچک در رشته‌کوه هیمالیا"},{"category":"کشور","word":"لوکزامبورگ","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری کوچک در اروپای غربی"},
        {"category":"کشور","word":"لیختن‌اشتاین","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"شاهزاده‌نشینی کوچک میان سوئیس و اتریش"},
        {"category":"کشور","word":"موناکو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"دومین کشور کوچک جهان"},
        {"category":"کشور","word":"سن مارینو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کشوری کوچک درون ایتالیا"},
        {"category":"کشور","word":"واتیکان","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"کوچک‌ترین کشور جهان"},
        {"category":"کشور","word":"مالت","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری جزیره‌ای در مدیترانه"},
        {"category":"کشور","word":"مولداوی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری میان رومانی و اوکراین"},
        {"category":"کشور","word":"استونی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در ساحل دریای بالتیک"},
        {"category":"کشور","word":"لتونی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری میان استونی و لیتوانی"},
        {"category":"کشور","word":"لیتوانی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"جنوبی‌ترین کشور حوزه بالتیک"},
        {"category":"کشور","word":"بوسنی و هرزگوین","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در منطقه بالکان"},
        {"category":"کشور","word":"مقدونیه شمالی","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در جنوب شرقی اروپا"},
        {"category":"کشور","word":"مونته‌نگرو","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری کوچک در ساحل آدریاتیک"},
        {"category":"کشور","word":"کوزوو","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در منطقه بالکان"},
        {"category":"کشور","word":"بنین","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در غرب آفریقا"},
        {"category":"کشور","word":"توگو","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری باریک در غرب آفریقا"},
        {"category":"کشور","word":"غنا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در ساحل خلیج گینه"},
        {"category":"کشور","word":"کامرون","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در مرکز آفریقا"},
        {"category":"کشور","word":"سنگال","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در غرب آفریقا"},
        {"category":"کشور","word":"مالی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری محصور در خشکی در آفریقا"},
        {"category":"کشور","word":"نیجر","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در صحرای آفریقا"},
        {"category":"کشور","word":"چاد","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در مرکز آفریقا"},
        {"category":"کشور","word":"موریتانی","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در شمال غرب آفریقا"},
        {"category":"کشور","word":"موزامبیک","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در جنوب شرق آفریقا"},
        {"category":"کشور","word":"آنگولا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در جنوب غرب آفریقا"},
        {"category":"کشور","word":"گابن","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در مرکز آفریقا"},
        {"category":"کشور","word":"کنگو","difficulty":3,"rarity":3,"points":15,"synonyms":"جمهوری کنگو","clue":"کشوری در مرکز آفریقا"},
        {"category":"کشور","word":"جمهوری دموکراتیک کنگو","difficulty":4,"rarity":4,"points":18,"synonyms":"کنگو دموکراتیک","clue":"دومین کشور بزرگ آفریقا"},
        {"category":"کشور","word":"رواندا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری کوچک در شرق آفریقا"},
        {"category":"کشور","word":"بوروندی","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در شرق آفریقا"},
        {"category":"کشور","word":"ماداگاسکار","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"چهارمین جزیره بزرگ جهان"},
        {"category":"کشور","word":"سیشل","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس هند"},
        {"category":"کشور","word":"مالدیو","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس هند"},
        {"category":"کشور","word":"سریلانکا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری جزیره‌ای در جنوب هند"},
        {"category":"کشور","word":"میانمار","difficulty":3,"rarity":3,"points":15,"synonyms":"برمه","clue":"کشوری در جنوب شرق آسیا"},
        {"category":"کشور","word":"کامبوج","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری که معبد انگکور وات در آن قرار دارد"},
        {"category":"کشور","word":"لائوس","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری محصور در خشکی در جنوب شرق آسیا"},
        {"category":"کشور","word":"برونئی","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"سلطنتی کوچک در جزیره بورنئو"},
        {"category":"کشور","word":"تیمور شرقی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کشوری در جنوب شرق آسیا"},
        {"category":"کشور","word":"ونواتو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس آرام"},
        {"category":"کشور","word":"تونگا","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"پادشاهی جزیره‌ای در اقیانوس آرام"},
        {"category":"کشور","word":"کیریباتی","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری متشکل از جزایر مرجانی در اقیانوس آرام"},
        {"category":"کشور","word":"تووالو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"یکی از کوچک‌ترین کشورهای جهان"},
        {"category":"کشور","word":"نائورو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای بسیار کوچک در اقیانوس آرام"},
        {"category":"کشور","word":"جزایر مارشال","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس آرام"},
        {"category":"کشور","word":"میکرونزی","difficulty":5,"rarity":5,"points":22,"synonyms":"ایالات فدرال میکرونزی","clue":"کشوری متشکل از صدها جزیره"},
        {"category":"کشور","word":"پالائو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در غرب اقیانوس آرام"},
        {"category":"کشور","word":"سائوتومه و پرنسیپ","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در خلیج گینه"},
        {"category":"کشور","word":"کیپ ورد","difficulty":4,"rarity":4,"points":18,"synonyms":"کابو ورده","clue":"کشوری جزیره‌ای در اقیانوس اطلس"},
        {"category":"کشور","word":"جیبوتی","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در شاخ آفریقا"},{"category":"کشور","word":"آنتیگوا و باربودا","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در دریای کارائیب"},
        {"category":"کشور","word":"باهاما","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری متشکل از صدها جزیره در اقیانوس اطلس"},
        {"category":"کشور","word":"باربادوس","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری جزیره‌ای در شرق دریای کارائیب"},
        {"category":"کشور","word":"بلیز","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"تنها کشور انگلیسی‌زبان آمریکای مرکزی"},
        {"category":"کشور","word":"کاستاریکا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در آمریکای مرکزی با طبیعت مشهور"},
        {"category":"کشور","word":"پاناما","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کانال معروف جهان در این کشور قرار دارد"},
        {"category":"کشور","word":"گواتمالا","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری در آمریکای مرکزی"},
        {"category":"کشور","word":"هندوراس","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در آمریکای مرکزی"},
        {"category":"کشور","word":"السالوادور","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کوچک‌ترین کشور آمریکای مرکزی"},
        {"category":"کشور","word":"نیکاراگوئه","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"بزرگ‌ترین کشور آمریکای مرکزی"},
        {"category":"کشور","word":"دومینیکا","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در کارائیب"},
        {"category":"کشور","word":"جمهوری دومینیکن","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در جزیره هیسپانیولا"},
        {"category":"کشور","word":"هائیتی","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری همسایه جمهوری دومینیکن"},
        {"category":"کشور","word":"گرنادا","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در دریای کارائیب"},
        {"category":"کشور","word":"سنت لوسیا","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در کارائیب"},
        {"category":"کشور","word":"سنت وینسنت و گرنادین‌ها","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در دریای کارائیب"},
        {"category":"کشور","word":"سنت کیتس و نویس","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کوچک‌ترین کشور قاره آمریکا از نظر جمعیت"},
        {"category":"کشور","word":"سورینام","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کوچک‌ترین کشور آمریکای جنوبی"},
        {"category":"کشور","word":"گویان","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در شمال آمریکای جنوبی"},
        {"category":"کشور","word":"گینه","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در غرب آفریقا"},
        {"category":"کشور","word":"گینه بیسائو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری کوچک در غرب آفریقا"},
        {"category":"کشور","word":"گینه استوایی","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"تنها کشور اسپانیایی‌زبان آفریقا"},
        {"category":"کشور","word":"سیرالئون","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در غرب آفریقا"},
        {"category":"کشور","word":"لیبریا","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"قدیمی‌ترین جمهوری آفریقا"},
        {"category":"کشور","word":"بورکینافاسو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری محصور در خشکی در غرب آفریقا"},
        {"category":"کشور","word":"اسواتینی","difficulty":5,"rarity":5,"points":22,"synonyms":"سوازیلند","clue":"پادشاهی کوچکی در جنوب آفریقا"},
        {"category":"کشور","word":"لسوتو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری که کاملاً درون آفریقای جنوبی قرار دارد"},
        {"category":"کشور","word":"کومور","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس هند"},
        {"category":"کشور","word":"اریتره","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در شاخ آفریقا"},
        {"category":"کشور","word":"سومالی","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"شرقی‌ترین کشور قاره آفریقا"},
        {"category":"کشور","word":"سودان جنوبی","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"جدیدترین کشور آفریقا"},
        {"category":"کشور","word":"تیمور شرقی","difficulty":5,"rarity":5,"points":22,"synonyms":"تیمور-لسته","clue":"کشوری در شرق جزیره تیمور"},
        {"category":"کشور","word":"سیشل","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"مجمع‌الجزایری در اقیانوس هند"},
        {"category":"کشور","word":"پالائو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس آرام"},
        {"category":"کشور","word":"جزایر سلیمان","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در اقیانوس آرام"},
        {"category":"کشور","word":"وانواتو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری جزیره‌ای در ملانزی"},
        {"category":"کشور","word":"پاپوآ گینه نو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری در شمال استرالیا"},
        {"category":"کشور","word":"جزایر مارشال","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری متشکل از آب‌سنگ‌های مرجانی"},
        {"category":"کشور","word":"کیریباتی","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"کشوری که در چهار نیمکره قرار گرفته است"},
        {"category":"کشور","word":"تووالو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"یکی از کم‌جمعیت‌ترین کشورهای جهان"},
        {"category":"کشور","word":"نائورو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"سومین کشور کوچک جهان از نظر مساحت"},
        {"category":"کشور","word":"فلسطین","difficulty":3,"rarity":3,"points":15,"synonyms":"دولت فلسطین","clue":"سرزمینی در خاورمیانه با پایتخت ادعایی قدس شرقی"},
        {"category":"کشور","word":"کوزوو","difficulty":4,"rarity":4,"points":18,"synonyms":"","clue":"کشوری در بالکان که همه کشورها آن را به رسمیت نشناخته‌اند"},
        {"category":"کشور","word":"تایوان","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"جزیره‌ای در شرق آسیا با وضعیت سیاسی ویژه"},
        {"category":"کشور","word":"صحرای غربی","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"سرزمینی مورد مناقشه در شمال غرب آفریقا"},
        {"category":"کشور","word":"آبخاز","difficulty":5,"rarity":5,"points":22,"synonyms":"آبخازیا","clue":"منطقه‌ای با شناسایی محدود در قفقاز"},
        {"category":"کشور","word":"اوستیای جنوبی","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"منطقه‌ای با شناسایی محدود در قفقاز"},
        {"category":"کشور","word":"ترانس‌نیستریا","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"منطقه‌ای با شناسایی محدود در شرق اروپا"},
        {"category":"کشور","word":"جمهوری ترک قبرس شمالی","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"بخش شمالی جزیره قبرس با شناسایی محدود"},
        {"category":"کشور","word":"قبرس","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کشوری جزیره‌ای در شرق دریای مدیترانه"},{"category":"رنگ","word":"قرمز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ خون"},
        {"category":"رنگ","word":"آبی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ آسمان صاف"},
        {"category":"رنگ","word":"سبز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ طبیعت"},
        {"category":"رنگ","word":"زرد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ خورشید"},
        {"category":"رنگ","word":"نارنجی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ میوه پرتقال"},
        {"category":"رنگ","word":"بنفش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ گل بنفشه"},
        {"category":"رنگ","word":"صورتی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ گل رز روشن"},
        {"category":"رنگ","word":"قهوه‌ای","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ چوب"},
        {"category":"رنگ","word":"مشکی","difficulty":1,"rarity":1,"points":10,"synonyms":"سیاه","clue":"تیره‌ترین رنگ"},
        {"category":"رنگ","word":"سفید","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رنگ برف"},
        {"category":"رنگ","word":"خاکستری","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"رنگ بین سفید و مشکی"},
        {"category":"رنگ","word":"فیروزه‌ای","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"رنگ سنگ فیروزه"},
        {"category":"رنگ","word":"لاجوردی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"آبی تیره و درخشان"},
        {"category":"رنگ","word":"سرمه‌ای","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"آبی بسیار تیره"},
        {"category":"رنگ","word":"زرشکی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"قرمز تیره"},
        {"category":"رنگ","word":"زرین","difficulty":2,"rarity":3,"points":15,"synonyms":"طلایی","clue":"رنگ طلا"},
        {"category":"رنگ","word":"نقره‌ای","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"رنگ فلز نقره"},
        {"category":"رنگ","word":"کرم","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"رنگی روشن نزدیک به بژ"},
        {"category":"رنگ","word":"بژ","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"رنگ روشن مایل به قهوه‌ای"},
        {"category":"رنگ","word":"زیتونی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"سبز مایل به قهوه‌ای"},
        {"category":"رنگ","word":"یشمی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"سبز تیره شبیه سنگ یشم"},
        {"category":"رنگ","word":"لیمویی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"زرد مایل به سبز"},
        {"category":"رنگ","word":"کرم روشن","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"رنگی نزدیک به سفید"},
        {"category":"رنگ","word":"آبی آسمانی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"آبی روشن"},
        {"category":"رنگ","word":"آبی نفتی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"آبی بسیار تیره"},
        {"category":"رنگ","word":"آبی فیروزه‌ای","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"ترکیبی از آبی و سبز"},
        {"category":"رنگ","word":"مرجانی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"رنگ مرجان دریایی"},
        {"category":"رنگ","word":"ارغوانی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"بنفش مایل به قرمز"},
        {"category":"رنگ","word":"عنابی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"قرمز بسیار تیره"},
        {"category":"رنگ","word":"خردلی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"زرد مایل به قهوه‌ای"},
        {"category":"رنگ","word":"کاهی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"رنگ ساقه خشک گندم"},
        {"category":"رنگ","word":"یاسی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"بنفش بسیار روشن"},
        {"category":"رنگ","word":"گلبهی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"صورتی مایل به نارنجی"},
        {"category":"رنگ","word":"آلبالویی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"قرمز تیره شبیه آلبالو"},
        {"category":"رنگ","word":"مسی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"رنگ فلز مس"},
        {"category":"رنگ","word":"دودی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"خاکستری تیره"},
        {"category":"رنگ","word":"نوک مدادی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"خاکستری تیره"},
        {"category":"رنگ","word":"کهربایی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"زرد مایل به نارنجی"},
        {"category":"رنگ","word":"استخوانی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"سفید مایل به کرم"},
        {"category":"رنگ","word":"بادمجانی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"بنفش بسیار تیره"},
        {"category":"رنگ","word":"کبود","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی مایل به بنفش"},
        {"category":"رنگ","word":"سربی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"خاکستری مایل به آبی"},
        {"category":"رنگ","word":"حنایی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قرمز مایل به قهوه‌ای"},
        {"category":"رنگ","word":"شکلاتی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"قهوه‌ای تیره"},
        {"category":"رنگ","word":"صدفی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سفید براق مایل به کرم"},
        {"category":"رنگ","word":"نعنایی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز بسیار روشن"},
        {"category":"رنگ","word":"زمردی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز شبیه سنگ زمرد"},
        {"category":"رنگ","word":"یاقوتی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قرمز شبیه سنگ یاقوت"},
        {"category":"رنگ","word":"کرم تیره","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"کرم مایل به قهوه‌ای"},
        {"category":"رنگ","word":"نیلی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی مایل به بنفش"},{"category":"رنگ","word":"فیلی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"خاکستری شبیه پوست فیل"},
        {"category":"رنگ","word":"کاربنی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی بسیار تیره"},
        {"category":"رنگ","word":"آلبالویی تیره","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قرمز تیره شبیه آلبالو"},
        {"category":"رنگ","word":"هلویی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"صورتی مایل به نارنجی"},
        {"category":"رنگ","word":"طلایی","difficulty":2,"rarity":2,"points":12,"synonyms":"زرین","clue":"رنگ فلز طلا"},
        {"category":"رنگ","word":"برنزی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"رنگ فلز برنز"},
        {"category":"رنگ","word":"پلاتینی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نقره‌ای روشن"},
        {"category":"رنگ","word":"شرابی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"قرمز تیره شبیه رنگ شراب"},
        {"category":"رنگ","word":"ارکیده‌ای","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بنفش روشن شبیه گل ارکیده"},
        {"category":"رنگ","word":"زعفرانی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"زرد مایل به نارنجی"},
        {"category":"رنگ","word":"ماهگونی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قهوه‌ای مایل به قرمز"},
        {"category":"رنگ","word":"دارچینی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قهوه‌ای مایل به نارنجی"},
        {"category":"رنگ","word":"شنی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"رنگ شن ساحل"},
        {"category":"رنگ","word":"کاپوچینویی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قهوه‌ای روشن شبیه نوشیدنی کاپوچینو"},
        {"category":"رنگ","word":"زغالی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"مشکی مایل به خاکستری"},
        {"category":"رنگ","word":"مرمری","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سفید و خاکستری شبیه سنگ مرمر"},
        {"category":"رنگ","word":"بلوطی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قهوه‌ای شبیه چوب بلوط"},
        {"category":"رنگ","word":"زنگاری","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز مایل به آبی روی فلز مس"},
        {"category":"رنگ","word":"کاکائویی","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"قهوه‌ای شبیه پودر کاکائو"},
        {"category":"رنگ","word":"پسته‌ای","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز روشن شبیه پسته"},
        {"category":"رنگ","word":"زمینی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قهوه‌ای شبیه خاک"},
        {"category":"رنگ","word":"شفقی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"ترکیبی از صورتی و نارنجی آسمان"},
        {"category":"رنگ","word":"اقیانوسی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی عمیق دریا"},
        {"category":"رنگ","word":"لاجوردی روشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی روشن مایل به لاجوردی"},
        {"category":"رنگ","word":"کبود روشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی روشن مایل به بنفش"},
        {"category":"رنگ","word":"کبالتی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی پررنگ شبیه فلز کبالت"},
        {"category":"رنگ","word":"یخچالی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی بسیار روشن"},
        {"category":"رنگ","word":"دریایی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی مایل به سبز"},
        {"category":"رنگ","word":"آکوامارین","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی مایل به سبز شبیه سنگ آکوامارین"},
        {"category":"رنگ","word":"سوسنی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بنفش روشن شبیه گل سوسن"},{"category":"رنگ","word":"زمردی روشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز روشن شبیه زمرد"},
        {"category":"رنگ","word":"چمنی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز شبیه چمن"},
        {"category":"رنگ","word":"جگری","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قرمز مایل به قهوه‌ای"},
        {"category":"رنگ","word":"تمشکی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قرمز مایل به بنفش"},
        {"category":"رنگ","word":"یاقوت کبود","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی شبیه سنگ یاقوت کبود"},
        {"category":"رنگ","word":"کرم استخوانی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کرم بسیار روشن"},
        {"category":"رنگ","word":"کرم نخودی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کرم مایل به زرد"},
        {"category":"رنگ","word":"شامپاینی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"طلایی بسیار روشن"},
        {"category":"رنگ","word":"رزگلد","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"طلایی مایل به صورتی"},
        {"category":"رنگ","word":"مرغابی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز مایل به آبی"},
        {"category":"رنگ","word":"آبی سلطنتی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی پررنگ و درخشان"},
        {"category":"رنگ","word":"سبز ارتشی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز لباس نظامی"},
        {"category":"رنگ","word":"سبز لجنی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز تیره مایل به قهوه‌ای"},
        {"category":"رنگ","word":"آبی یخی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی بسیار روشن"},
        {"category":"رنگ","word":"خاکی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"رنگ خاک خشک"},
        {"category":"رنگ","word":"نسکافه‌ای","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قهوه‌ای روشن شبیه نسکافه"},
        {"category":"رنگ","word":"کاراملی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قهوه‌ای طلایی"},
        {"category":"رنگ","word":"عسلی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"رنگ عسل"},
        {"category":"رنگ","word":"کرم عسلی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کرم مایل به طلایی"},
        {"category":"رنگ","word":"برفی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سفید خالص"},
        {"category":"رنگ","word":"مهتابی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سفید مایل به آبی"},
        {"category":"رنگ","word":"دودی روشن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"خاکستری روشن"},
        {"category":"رنگ","word":"زیتونی تیره","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز زیتونی پررنگ"},
        {"category":"رنگ","word":"بلوطی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قهوه‌ای تیره شبیه چوب بلوط"},
        {"category":"رنگ","word":"شرابی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قرمز بسیار تیره"},
        {"category":"رنگ","word":"ارغوانی روشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بنفش مایل به صورتی"},
        {"category":"رنگ","word":"بادامی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قهوه‌ای روشن شبیه بادام"},
        {"category":"رنگ","word":"موکا","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قهوه‌ای شبیه قهوه موکا"},
        {"category":"رنگ","word":"وانیلی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کرم بسیار روشن"},
        {"category":"رنگ","word":"لیمویی روشن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"زرد بسیار روشن"},{"category":"رنگ","word":"سرخابی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"صورتی پررنگ مایل به قرمز"},
        {"category":"رنگ","word":"ارغوان","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بنفش مایل به قرمز"},
        {"category":"رنگ","word":"لاجورد","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی عمیق شبیه سنگ لاجورد"},
        {"category":"رنگ","word":"نیلوفری","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بنفش مایل به آبی"},
        {"category":"رنگ","word":"آلباستری","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"سفید مایل به کرم شبیه سنگ مرمر"},
        {"category":"رنگ","word":"کرم صدفی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کرم براق"},
        {"category":"رنگ","word":"سبز زمردی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز شبیه زمرد"},
        {"category":"رنگ","word":"سبز زنگاری","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز مایل به آبی"},
        {"category":"رنگ","word":"آبی کبالتی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی پررنگ شبیه کبالت"},
        {"category":"رنگ","word":"آبی نیلی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی تیره مایل به بنفش"},
        {"category":"رنگ","word":"فیروزه","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی مایل به سبز"},
        {"category":"رنگ","word":"زمرد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز درخشان"},
        {"category":"رنگ","word":"زعفرانی روشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نارنجی مایل به زرد"},
        {"category":"رنگ","word":"کهربایی روشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"طلایی مایل به نارنجی"},
        {"category":"رنگ","word":"عقیقی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قرمز شبیه سنگ عقیق"},
        {"category":"رنگ","word":"مرواریدی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سفید براق"},
        {"category":"رنگ","word":"شیری","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"سفید مایل به کرم"},
        {"category":"رنگ","word":"کرم شیری","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کرم روشن"},
        {"category":"رنگ","word":"آجری","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"قرمز مایل به قهوه‌ای"},
        {"category":"رنگ","word":"گوجه‌ای","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قرمز شبیه گوجه"},
        {"category":"رنگ","word":"اناری","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قرمز شبیه انار"},
        {"category":"رنگ","word":"زرشکی تیره","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"زرشکی پررنگ"},
        {"category":"رنگ","word":"کرم طلایی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کرم مایل به طلایی"},
        {"category":"رنگ","word":"سبز فسفری","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز بسیار درخشان"},
        {"category":"رنگ","word":"زرد فسفری","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"زرد بسیار درخشان"},
        {"category":"رنگ","word":"صورتی چرک","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"صورتی مات"},
        {"category":"رنگ","word":"آبی طاووسی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی مایل به سبز شبیه پر طاووس"},
        {"category":"رنگ","word":"سبز طاووسی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز آبی شبیه پر طاووس"},
        {"category":"رنگ","word":"بنفش تیره","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بنفش پررنگ"},
        {"category":"رنگ","word":"صورتی چرک روشن","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"صورتی کم‌رنگ و مات"},{"category":"رنگ","word":"آبی فیروزه","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی شبیه سنگ فیروزه"},
        {"category":"رنگ","word":"یشمی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز بسیار تیره"},
        {"category":"رنگ","word":"زیتونی روشن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز روشن مایل به زرد"},
        {"category":"رنگ","word":"سبز نعنایی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز روشن شبیه نعناع"},
        {"category":"رنگ","word":"سبز پسته‌ای","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز روشن شبیه پسته"},
        {"category":"رنگ","word":"کرم بژ","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کرم مایل به قهوه‌ای"},
        {"category":"رنگ","word":"شنی روشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"رنگ ماسه روشن"},
        {"category":"رنگ","word":"شنی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"رنگ ماسه مرطوب"},
        {"category":"رنگ","word":"قهوه‌ای روشن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"قهوه‌ای کم‌رنگ"},
        {"category":"رنگ","word":"قهوه‌ای تیره","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"قهوه‌ای پررنگ"},
        {"category":"رنگ","word":"فندقی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قهوه‌ای شبیه فندق"},
        {"category":"رنگ","word":"گردویی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قهوه‌ای شبیه گردو"},
        {"category":"رنگ","word":"قهوه‌ای سوخته","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قهوه‌ای بسیار تیره"},
        {"category":"رنگ","word":"کافه‌ای","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قهوه‌ای شبیه قهوه"},
        {"category":"رنگ","word":"برگ زیتونی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز شبیه برگ زیتون"},
        {"category":"رنگ","word":"سبز زمستانی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز تیره و سرد"},
        {"category":"رنگ","word":"آبی فولادی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی مایل به خاکستری"},
        {"category":"رنگ","word":"خاکستری فولادی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"خاکستری شبیه فولاد"},
        {"category":"رنگ","word":"نقره‌ای مات","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نقره‌ای بدون براقیت"},
        {"category":"رنگ","word":"طلایی مات","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"طلایی بدون درخشندگی"},
        {"category":"رنگ","word":"یاقوت سرخ","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قرمز شبیه سنگ یاقوت"},
        {"category":"رنگ","word":"زمرد کبود","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"سبز مایل به آبی"},
        {"category":"رنگ","word":"نیلی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نیلی پررنگ"},
        {"category":"رنگ","word":"نیلی روشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نیلی کم‌رنگ"},
        {"category":"رنگ","word":"گلبهی روشن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"صورتی مایل به نارنجی روشن"},
        {"category":"رنگ","word":"گلبهی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"گلبهی پررنگ"},
        {"category":"رنگ","word":"هلویی روشن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"هلویی کم‌رنگ"},
        {"category":"رنگ","word":"هلویی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"هلویی پررنگ"},
        {"category":"رنگ","word":"شفقی صورتی","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"رنگ آسمان هنگام طلوع"},
        {"category":"رنگ","word":"شفقی نارنجی","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"نارنجی آسمان هنگام غروب"},{"category":"رنگ","word":"قرمز آجری","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قرمز شبیه آجر"},
        {"category":"رنگ","word":"قرمز مرجانی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قرمز مایل به نارنجی"},
        {"category":"رنگ","word":"قرمز گوجه‌ای","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قرمز شبیه گوجه فرنگی"},
        {"category":"رنگ","word":"سبز زیتونی روشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز روشن شبیه زیتون"},
        {"category":"رنگ","word":"سبز زیتونی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز تیره شبیه زیتون"},
        {"category":"رنگ","word":"سبز زمردی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز براق شبیه زمرد"},
        {"category":"رنگ","word":"سبز جنگلی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز تیره شبیه جنگل"},
        {"category":"رنگ","word":"سبز چمنی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سبز شبیه چمن تازه"},
        {"category":"رنگ","word":"سبز خزه‌ای","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سبز شبیه خزه"},
        {"category":"رنگ","word":"سبز آووکادویی","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"سبز شبیه آووکادو"},
        {"category":"رنگ","word":"آبی اقیانوسی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی عمیق دریا"},
        {"category":"رنگ","word":"آبی آسمانی روشن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آبی روشن آسمان"},
        {"category":"رنگ","word":"آبی آسمانی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی پررنگ آسمان"},
        {"category":"رنگ","word":"آبی کبالتی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی شبیه فلز کبالت"},
        {"category":"رنگ","word":"آبی نفتی تیره","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"آبی بسیار تیره"},
        {"category":"رنگ","word":"بنفش یاسی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بنفش روشن شبیه گل یاس"},
        {"category":"رنگ","word":"بنفش ارغوانی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بنفش مایل به قرمز"},
        {"category":"رنگ","word":"بنفش آلویی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بنفش شبیه آلو"},
        {"category":"رنگ","word":"بنفش سلطنتی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بنفش پررنگ"},
        {"category":"رنگ","word":"صورتی روشن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"صورتی کم‌رنگ"},
        {"category":"رنگ","word":"صورتی تیره","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"صورتی پررنگ"},
        {"category":"رنگ","word":"صورتی سالمونی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"صورتی مایل به نارنجی"},
        {"category":"رنگ","word":"صورتی رز","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"صورتی شبیه گل رز"},
        {"category":"رنگ","word":"نارنجی سوخته","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نارنجی تیره"},
        {"category":"رنگ","word":"نارنجی کدوحلوایی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نارنجی شبیه کدو"},
        {"category":"رنگ","word":"زرد طلایی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"زرد درخشان"},
        {"category":"رنگ","word":"زرد لیمویی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"زرد شبیه لیمو"},
        {"category":"رنگ","word":"زرد قناری","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"زرد شبیه پر قناری"},
        {"category":"رنگ","word":"زرد خورشیدی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"زرد روشن مانند خورشید"},
        {"category":"رنگ","word":"زرد کهربایی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"زرد مایل به نارنجی"},{"category":"فوتبال","word":"لیونل مسی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسطوره آرژانتینی فوتبال"},
        {"category":"فوتبال","word":"کریستیانو رونالدو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ستاره پرتغالی فوتبال"},
        {"category":"فوتبال","word":"نیمار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بازیکن مشهور برزیلی"},
        {"category":"فوتبال","word":"کیلیان امباپه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ستاره سرعتی فرانسه"},
        {"category":"فوتبال","word":"ارلینگ هالند","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مهاجم نروژی گلزن"},
        {"category":"فوتبال","word":"لوکا مودریچ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"هافبک کروات رئال مادرید"},
        {"category":"فوتبال","word":"کوین دی بروینه","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"هافبک بلژیکی منچسترسیتی"},
        {"category":"فوتبال","word":"محمد صلاح","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ستاره مصری لیورپول"},
        {"category":"فوتبال","word":"روبرت لواندوفسکی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مهاجم لهستانی"},
        {"category":"فوتبال","word":"وینیسیوس جونیور","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"وینگر برزیلی رئال مادرید"},
        {"category":"فوتبال","word":"لامین یامال","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"پدیده جوان بارسلونا"},
        {"category":"فوتبال","word":"جود بلینگام","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"هافبک انگلیسی"},
        {"category":"فوتبال","word":"پدری","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"هافبک جوان بارسلونا"},
        {"category":"فوتبال","word":"گاوی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"هافبک اسپانیایی بارسلونا"},
        {"category":"فوتبال","word":"تیبو کورتوا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"دروازه‌بان بلژیکی"},
        {"category":"فوتبال","word":"مارک آندره تراشتگن","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"دروازه‌بان آلمانی بارسلونا"},
        {"category":"فوتبال","word":"آلیسون بکر","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"دروازه‌بان برزیلی لیورپول"},
        {"category":"فوتبال","word":"جان لوئیجی دوناروما","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"دروازه‌بان ایتالیایی"},
        {"category":"فوتبال","word":"ویرجیل فن دایک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مدافع هلندی لیورپول"},
        {"category":"فوتبال","word":"رودری","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"هافبک دفاعی اسپانیایی"},
        {"category":"فوتبال","word":"اشرف حکیمی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مدافع مراکشی"},
        {"category":"فوتبال","word":"ترنت الکساندر آرنولد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مدافع راست انگلیسی"},
        {"category":"فوتبال","word":"آنتوان گریزمان","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مهاجم فرانسوی"},
        {"category":"فوتبال","word":"هری کین","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"کاپیتان تیم ملی انگلیس"},
        {"category":"فوتبال","word":"سون هیونگ مین","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ستاره اهل کره جنوبی"},
        {"category":"فوتبال","word":"جمال موسیالا","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"هافبک جوان آلمان"},
        {"category":"فوتبال","word":"رافینیا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"وینگر برزیلی بارسلونا"},
        {"category":"فوتبال","word":"برونو فرناندز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"هافبک پرتغالی"},
        {"category":"فوتبال","word":"برناردو سیلوا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"هافبک پرتغالی منچسترسیتی"},
        {"category":"فوتبال","word":"فیل فودن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ستاره انگلیسی منچسترسیتی"},{"category":"فوتبال","word":"سرخیو راموس","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مدافع اسپانیایی مشهور"},
        {"category":"فوتبال","word":"ژاوی هرناندز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"هافبک افسانه‌ای بارسلونا"},
        {"category":"فوتبال","word":"آندرس اینیستا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"زننده گل قهرمانی اسپانیا در جام جهانی"},
        {"category":"فوتبال","word":"رونالدینیو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"جادوگر برزیلی فوتبال"},
        {"category":"فوتبال","word":"رونالدو نازاریو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مهاجم افسانه‌ای برزیل"},
        {"category":"فوتبال","word":"زین الدین زیدان","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"اسطوره فوتبال فرانسه"},
        {"category":"فوتبال","word":"پائولو مالدینی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مدافع افسانه‌ای میلان"},
        {"category":"فوتبال","word":"جیانلوئیجی بوفون","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"دروازه‌بان افسانه‌ای ایتالیا"},
        {"category":"فوتبال","word":"فرانچسکو توتی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"اسطوره باشگاه رم"},
        {"category":"فوتبال","word":"کاکا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"برنده توپ طلای برزیلی"},
        {"category":"فوتبال","word":"لوئیس سوارز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مهاجم اروگوئه‌ای"},
        {"category":"فوتبال","word":"ادینسون کاوانی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"گلزن اروگوئه‌ای"},
        {"category":"فوتبال","word":"گرت بیل","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ستاره ولزی رئال مادرید"},
        {"category":"فوتبال","word":"آرین روبن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"وینگر چپ‌پای هلندی"},
        {"category":"فوتبال","word":"فرانک ریبری","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"وینگر فرانسوی بایرن"},
        {"category":"فوتبال","word":"توماس مولر","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مهاجم آلمانی بایرن"},
        {"category":"فوتبال","word":"مانوئل نویر","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"دروازه‌بان آلمانی"},
        {"category":"فوتبال","word":"ژروم بواتنگ","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مدافع سابق آلمان"},
        {"category":"فوتبال","word":"سادیو مانه","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ستاره سنگالی"},
        {"category":"فوتبال","word":"ریاض محرز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بازیکن مشهور الجزایری"},
        {"category":"فوتبال","word":"جیمی واردی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"قهرمان شگفتی‌ساز لسترسیتی"},
        {"category":"فوتبال","word":"انگولو کانته","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"هافبک پرتلاش فرانسوی"},
        {"category":"فوتبال","word":"دیدیه دروگبا","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"اسطوره چلسی"},
        {"category":"فوتبال","word":"ساموئل اتوئو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مهاجم مشهور کامرونی"},
        {"category":"فوتبال","word":"یاپ استام","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"مدافع تنومند هلندی"},
        {"category":"فوتبال","word":"روبرتو کارلوس","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مدافع چپ افسانه‌ای برزیل"},
        {"category":"فوتبال","word":"دیوید بکام","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"هافبک انگلیسی با ضربات آزاد مشهور"},
        {"category":"فوتبال","word":"پل اسکولز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"هافبک افسانه‌ای منچستریونایتد"},
        {"category":"فوتبال","word":"رود فان نیستلروی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"گلزن هلندی رئال و یونایتد"},
        {"category":"فوتبال","word":"مایکل اوون","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"برنده توپ طلای انگلیسی"},{"category":"فوتبال","word":"روبرتو باجو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"اسطوره فوتبال ایتالیا"},
        {"category":"فوتبال","word":"پاول ندود","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"هافبک اهل جمهوری چک"},
        {"category":"فوتبال","word":"پاول پوگبا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"هافبک فرانسوی"},
        {"category":"فوتبال","word":"آندره پیرلو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"استاد پاس‌های بلند ایتالیا"},
        {"category":"فوتبال","word":"کلارنس سیدورف","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"تنها قهرمان اروپا با سه باشگاه"},
        {"category":"فوتبال","word":"ادگار داویدز","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"هافبک هلندی با عینک محافظ"},
        {"category":"فوتبال","word":"دنی آلوز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مدافع راست برزیلی"},
        {"category":"فوتبال","word":"مارسلو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مدافع چپ برزیلی رئال مادرید"},
        {"category":"فوتبال","word":"په په","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مدافع پرتغالی خشن"},
        {"category":"فوتبال","word":"رافائل واران","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مدافع فرانسوی"},
        {"category":"فوتبال","word":"فابیو کاناوارو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"مدافع برنده توپ طلا"},
        {"category":"فوتبال","word":"جرارد پیکه","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مدافع سابق بارسلونا"},
        {"category":"فوتبال","word":"کارلس پویول","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کاپیتان افسانه‌ای بارسلونا"},
        {"category":"فوتبال","word":"ژاوی آلونسو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"هافبک اسپانیایی و سرمربی"},
        {"category":"فوتبال","word":"سرخیو بوسکتس","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"هافبک دفاعی بارسلونا"},
        {"category":"فوتبال","word":"سسک فابرگاس","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"هافبک اسپانیایی آرسنال و بارسلونا"},
        {"category":"فوتبال","word":"ایکر کاسیاس","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"دروازه‌بان افسانه‌ای رئال مادرید"},
        {"category":"فوتبال","word":"اولیور کان","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"دروازه‌بان افسانه‌ای آلمان"},
        {"category":"فوتبال","word":"پتر چک","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"دروازه‌بان اهل جمهوری چک"},
        {"category":"فوتبال","word":"ادوین فن درسار","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"دروازه‌بان هلندی منچستریونایتد"},
        {"category":"فوتبال","word":"روملو لوکاکو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مهاجم بلژیکی"},
        {"category":"فوتبال","word":"گونسالو هیگواین","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مهاجم آرژانتینی"},
        {"category":"فوتبال","word":"آنخل دی ماریا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"وینگر آرژانتینی"},
        {"category":"فوتبال","word":"کارلوس توس","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مهاجم آرژانتینی"},
        {"category":"فوتبال","word":"سرخیو آگوئرو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"اسطوره گلزنی منچسترسیتی"},
        {"category":"فوتبال","word":"گابریل باتیستوتا","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"گلزن افسانه‌ای آرژانتین"},
        {"category":"فوتبال","word":"هرنان کرسپو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"مهاجم مشهور آرژانتین"},
        {"category":"فوتبال","word":"مارکو فان باستن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"اسطوره فوتبال هلند"},
        {"category":"فوتبال","word":"رود گولیت","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کاپیتان افسانه‌ای هلند"},
        {"category":"فوتبال","word":"دنیس برگکمپ","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"اسطوره آرسنال و هلند"},{"category":"فوتبال","word":"پله","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"اسطوره سه‌بار قهرمان جام جهانی"},
        {"category":"فوتبال","word":"دیگو مارادونا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"اسطوره آرژانتین و گل دست خدا"},
        {"category":"فوتبال","word":"فرانتس بکن‌باوئر","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"قیصر فوتبال آلمان"},
        {"category":"فوتبال","word":"گرد مولر","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"ماشین گلزنی آلمان"},
        {"category":"فوتبال","word":"لوتر ماتئوس","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"کاپیتان قهرمان آلمان در ۱۹۹۰"},
        {"category":"فوتبال","word":"روماریو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مهاجم افسانه‌ای برزیل"},
        {"category":"فوتبال","word":"ریوالدو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"برنده توپ طلای برزیل"},
        {"category":"فوتبال","word":"روماریو د سوزا","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نام کامل روماریو"},
        {"category":"فوتبال","word":"کافو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کاپیتان قهرمان برزیل"},
        {"category":"فوتبال","word":"روبرتو ریولینو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"هافبک تکنیکی برزیل"},
        {"category":"فوتبال","word":"جارج وه‌آ","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"تنها آفریقایی برنده توپ طلا"},
        {"category":"فوتبال","word":"جرج بست","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"اسطوره ایرلند شمالی"},
        {"category":"فوتبال","word":"یان راش","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"گلزن افسانه‌ای لیورپول"},
        {"category":"فوتبال","word":"استیون جرارد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کاپیتان افسانه‌ای لیورپول"},
        {"category":"فوتبال","word":"فرانک لمپارد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"هافبک گلزن چلسی"},
        {"category":"فوتبال","word":"جان تری","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کاپیتان افسانه‌ای چلسی"},
        {"category":"فوتبال","word":"ریو فردیناند","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مدافع سابق منچستریونایتد"},
        {"category":"فوتبال","word":"نمانیا ویدیچ","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مدافع صرب منچستریونایتد"},
        {"category":"فوتبال","word":"رایان گیگز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"اسطوره ولزی منچستریونایتد"},
        {"category":"فوتبال","word":"وین رونی","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"گلزن تاریخی منچستریونایتد"},
        {"category":"فوتبال","word":"اریک کانتونا","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"پادشاه فرانسوی اولدترافورد"},
        {"category":"فوتبال","word":"روی کین","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کاپیتان جنگنده منچستریونایتد"},
        {"category":"فوتبال","word":"مایکل بالاک","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"هافبک مشهور آلمان"},
        {"category":"فوتبال","word":"باستیان شواینشتایگر","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"هافبک قهرمان جهان آلمان"},
        {"category":"فوتبال","word":"فیلیپ لام","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کاپیتان قهرمان جهان آلمان"},
        {"category":"فوتبال","word":"میشل پلاتینی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"اسطوره فوتبال فرانسه"},
        {"category":"فوتبال","word":"ریمون کوپا","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"برنده توپ طلای فرانسه"},
        {"category":"فوتبال","word":"اوسبیو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"اسطوره فوتبال پرتغال"},
        {"category":"فوتبال","word":"لوئیس فیگو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"ستاره پرتغالی رئال و بارسلونا"},
        {"category":"فوتبال","word":"دکو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"هافبک پرتغالی-برزیلی بارسلونا"},{"category":"بازی","word":"ماینکرفت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بازی ساخت‌وساز با بلوک‌ها"},
        {"category":"بازی","word":"جی تی ای","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مجموعه بازی جهان‌باز راک‌استار"},
        {"category":"بازی","word":"فورتنایت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بتل رویال محبوب اپیک گیمز"},
        {"category":"بازی","word":"پابجی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بتل رویال معروف موبایل و کامپیوتر"},
        {"category":"بازی","word":"کالاف دیوتی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مجموعه شوتر اول شخص معروف"},
        {"category":"بازی","word":"کلش اف کلنز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بازی استراتژی موبایل"},
        {"category":"بازی","word":"کلش رویال","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بازی کارتی سوپرسل"},
        {"category":"بازی","word":"فری فایر","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"بتل رویال موبایلی"},
        {"category":"بازی","word":"روبلوکس","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"پلتفرم ساخت و تجربه بازی"},
        {"category":"بازی","word":"ولورانت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شوتر تاکتیکی رایوت گیمز"},
        {"category":"بازی","word":"کانتر استرایک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"شوتر رقابتی مشهور"},
        {"category":"بازی","word":"دوتا دو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بازی سبک MOBA شرکت ولو"},
        {"category":"بازی","word":"لیگ آو لجندز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بازی MOBA رایوت گیمز"},
        {"category":"بازی","word":"رد دد ریدمپشن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"وسترن جهان‌باز راک‌استار"},
        {"category":"بازی","word":"الدن رینگ","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"اثر نقش‌آفرینی فرام‌سافتور"},
        {"category":"بازی","word":"دارک سولز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مجموعه سخت و مشهور فرام‌سافتور"},
        {"category":"بازی","word":"بلک میث ووکانگ","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی اکشن الهام‌گرفته از سفر به غرب"},
        {"category":"بازی","word":"ویچر سه","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ماجراجویی گرالت"},
        {"category":"بازی","word":"سایبرپانک ۲۰۷۷","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بازی آینده‌نگر CD Projekt"},
        {"category":"بازی","word":"گاد آو وار","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ماجراجویی کریتوس"},
        {"category":"بازی","word":"هورایزن زیرو داون","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"دنیای ربات‌ها و ایلوی"},
        {"category":"بازی","word":"گوست آو سوشیما","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سامورایی در جزیره سوشیما"},
        {"category":"بازی","word":"رزیدنت اویل","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مجموعه ترس و بقا"},
        {"category":"بازی","word":"سایلنت هیل","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی ترسناک روان‌شناختی"},
        {"category":"بازی","word":"فیفا","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"شبیه‌ساز فوتبال"},
        {"category":"بازی","word":"ای فوتبال","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"نسخه جدید PES"},
        {"category":"بازی","word":"سوپر ماریو","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"لوله‌کش مشهور نینتندو"},
        {"category":"بازی","word":"زلدا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ماجراجویی لینک"},
        {"category":"بازی","word":"متال گیر سالید","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی مخفی‌کاری هیدئو کوجیما"},
        {"category":"بازی","word":"اساسینز کرید","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مجموعه مشهور یوبیسافت"},{"category":"بازی","word":"دد بای دیلایت","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"ترس و بقا به‌صورت چندنفره"},
        {"category":"بازی","word":"فال گایز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"رقابت بامزه با موانع رنگارنگ"},
        {"category":"بازی","word":"امانگ آس","difficulty":1,"rarity":2,"points":12,"synonyms":"","clue":"پیدا کردن خائن در سفینه"},
        {"category":"بازی","word":"راکت لیگ","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ترکیب فوتبال و ماشین"},
        {"category":"بازی","word":"ریمبو سیکس سیج","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شوتر تاکتیکی یوبیسافت"},
        {"category":"بازی","word":"اوورواچ","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شوتر قهرمان‌محور بلیزارد"},
        {"category":"بازی","word":"دیابلو چهار","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نقش‌آفرینی اکشن بلیزارد"},
        {"category":"بازی","word":"ورلد آو وارکرفت","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی آنلاین نقش‌آفرینی مشهور"},
        {"category":"بازی","word":"استارفیلد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نقش‌آفرینی فضایی بتسدا"},
        {"category":"بازی","word":"فال اوت","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"جهان آخرالزمانی هسته‌ای"},
        {"category":"بازی","word":"اسکایریم","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"محبوب‌ترین نسخه الدر اسکرولز"},
        {"category":"بازی","word":"الدِر اسکرولز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مجموعه نقش‌آفرینی بتسدا"},
        {"category":"بازی","word":"هیتمن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مامور ۴۷"},
        {"category":"بازی","word":"دویل می کرای","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"اکشن مشهور با شخصیت دانته"},
        {"category":"بازی","word":"تکن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بازی مبارزه‌ای محبوب"},
        {"category":"بازی","word":"مورتال کامبت","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بازی مبارزه‌ای با فیتالیتی"},
        {"category":"بازی","word":"استریت فایتر","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی مبارزه‌ای کپکام"},
        {"category":"بازی","word":"آنچارتد","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ماجراجویی نیتن دریک"},
        {"category":"بازی","word":"د لست آو آس","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"داستان جوئل و الی"},
        {"category":"بازی","word":"بلادبورن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"اثر گوتیک فرام‌سافتور"},
        {"category":"بازی","word":"هیدیز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"روگ‌لایک اسطوره‌ای"},
        {"category":"بازی","word":"سلست","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"پلتفرمر سخت با داستان احساسی"},
        {"category":"بازی","word":"هالو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شوتر علمی‌تخیلی ایکس‌باکس"},
        {"category":"بازی","word":"گیرز آو وار","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شوتر سوم‌شخص ایکس‌باکس"},
        {"category":"بازی","word":"پورتال","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"حل معما با تفنگ پورتال"},
        {"category":"بازی","word":"هاف لایف","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"ماجراجویی گوردون فریمن"},
        {"category":"بازی","word":"لفت فور دد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"همکاری برای نجات از زامبی‌ها"},
        {"category":"بازی","word":"تراریا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"ماجراجویی دوبعدی شبیه ماینکرفت"},
        {"category":"بازی","word":"استاردیو ولی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شبیه‌ساز مزرعه و زندگی"},
        {"category":"بازی","word":"پال ورلد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"جمع‌آوری موجودات و بقا"},{"category":"بازی","word":"کنکورد","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"شوتر آنلاین سونی که خیلی زود متوقف شد"},
        {"category":"بازی","word":"وارفریم","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"اکشن آنلاین با نینجاهای فضایی"},
        {"category":"بازی","word":"دستینی دو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شوتر آنلاین بانجی"},
        {"category":"بازی","word":"مانستر هانتر","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شکار هیولاهای غول‌پیکر"},
        {"category":"بازی","word":"سابناتیکا","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بقای زیر آب در سیاره‌ای بیگانه"},
        {"category":"بازی","word":"نو منز اسکای","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"اکتشاف بی‌پایان در فضا"},
        {"category":"بازی","word":"آرک","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بقا در کنار دایناسورها"},
        {"category":"بازی","word":"راست","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بازی بقا با محوریت ساخت پایگاه"},
        {"category":"بازی","word":"دی لنگ دارک","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بقای زمستانی تک‌نفره"},
        {"category":"بازی","word":"گرین هل","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بقا در جنگل‌های آمازون"},
        {"category":"بازی","word":"والهایم","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بقای وایکینگی"},
        {"category":"بازی","word":"انسکریپشن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"ترکیب بازی کارتی و معمایی"},
        {"category":"بازی","word":"بالاترو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بازی کارتی الهام‌گرفته از پوکر"},
        {"category":"بازی","word":"کاپ هد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی با طراحی کارتونی دهه ۳۰"},
        {"category":"بازی","word":"هالو نایت","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مترویدوانیای مشهور با شوالیه کوچک"},
        {"category":"بازی","word":"اورکوکد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آشپزی گروهی پر از هرج‌ومرج"},
        {"category":"بازی","word":"ایت تیکس تو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی دونفره برنده بهترین بازی سال"},
        {"category":"بازی","word":"ا وی اوت","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"فرار دو زندانی به‌صورت دونفره"},
        {"category":"بازی","word":"پی وان ای","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"شبیه‌ساز مسابقات فرمول یک"},
        {"category":"بازی","word":"گرن توریسمو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شبیه‌ساز رانندگی پلی‌استیشن"},
        {"category":"بازی","word":"فورزا هورایزن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مسابقه‌ای جهان‌باز ایکس‌باکس"},
        {"category":"بازی","word":"نید فور اسپید","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مجموعه مشهور مسابقات خیابانی"},
        {"category":"بازی","word":"د کریو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"رانندگی جهان‌باز یوبیسافت"},
        {"category":"بازی","word":"اسنایپر الیت","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"تیراندازی با دوربین و صحنه‌های اشعه ایکس"},
        {"category":"بازی","word":"آلن ویک","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نویسنده‌ای که با تاریکی می‌جنگد"},
        {"category":"بازی","word":"کنترل","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"اکشن فراطبیعی شرکت Remedy"},
        {"category":"بازی","word":"دث استرندینگ","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"تحویل محموله در دنیایی آخرالزمانی"},
        {"category":"بازی","word":"کینگدام کام دلیورنس","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نقش‌آفرینی واقع‌گرایانه قرون وسطی"},
        {"category":"بازی","word":"سی آف تیوز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"ماجراجویی دزدان دریایی آنلاین"},
        {"category":"بازی","word":"فاسموفوبیا","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شکار ارواح به‌صورت گروهی"},{"category":"بازی","word":"اسپلیت فیکشن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی همکاری‌محور سازندگان It Takes Two"},
        {"category":"بازی","word":"پروتوکول کالیستو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بازی ترسناک فضایی"},
        {"category":"بازی","word":"اسپیس مارین دو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"اکشن دنیای وارهمر چهل هزار"},
        {"category":"بازی","word":"وارهمر چهل هزار","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"دنیای علمی‌تخیلی تاریک و مشهور"},
        {"category":"بازی","word":"ریم‌ورلد","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"شبیه‌ساز مدیریت کلونی"},
        {"category":"بازی","word":"فکتوریو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"ساخت کارخانه‌های عظیم"},
        {"category":"بازی","word":"سی اس تو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"نسخه جدید کانتر استرایک"},
        {"category":"بازی","word":"پث آو اگزایل","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نقش‌آفرینی آنلاین رایگان"},
        {"category":"بازی","word":"پث آو اگزایل دو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"دنباله نقش‌آفرینی مشهور"},
        {"category":"بازی","word":"ریمچ ورلد","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"نام انگلیسی RimWorld به فارسی"},
        {"category":"بازی","word":"سون دیز تو دای","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بقای زامبی با چرخه هفتگی"},
        {"category":"بازی","word":"زنوورس","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بازی دنیای دراگون بال"},
        {"category":"بازی","word":"دراگون بال اسپارکینگ زیرو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"جدیدترین نسخه Budokai Tenkaichi"},
        {"category":"بازی","word":"بلک اپس شش","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نسخه جدید Call of Duty"},
        {"category":"بازی","word":"وارزون","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بتل رویال رایگان کالاف دیوتی"},
        {"category":"بازی","word":"آنرلد","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"ماجراجویی با شخصیت کاموایی"},
        {"category":"بازی","word":"لیتل نایتمرز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"ترس و معمایی با شخصیت سیکس"},
        {"category":"بازی","word":"پاپی پلی‌تایم","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"ترسناک با عروسک آبی"},
        {"category":"بازی","word":"فایو نایتس ات فردیز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی ترسناک با انیماترونیک‌ها"},
        {"category":"بازی","word":"آوتر وایلدز","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"اکتشاف منظومه در حلقه زمانی"},
        {"category":"بازی","word":"اکسکام دو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"استراتژی نوبتی مقابله با بیگانگان"},
        {"category":"بازی","word":"توتال وار","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"مجموعه استراتژی جنگ‌های بزرگ"},
        {"category":"بازی","word":"ایج آو امپایرز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"استراتژی تاریخی مشهور"},
        {"category":"بازی","word":"استراندد دیپ","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بقا روی جزیره پس از سقوط هواپیما"},
        {"category":"بازی","word":"اکو","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"بازی بقا با جامعه بازیکنان"},
        {"category":"بازی","word":"دیسکو الیسیوم","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نقش‌آفرینی کارآگاهی با دیالوگ‌های عمیق"},
        {"category":"بازی","word":"اکسپدیشن سی و سه","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نقش‌آفرینی Clair Obscur"},
        {"category":"بازی","word":"کلیر آبسکیور","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"عنوان کوتاه Clair Obscur"},
        {"category":"بازی","word":"اسپلانکی","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"روگ‌لایک دوبعدی مشهور"},
        {"category":"بازی","word":"دیپ راک گالاکتیک","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"همکاری کوتوله‌های فضایی برای استخراج معدن"},{"category":"بازی","word":"بالدورز گیت سه","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"نقش‌آفرینی فوق‌العاده عمیق نوبتی"},
        {"category":"بازی","word":"اسکول اند بونز","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بازی دزدان دریایی یوبیسافت"},
        {"category":"بازی","word":"اسپایدرمن","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"قهرمان مرد عنکبوتی در بازی‌های پلی‌استیشن"},
        {"category":"بازی","word":"بتمن آرکهام","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"سری بازی‌های بتمن راکستدی"},
        {"category":"بازی","word":"مادرن وارفر","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"زیرمجموعه محبوب کالاف دیوتی"},
        {"category":"بازی","word":"وورلد آو تانکس","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نبرد تانک‌های آنلاین"},
        {"category":"بازی","word":"وورلد آو وارشیپز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نبرد کشتی‌های جنگی آنلاین"},
        {"category":"بازی","word":"پالادینز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شوتر قهرمان‌محور شبیه اوورواچ"},
        {"category":"بازی","word":"اسمايت","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی MOBA با خدایان اسطوره‌ای"},
        {"category":"بازی","word":"دیوتی مودرن وارفر دو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نسخه جدید کالاف دیوتی مدرن"},
        {"category":"بازی","word":"دیوتی بلک اپس","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سری داستانی Call of Duty"},
        {"category":"بازی","word":"فاینال فانتزی","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سری نقش‌آفرینی ژاپنی مشهور"},
        {"category":"بازی","word":"نینجا گایدن","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"اکشن سخت با نینجا ریئو هایابوسا"},
        {"category":"بازی","word":"سکیرو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"سامورایی سخت فرام‌سافتور"},
        {"category":"بازی","word":"نینتندو سوییچ اسپورتز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مجموعه ورزش‌های نینتندو"},
        {"category":"بازی","word":"وایلد هارتز","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"شکار هیولا در دنیای فانتزی ژاپنی"},
        {"category":"بازی","word":"نینجا ساندباکس","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بازی‌های آزاد با فیزیک خلاقانه"},
        {"category":"بازی","word":"تینی تینا واندرلندز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شوتر فانتزی طنزآمیز"},
        {"category":"بازی","word":"بوردرلندز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شوتر لوت‌محور سل‌شید"},
        {"category":"بازی","word":"کینگ آو فایترز","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی مبارزه‌ای SNK"},{"category":"بازی","word":"اسپلینتر سل","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"مأمور مخفی سم فیشر"},
        {"category":"بازی","word":"پرنس آو پرشیا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"شاهزاده ایرانی و اکشن کلاسیک"},
        {"category":"بازی","word":"داوینچی رزولوشن","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"بازی معمایی بسیار ناشناخته"},
        {"category":"بازی","word":"اوری اند د بِلایند فارست","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"ماجراجویی احساسی جنگل تاریک"},
        {"category":"بازی","word":"اوری اند د ویل آو د ویزپس","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"دنباله Ori با گرافیک هنری"},
        {"category":"بازی","word":"داست","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بازی ایندی با فضای شاعرانه"},
        {"category":"بازی","word":"بایوشاک","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شهر زیرآبی رپچر"},
        {"category":"بازی","word":"بایوشاک اینفینیت","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شهر معلق کلمبیا"},
        {"category":"بازی","word":"پری‌دا","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"شوتر علمی‌تخیلی در ایستگاه فضایی"},
        {"category":"بازی","word":"دئوس اکس","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"سایبرپانک و تصمیم‌های اخلاقی"},
        {"category":"بازی","word":"مافیا","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"سری داستان گانگستری"},
        {"category":"بازی","word":"لاست اریکا","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"بازی موبایل داستانی و کمیاب"},
        {"category":"بازی","word":"انیمال کراسینگ","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"زندگی آرام در جزیره"},
        {"category":"بازی","word":"پیکمین","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"کنترل موجودات کوچک برای حل معما"},
        {"category":"بازی","word":"میتال هیلس","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"شهر مه‌آلود ترسناک"},
        {"category":"بازی","word":"سایلنت هیل دو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"نسخه کلاسیک ترسناک معروف"},
        {"category":"بازی","word":"سوپر اسمش برادرز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"مبارزه با شخصیت‌های نینتندو"},
        {"category":"بازی","word":"کراش بندیکوت","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"روباه بامزه پلی‌استیشن"},
        {"category":"بازی","word":"اسپایرو","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اژدهای کوچک بنفش"},{"category":"بازی","word":"کربال اسپیس پروگرام","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"ساخت و مدیریت موشک و فضاپیما"},
        {"category":"بازی","word":"دیسنرد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی مخفی‌کاری با قدرت‌های جادویی"},
        {"category":"بازی","word":"دیسنرد دو","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"دنباله ماجرای کوروو و امیلی"},
        {"category":"بازی","word":"تایتان فال دو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"شوتر سریع با ربات‌های تایتان"},
        {"category":"بازی","word":"ایپکس لجندز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بتل رویال تیمی از ریسپاون"},
        {"category":"بازی","word":"لِژند آو زلدا بریث آو د وایلد","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"ماجراجویی جهان‌باز لینک"},
        {"category":"بازی","word":"تیراریا کالکشن","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"ماجراجویی دوبعدی ساخت و بقا"},
        {"category":"بازی","word":"پورتال دو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"معماهای فیزیکی با گلاDOS"},
        {"category":"بازی","word":"لِفت فور دد دو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بقای گروهی در برابر زامبی‌ها"},
        {"category":"بازی","word":"جاست کاز","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"جهان‌باز با انفجارهای دیوانه‌وار"},
        {"category":"بازی","word":"مافیا دو","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"داستان گانگستری کلاسیک"},
        {"category":"بازی","word":"اسنایپر گوست واریور","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"تیراندازی تک‌تیرانداز مخفی"},
        {"category":"بازی","word":"پورتال استوریز مل","difficulty":4,"rarity":5,"points":20,"synonyms":"","clue":"بخش داستانی دنیای پورتال"},
        {"category":"بازی","word":"لیمبو","difficulty":2,"rarity":3,"points":15,"synonyms":"","clue":"بازی سیاه و سفید معمایی"},
        {"category":"بازی","word":"اینساید","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"بازی تاریک از سازندگان لیمبو"},
        {"category":"بازی","word":"هیتمن سه","difficulty":3,"rarity":4,"points":18,"synonyms":"","clue":"آخرین نسخه مامور ۴۷"},{"category":"گل","word":"رز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"معروف‌ترین گل عاشقانه"},
        {"category":"گل","word":"لاله","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"گل ملی ایران و هلند"},
        {"category":"گل","word":"سنبل","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"گل معطر بهاری"},
        {"category":"گل","word":"یاس","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"گل سفید بسیار خوشبو"},
        {"category":"گل","word":"نرگس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل زرد و سفید زمستانی"},
        {"category":"گل","word":"ارکیده","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل زینتی لوکس و کمیاب"},
        {"category":"گل","word":"آفتابگردان","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"گلی که به سمت خورشید می‌چرخد"},
        {"category":"گل","word":"نرگس شیراز","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نوع معطر و خاص نرگس"},
        {"category":"گل","word":"میخک","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل تزیینی شاخه‌ای"},
        {"category":"گل","word":"بنفشه","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل کوچک رنگارنگ"},
        {"category":"گل","word":"مریم","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل سفید خوشبو برای دسته‌گل"},
        {"category":"گل","word":"داوودی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل پاییزی محبوب"},
        {"category":"گل","word":"شقایق","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل قرمز دشت‌ها"},
        {"category":"گل","word":"مگنولیا","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"درختچه گل‌دار لوکس"},
        {"category":"گل","word":"سوسن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل بلند و زیبا"},
        {"category":"گل","word":"زنبق","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل سلطنتی و باوقار"},
        {"category":"گل","word":"شقایق وحشی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نوع دشت‌رو و آزاد شقایق"},{"category":"گل","word":"گل رز وحشی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نسخه طبیعی و خودرو رز"},
        {"category":"گل","word":"گل کاغذی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل رنگارنگ مخصوص باغ‌ها"},
        {"category":"گل","word":"گل ختمی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل دارویی و زینتی بلند"},
        {"category":"گل","word":"گل همیشه بهار","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گلی مقاوم و زردرنگ"},
        {"category":"گل","word":"گل صدتومنی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نوعی رز درشت و پُرگلبرگ"},
        {"category":"گل","word":"گل سوسن چلچراغ","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"گل کمیاب و حفاظت‌شده ایران"},
        {"category":"گل","word":"گل لادن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل خوراکی و زینتی"},
        {"category":"گل","word":"گل جعفری","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل نارنجی و زرد باغچه‌ای"},
        {"category":"گل","word":"گل ناز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گیاه خزنده با گل‌های کوچک"},
        {"category":"گل","word":"گل نیلوفر","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل آبی روی آب"},
        {"category":"گل","word":"گل نیلوفر آبی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گل شناور در مرداب‌ها"},
        {"category":"گل","word":"گل داوودی ژاپنی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نوع خاص داوودی تزئینی"},
        {"category":"گل","word":"گل اطلسی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل آویز و رنگارنگ باغچه‌ای"},{"category":"گل","word":"گل بنفشه آفریقایی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گل آپارتمانی کوچک و رنگارنگ"},
        {"category":"گل","word":"گل شمعدانی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"گل رایج گلدانی در خانه‌ها"},
        {"category":"گل","word":"گل پیچ امین الدوله","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گیاه رونده با گل‌های خوشبو"},
        {"category":"گل","word":"گل یخ","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گیاه مقاوم در سرمای شدید"},
        {"category":"گل","word":"گل شاه پسند","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گل زینتی معطر باغچه‌ای"},
        {"category":"گل","word":"گل ناز آفتابی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل مقاوم به خشکی و نور زیاد"},
        {"category":"گل","word":"گل شب بو","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل معطر که شب‌ها بو می‌دهد"},
        {"category":"گل","word":"گل پامچال","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل بهاری کوچک و رنگارنگ"},
        {"category":"گل","word":"گل کوکب","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل باغچه‌ای درشت و متنوع"},{"category":"گل","word":"گل رز مینیاتوری","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نوع کوچک و تزئینی رز"},
        {"category":"گل","word":"گل ارغوان","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"درختی با گل‌های بنفش بهاری"},
        {"category":"گل","word":"گل اقاقیا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"درختی با گل‌های خوشه‌ای سفید"},
        {"category":"گل","word":"گل اکالیپتوس","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"درخت معطر و دارویی"},
        {"category":"گل","word":"گل کاملیا","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"گل لوکس زمستانی با گلبرگ‌های ضخیم"},
        {"category":"گل","word":"گل آزالیا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"درختچه گل‌دار رنگارنگ"},
        {"category":"گل","word":"گل رودودندرون","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"درختچه کوهستانی کمیاب"},
        {"category":"گل","word":"گل فوشیا","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"گل آویز بنفش و صورتی"},
        {"category":"گل","word":"گل استر","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گل شبیه داوودی با گلبرگ باریک"},
        {"category":"گل","word":"گل لوتوس","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نام لاتین نیلوفر آبی مقدس"},
        {"category":"گل","word":"گل هورتانسیا","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"گل درشت خوشه‌ای رنگی"},
        {"category":"گل","word":"گل گاردنیا","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"گل سفید بسیار خوشبو"},
        {"category":"گل","word":"گل یاسمن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نوعی یاس معطر"},
        {"category":"گل","word":"گل بنفشه معطر","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"بنفشه با رایحه قوی"},
        {"category":"گل","word":"گل پامچال کوهی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نوع وحشی پامچال"},
        {"category":"گل","word":"گل نیلوفر پیچ","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گیاه رونده با گل شیپوری"},{"category":"گل","word":"گل پیچ گلیسین","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"رونده با خوشه‌های بنفش آویزان"},
        {"category":"گل","word":"گل میموزا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"درختچه با گل‌های پفکی زرد"},
        {"category":"گل","word":"گل زعفران","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گلی که ادویه گران‌قیمت از آن می‌آید"},
        {"category":"گل","word":"گل زنبق آبی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"زنبق با رنگ آبی خاص"},
        {"category":"گل","word":"گل سوسن سفید","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نوع سفید و معطر سوسن"},
        {"category":"گل","word":"گل داوودی کره‌ای","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نوع تزئینی داوودی آسیایی"},
        {"category":"گل","word":"گل رز هلندی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"رز وارداتی با ساقه بلند"},
        {"category":"گل","word":"گل رز داماسک","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"رز معطر قدیمی برای گلاب"},
        {"category":"گل","word":"گل شب‌بو بنفش","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"شب‌بو با رنگ خاص"},
        {"category":"گل","word":"گل نرگس شیپوری","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نرگس با فرم شیپوری بزرگ"},
        {"category":"گل","word":"گل نیلوفر مردابی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نیلوفر مخصوص مرداب‌ها"},
        {"category":"گل","word":"گل نیلوفر آبی مقدس","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"گل مقدس در فرهنگ شرق"},
        {"category":"گل","word":"گل یاس عربی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"یاس بسیار معطر خاورمیانه"},{"category":"گل","word":"گل ارکیده فالانوپسیس","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"ارکیده آپارتمانی پروانه‌ای شکل"},
        {"category":"گل","word":"گل ارکیده کاتلیا","difficulty":5,"rarity":5,"points":22,"synonyms":"","clue":"ارکیده لوکس با گل‌های درشت"},
        {"category":"گل","word":"گل ارکیده دندروبیوم","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"ارکیده خوشه‌ای شاخه‌ای"},
        {"category":"گل","word":"گل رز چای","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"رز کلاسیک با عطر ملایم"},
        {"category":"گل","word":"گل رز وحشی سفید","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"رز خودرو با گل‌های ساده"},
        {"category":"گل","word":"گل یاس وحشی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"یاس خودرو در طبیعت"},
        {"category":"گل","word":"گل نسترن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گل خودرو شبیه رز وحشی"},
        {"category":"گل","word":"گل بگونیا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گل زینتی رنگارنگ آپارتمانی"},
        {"category":"گل","word":"گل کالانکوئه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گیاه گوشتی با گل‌های کوچک"},
        {"category":"گل","word":"گل پتوس گلدار","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"پتوس در حالت گل‌دهی نادر"},{"category":"گل","word":"گل مگنولیا سفید","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"نوع خاص و سفید مگنولیا"},
        {"category":"گل","word":"گل مگنولیا صورتی","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"مگنولیا با رنگ صورتی ملایم"},
        {"category":"گل","word":"گل کاملیا صورتی","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"کاملیا با رنگ صورتی زیبا"},
        {"category":"گل","word":"گل کاملیا سفید","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"کاملیا کلاسیک سفید"},
        {"category":"گل","word":"گل آزالیا سفید","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"آزالیا با گل‌های سفید"},
        {"category":"گل","word":"گل آزالیا صورتی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"آزالیا با رنگ صورتی"},
        {"category":"گل","word":"گل رز قرمز هلندی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"رز صادراتی قرمز"},
        {"category":"گل","word":"گل رز سفید هلندی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"رز سفید وارداتی"},
        {"category":"گل","word":"گل رز صورتی هلندی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"رز صورتی صادراتی"},
        {"category":"گل","word":"گل داوودی سفید","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"داوودی ساده سفید"},
        {"category":"گل","word":"گل داوودی زرد","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"داوودی رنگ زرد"},
        {"category":"گل","word":"گل داوودی بنفش","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"داوودی بنفش تزئینی"},
        {"category":"گل","word":"گل داوودی صورتی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"داوودی رنگ صورتی"},
        {"category":"گل","word":"گل میخک قرمز","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"میخک رنگ قرمز"},
        {"category":"گل","word":"گل میخک سفید","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"میخک سفید ساده"},
        {"category":"گل","word":"گل میخک صورتی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"میخک صورتی"},
        {"category":"گل","word":"گل لاله قرمز","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"لاله رنگ قرمز کلاسیک"},
        {"category":"گل","word":"گل لاله زرد","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"لاله رنگ زرد"},
        {"category":"گل","word":"گل لاله سفید","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"لاله سفید ساده"},
        {"category":"گل","word":"گل لاله بنفش","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"لاله بنفش خاص"},
        {"category":"گل","word":"گل سنبل آبی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"سنبل رنگ آبی"},
        {"category":"گل","word":"گل سنبل صورتی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"سنبل رنگ صورتی"},
        {"category":"گل","word":"گل سنبل سفید","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"سنبل سفید"},
        {"category":"گل","word":"گل یاس زرد","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"یاس با رنگ زرد"},
        {"category":"گل","word":"گل یاس سفید","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"یاس کلاسیک سفید"},
        {"category":"گل","word":"گل یاس صورتی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"یاس صورتی کمیاب"},
        {"category":"گل","word":"گل ارکیده سفید","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ارکیده سفید آپارتمانی"},
        {"category":"گل","word":"گل ارکیده صورتی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ارکیده صورتی"},
        {"category":"گل","word":"گل ارکیده بنفش","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"ارکیده بنفش لوکس"},
        {"category":"گل","word":"گل ارکیده زرد","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"ارکیده زرد کمیاب"},{"category":"برند","word":"نایکی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برند معروف لباس و کفش ورزشی"},
        {"category":"برند","word":"آدیداس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رقیب اصلی نایکی در ورزش"},
        {"category":"برند","word":"پوما","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"برند ورزشی آلمانی"},
        {"category":"برند","word":"ریبوک","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند کفش ورزشی"},
        {"category":"برند","word":"آندر آرمور","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند آمریکایی لباس ورزشی"},
        {"category":"برند","word":"گوچی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند لوکس ایتالیایی"},
        {"category":"برند","word":"پرادا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"برند مد و فشن لوکس"},
        {"category":"برند","word":"لوئی ویتون","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"برند لاکچری کیف و چمدان"},
        {"category":"برند","word":"شنل","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"برند لوکس فرانسوی"},
        {"category":"برند","word":"دیور","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند مد و عطر"},
        {"category":"برند","word":"زارا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"برند پوشاک فست فشن"},
        {"category":"برند","word":"اچ اند ام","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"برند پوشاک اقتصادی"},
        {"category":"برند","word":"یونی‌کلو","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند ساده و مینیمال ژاپنی"},
        {"category":"برند","word":"سامسونگ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برند کره‌ای موبایل و لوازم الکترونیک"},
        {"category":"برند","word":"اپل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برند آیفون و مک‌بوک"},
        {"category":"برند","word":"شیائومی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"برند موبایل اقتصادی چین"},
        {"category":"برند","word":"هواوی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند تکنولوژی چینی"},
        {"category":"برند","word":"نستله","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"برند مواد غذایی و شکلات"},
        {"category":"برند","word":"کوکاکولا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نوشابه معروف جهانی"},
        {"category":"برند","word":"پپسی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"رقیب کوکاکولا"},{"category":"برند","word":"ایسوس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند سخت‌افزار و لپ‌تاپ"},
        {"category":"برند","word":"ایسر","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند لپ‌تاپ و مانیتور"},
        {"category":"برند","word":"دل","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند لپ‌تاپ آمریکایی"},
        {"category":"برند","word":"لنوو","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند لپ‌تاپ و کامپیوتر"},
        {"category":"برند","word":"اینتل","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سازنده پردازنده‌های معروف"},
        {"category":"برند","word":"ان‌ویدیا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"سازنده کارت گرافیک"},
        {"category":"برند","word":"ای ام دی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"رقیب اینتل در پردازنده"},
        {"category":"برند","word":"گوگل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"موتور جستجو و شرکت بزرگ"},
        {"category":"برند","word":"یوتیوب","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پلتفرم ویدیو"},
        {"category":"برند","word":"اینستاگرام","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شبکه اجتماعی عکس"},
        {"category":"برند","word":"فیسبوک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"شبکه اجتماعی متا"},
        {"category":"برند","word":"توییتر","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"شبکه اجتماعی متن کوتاه"},
        {"category":"برند","word":"اسنپ‌چت","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"پیام‌رسان تصویری"},
        {"category":"برند","word":"تلگرام","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پیام‌رسان محبوب"},
        {"category":"برند","word":"واتساپ","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پیام‌رسان متا"},
        {"category":"برند","word":"اوبر","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سرویس تاکسی اینترنتی"},
        {"category":"برند","word":"دیجی‌کالا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"فروشگاه اینترنتی ایران"},
        {"category":"برند","word":"آمازون","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"بزرگ‌ترین فروشگاه آنلاین"},
        {"category":"برند","word":"ایبی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"بازار خرید و فروش آنلاین"},
        {"category":"برند","word":"تسلا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خودروهای برقی و ایلان ماسک"},
        {"category":"برند","word":"مرسدس بنز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند لوکس خودرو آلمانی"},
        {"category":"برند","word":"بی ام و","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خودروهای اسپرت آلمانی"},
        {"category":"برند","word":"پورشه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"برند سوپراسپرت آلمانی"},
        {"category":"برند","word":"فراری","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ابرماشین ایتالیایی قرمز"},
        {"category":"برند","word":"لامبورگینی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ابرماشین تهاجمی ایتالیایی"},
        {"category":"برند","word":"تویوتا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"خودروساز ژاپنی قابل اعتماد"},
        {"category":"برند","word":"هیوندای","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"خودروساز کره‌ای"},
        {"category":"برند","word":"کیا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"برند خودروی کره‌ای"},{"category":"برند","word":"فورد","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"خودروساز معروف آمریکایی"},
        {"category":"برند","word":"شورلت","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند خودرو جنرال موتورز"},
        {"category":"برند","word":"جیلی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خودروساز چینی رو به رشد"},
        {"category":"برند","word":"رولز رویس","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"لوکس‌ترین خودروهای جهان"},
        {"category":"برند","word":"بنتلی","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"خودروی فوق لوکس انگلیسی"},
        {"category":"برند","word":"آستون مارتین","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"ماشین جیمز باند"},
        {"category":"برند","word":"مک لارن","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"سوپرکار انگلیسی مسابقه‌ای"},
        {"category":"برند","word":"جگوار","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"خودروساز لوکس بریتانیایی"},
        {"category":"برند","word":"لکسوس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند لوکس تویوتا"},
        {"category":"برند","word":"آکورا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند لوکس هوندا"},
        {"category":"برند","word":"مزدا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"خودروساز ژاپنی"},
        {"category":"برند","word":"سوبارو","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ماشین‌های AWD ژاپنی"},
        {"category":"برند","word":"میتسوبیشی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند خودرو ژاپنی"},
        {"category":"برند","word":"سوزوکی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"خودروساز اقتصادی ژاپن"},
        {"category":"برند","word":"نیسان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برند محبوب خودرو ژاپنی"},
        {"category":"برند","word":"داچیا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"برند اقتصادی زیرمجموعه رنو"},
        {"category":"برند","word":"رنو","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خودروساز فرانسوی"},
        {"category":"برند","word":"پژو","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"خودروساز فرانسوی"},
        {"category":"برند","word":"سیتروئن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند خودرو فرانسوی"},
        {"category":"برند","word":"آلفا رومئو","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"خودروساز اسپرت ایتالیایی"},{"category":"برند","word":"فیات","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خودروساز اقتصادی ایتالیایی"},
        {"category":"برند","word":"مازراتی","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"برند لوکس و اسپرت ایتالیایی"},
        {"category":"برند","word":"دوکاتی","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"موتورسیکلت‌های اسپرت ایتالیایی"},
        {"category":"برند","word":"هارلی دیویدسون","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"موتورهای کلاسیک آمریکایی"},
        {"category":"برند","word":"کوازاکی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برند موتور ژاپنی"},
        {"category":"برند","word":"یاماها","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"موتورساز و تجهیزات موسیقی"},
        {"category":"برند","word":"هوندا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"خودرو و موتور ژاپنی"},
        {"category":"برند","word":"سونی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"الکترونیک و پلی‌استیشن"},
        {"category":"برند","word":"نینتندو","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سازنده بازی و کنسول"},
        {"category":"برند","word":"ایکس‌باکس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"کنسول بازی مایکروسافت"},
        {"category":"برند","word":"استیم","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"پلتفرم بازی کامپیوتری"},
        {"category":"برند","word":"اپیک گیمز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سازنده فورتنایت"},
        {"category":"برند","word":"یوبی‌سافت","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سازنده اساسین کرید"},
        {"category":"برند","word":"اکتیویژن","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"سازنده کالاف دیوتی"},
        {"category":"برند","word":"الکترونیک آرتز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"EA سازنده FIFA"},
        {"category":"برند","word":"راک‌استار گیمز","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"سازنده GTA"},
        {"category":"برند","word":"بلومبرگ","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"رسانه و داده مالی"},
        {"category":"برند","word":"نتفلیکس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پلتفرم فیلم و سریال"},
        {"category":"برند","word":"اسپاتیفای","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"پخش موسیقی آنلاین"},
        {"category":"برند","word":"آمازون پرایم","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سرویس ویدیو آمازون"},{"category":"برند","word":"ردبول","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"نوشیدنی انرژی‌زا معروف"},
        {"category":"برند","word":"مونستر","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"انرژی‌درینک با لوگوی سبز"},
        {"category":"برند","word":"نسکافه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قهوه فوری نستله"},
        {"category":"برند","word":"استارباکس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کافه زنجیره‌ای جهانی"},
        {"category":"برند","word":"دومینوز پیتزا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"زنجیره پیتزافروشی معروف"},
        {"category":"برند","word":"پیتزا هات","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"رستوران زنجیره‌ای پیتزا"},
        {"category":"برند","word":"کی‌اف‌سی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مرغ سوخاری معروف"},
        {"category":"برند","word":"مک دونالد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"فست‌فود جهانی همبرگر"},
        {"category":"برند","word":"برگر کینگ","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"رقیب مک‌دونالد"},
        {"category":"برند","word":"ساب‌وی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ساندویچ زنجیره‌ای"},
        {"category":"برند","word":"دلتا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خطوط هوایی آمریکایی"},
        {"category":"برند","word":"قطر ایرویز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ایرلاین لوکس قطر"},
        {"category":"برند","word":"امارات ایرلاین","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"شرکت هواپیمایی دبی"},
        {"category":"برند","word":"لوفتهانزا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ایرلاین آلمانی"},
        {"category":"برند","word":"بریتش ایرویز","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ایرلاین بریتانیا"},
        {"category":"برند","word":"ایر فرانس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خط هوایی فرانسه"},
        {"category":"برند","word":"دلتا ایرلاینز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"یکی از بزرگ‌ترین ایرلاین‌ها"},
        {"category":"برند","word":"آمریکن ایرلاینز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"شرکت هواپیمایی آمریکا"},
        {"category":"برند","word":"اوبر ایٹس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سفارش غذا آنلاین"},
        {"category":"برند","word":"دلیورو","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سرویس سفارش غذا"},{"category":"اسم","word":"علی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسم پسرانه بسیار رایج"},
        {"category":"اسم","word":"محمد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسم پیامبر اسلام"},
        {"category":"اسم","word":"حسین","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسم امام سوم شیعیان"},
        {"category":"اسم","word":"حسن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"برادر امام حسین"},
        {"category":"اسم","word":"رضا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسم امام هشتم"},
        {"category":"اسم","word":"فاطمه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسم دخترانه بسیار رایج"},
        {"category":"اسم","word":"زهرا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"لقب حضرت فاطمه"},
        {"category":"اسم","word":"زینب","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"خواهر امام حسین"},
        {"category":"اسم","word":"سارا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اسم دخترانه بین‌المللی"},
        {"category":"اسم","word":"مریم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسم مادر حضرت عیسی"},
        {"category":"اسم","word":"یوسف","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پیامبر زیبایی"},
        {"category":"اسم","word":"یاسین","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نام یک سوره قرآن"},
        {"category":"اسم","word":"امیر","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"لقب به معنی فرمانده"},
        {"category":"اسم","word":"احمد","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نام دیگر پیامبر اسلام"},
        {"category":"اسم","word":"سعید","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"به معنی خوشحال"},
        {"category":"اسم","word":"سعیده","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اسم دخترانه از ریشه سعید"},
        {"category":"اسم","word":"نرگس","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اسم دخترانه و نام گل"},
        {"category":"اسم","word":"مهدی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسم امام دوازدهم"},
        {"category":"اسم","word":"علیرضا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ترکیب علی و رضا"},
        {"category":"اسم","word":"محمدرضا","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"نام ترکیبی بسیار رایج"},{"category":"اسم","word":"امیرحسین","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسم ترکیبی رایج پسرانه"},
        {"category":"اسم","word":"امیرعباس","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اسم پسرانه با ریشه عربی"},
        {"category":"اسم","word":"محمدحسین","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسم ترکیبی بسیار رایج"},
        {"category":"اسم","word":"محمدعلی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نام ترکیبی اسلامی"},
        {"category":"اسم","word":"علی‌اکبر","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"اسم مذهبی ترکیبی"},
        {"category":"اسم","word":"ابوالفضل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نام مشهور مذهبی"},
        {"category":"اسم","word":"ابراهیم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نام یکی از پیامبران"},
        {"category":"اسم","word":"اسماعیل","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"فرزند حضرت ابراهیم"},
        {"category":"اسم","word":"داوود","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"پیامبر و پادشاه"},
        {"category":"اسم","word":"سلیمان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پادشاه و پیامبر معروف"},
        {"category":"اسم","word":"نوید","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"به معنی خبر خوب"},
        {"category":"اسم","word":"میلاد","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"به معنی تولد"},
        {"category":"اسم","word":"پارسا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"به معنی با تقوا"},
        {"category":"اسم","word":"پرهام","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اسم پسرانه مدرن"},
        {"category":"اسم","word":"آریا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"نام باستانی ایرانی"},
        {"category":"اسم","word":"آرین","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اسم پسرانه ایرانی"},
        {"category":"اسم","word":"آیلین","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اسم دخترانه مدرن"},
        {"category":"اسم","word":"آوا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"به معنی صدا"},
        {"category":"اسم","word":"نیما","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نام شاعر معروف"},
        {"category":"اسم","word":"سینا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نام دانشمند ایرانی"},{"category":"فیزیک","word":"نیرو","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"عامل تغییر حرکت اجسام"},
        {"category":"فیزیک","word":"جرم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مقدار ماده در یک جسم"},
        {"category":"فیزیک","word":"وزن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نیروی گرانش وارد بر جسم"},
        {"category":"فیزیک","word":"سرعت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مقدار جابه‌جایی در واحد زمان"},
        {"category":"فیزیک","word":"شتاب","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"تغییر سرعت در واحد زمان"},
        {"category":"فیزیک","word":"اصطکاک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"نیرویی که حرکت را کند می‌کند"},
        {"category":"فیزیک","word":"انرژی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"توانایی انجام کار"},
        {"category":"فیزیک","word":"کار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حاصل ضرب نیرو در جابه‌جایی"},
        {"category":"فیزیک","word":"توان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"نرخ انجام کار"},
        {"category":"فیزیک","word":"گشتاور","difficulty":3,"rarity":3,"points":15,"synonyms":"","clue":"اثر چرخشی نیرو"},
        {"category":"فیزیک","word":"فشار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نیرو بر واحد سطح"},
        {"category":"فیزیک","word":"چگالی","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"جرم بر واحد حجم"},
        {"category":"فیزیک","word":"دما","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"میزان گرمی یا سردی"},
        {"category":"فیزیک","word":"گرما","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"انتقال انرژی حرارتی"},
        {"category":"فیزیک","word":"نور","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"تابش قابل مشاهده"},
        {"category":"فیزیک","word":"آینه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"سطح بازتاب‌دهنده نور"},
        {"category":"فیزیک","word":"لنز","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"ابزار شکست نور"},
        {"category":"فیزیک","word":"موج","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"انتقال انرژی بدون انتقال ماده"},
        {"category":"فیزیک","word":"فرکانس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"تعداد نوسان در ثانیه"},
        {"category":"فیزیک","word":"طول موج","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"فاصله بین دو قله موج"},{"category":"فیزیک","word":"الکتریسیته","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"جریان بارهای الکتریکی"},
        {"category":"فیزیک","word":"جریان برق","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"حرکت الکترون‌ها در مدار"},
        {"category":"فیزیک","word":"ولتاژ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"اختلاف پتانسیل الکتریکی"},
        {"category":"فیزیک","word":"مقاومت","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"مانع عبور جریان برق"},
        {"category":"فیزیک","word":"مدار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مسیر عبور جریان الکتریکی"},
        {"category":"فیزیک","word":"باتری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"منبع انرژی الکتریکی"},
        {"category":"فیزیک","word":"آهنربا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"جسمی که فلزات را جذب می‌کند"},
        {"category":"فیزیک","word":"میدان مغناطیسی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ناحیه اثر آهنربا"},
        {"category":"فیزیک","word":"القای الکترومغناطیسی","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"تولید برق از تغییر میدان مغناطیسی"},
        {"category":"فیزیک","word":"نیوتن","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"واحد نیرو"},
        {"category":"فیزیک","word":"ژول","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"واحد انرژی"},
        {"category":"فیزیک","word":"وات","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"واحد توان"},
        {"category":"فیزیک","word":"پاسکال","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"واحد فشار"},
        {"category":"فیزیک","word":"کولن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"واحد بار الکتریکی"},
        {"category":"فیزیک","word":"امپرساعت","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"واحد ظرفیت باتری"},
        {"category":"فیزیک","word":"قانون نیوتن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"قوانین حرکت اجسام"},
        {"category":"فیزیک","word":"گرانش","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"نیروی جذب بین اجسام"},
        {"category":"فیزیک","word":"سیب نیوتن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"داستان کشف گرانش"},
        {"category":"فیزیک","word":"سرعت نور","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"بیشترین سرعت در جهان"},
        {"category":"فیزیک","word":"نسبیت","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نظریه اینشتین درباره فضا و زمان"},{"category":"فیزیک","word":"ترمودینامیک","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"شاخه‌ای از فیزیک درباره گرما و انرژی"},
        {"category":"فیزیک","word":"انتقال گرما","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"جابجایی انرژی حرارتی بین اجسام"},
        {"category":"فیزیک","word":"همرفت","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"انتقال گرما در سیالات"},
        {"category":"فیزیک","word":"رسانش","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"انتقال گرما از طریق تماس مستقیم"},
        {"category":"فیزیک","word":"تابش","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"انتقال انرژی بدون نیاز به ماده"},
        {"category":"فیزیک","word":"دمای مطلق","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"اندازه‌گیری دما بر حسب کلوین"},
        {"category":"فیزیک","word":"کلوین","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"واحد دمای مطلق"},
        {"category":"فیزیک","word":"صوت","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"موج قابل شنیدن توسط انسان"},
        {"category":"فیزیک","word":"سرعت صوت","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سرعت انتشار صدا در محیط"},
        {"category":"فیزیک","word":"رزونانس","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"تقویت نوسان در فرکانس خاص"},
        {"category":"فیزیک","word":"الکترون","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ذره باردار منفی در اتم"},
        {"category":"فیزیک","word":"پروتون","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ذره باردار مثبت در هسته"},
        {"category":"فیزیک","word":"نوترون","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ذره بدون بار در هسته"},
        {"category":"فیزیک","word":"اتم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کوچک‌ترین واحد ماده"},
        {"category":"فیزیک","word":"مولکول","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"ترکیب چند اتم"},
        {"category":"فیزیک","word":"هسته اتم","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"مرکز اتم"},
        {"category":"فیزیک","word":"شکافت هسته‌ای","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"تقسیم هسته اتم"},
        {"category":"فیزیک","word":"همجوشی هسته‌ای","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"ترکیب هسته‌های سبک"},
        {"category":"فیزیک","word":"انرژی هسته‌ای","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"انرژی حاصل از هسته اتم"},
        {"category":"فیزیک","word":"مکانیک","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"بررسی حرکت اجسام"},{"category":"فیزیک","word":"سینماتیک","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"بررسی حرکت بدون در نظر گرفتن نیرو"},
        {"category":"فیزیک","word":"دینامیک","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"بررسی حرکت با در نظر گرفتن نیرو"},
        {"category":"فیزیک","word":"تعادل","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"حالتی که نیروهای وارد بر جسم خنثی هستند"},
        {"category":"فیزیک","word":"شتاب گرانشی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"شتاب ناشی از نیروی گرانش زمین"},
        {"category":"فیزیک","word":"مدار بسته","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"مداری که جریان در آن کامل عبور می‌کند"},
        {"category":"فیزیک","word":"مدار باز","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"مداری که جریان در آن قطع است"},
        {"category":"فیزیک","word":"قانون پایستگی انرژی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"انرژی از بین نمی‌رود و فقط تبدیل می‌شود"},
        {"category":"فیزیک","word":"قانون پایستگی جرم","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"جرم در واکنش‌ها ثابت می‌ماند"},
        {"category":"فیزیک","word":"کاربرد نیرو","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اثر نیرو بر حرکت یا شکل جسم"},
        {"category":"فیزیک","word":"بردار نیرو","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نیرو با جهت و اندازه مشخص"},
        {"category":"فیزیک","word":"شتاب منفی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"کاهش سرعت در حرکت"},
        {"category":"فیزیک","word":"حرکت یکنواخت","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"حرکتی با سرعت ثابت"},
        {"category":"فیزیک","word":"حرکت شتابدار","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"حرکتی با تغییر سرعت"},
        {"category":"فیزیک","word":"مسیر حرکت","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"خطی که جسم طی می‌کند"},
        {"category":"فیزیک","word":"نیروی خالص","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"جمع برداری همه نیروهای وارد بر جسم"},
        {"category":"فیزیک","word":"قانون سوم نیوتن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"برای هر کنش، واکنشی برابر وجود دارد"},
        {"category":"فیزیک","word":"قانون دوم نیوتن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"F=ma"},
        {"category":"فیزیک","word":"قانون اول نیوتن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"اینرسی یا لَختی اجسام"},
        {"category":"فیزیک","word":"اینرسی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"تمایل جسم به حفظ حالت حرکت"},
        {"category":"فیزیک","word":"لختی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"مقاومت جسم در برابر تغییر حرکت"},{"category":"فیزیک","word":"تکانه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"حاصل ضرب جرم در سرعت"},
        {"category":"فیزیک","word":"برخورد","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"برخورد دو جسم با هم"},
        {"category":"فیزیک","word":"پایستگی تکانه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"تکانه کل قبل و بعد ثابت می‌ماند"},
        {"category":"فیزیک","word":"حرکت دایره‌ای","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"حرکت روی مسیر دایره"},
        {"category":"فیزیک","word":"نیروی مرکزگرا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نیرویی به سمت مرکز دایره"},
        {"category":"فیزیک","word":"شتاب مرکزگرا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"شتاب در حرکت دایره‌ای"},
        {"category":"فیزیک","word":"مدار الکتریکی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"مسیر بسته جریان برق"},
        {"category":"فیزیک","word":"میدان الکتریکی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ناحیه اثر نیروی الکتریکی"},
        {"category":"فیزیک","word":"پتانسیل الکتریکی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"انرژی الکتریکی در هر واحد بار"},
        {"category":"فیزیک","word":"قانون اهم","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"رابطه ولتاژ، جریان و مقاومت"},
        {"category":"فیزیک","word":"مقاومت ویژه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"خاصیت ذاتی مواد در برابر جریان"},
        {"category":"فیزیک","word":"توان الکتریکی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"مصرف یا تولید انرژی در مدار"},
        {"category":"فیزیک","word":"انرژی جنبشی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"انرژی ناشی از حرکت"},
        {"category":"فیزیک","word":"انرژی پتانسیل","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"انرژی ذخیره شده"},
        {"category":"فیزیک","word":"انرژی مکانیکی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"جمع انرژی جنبشی و پتانسیل"},
        {"category":"فیزیک","word":"کار مکانیکی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"انرژی منتقل شده توسط نیرو"},
        {"category":"فیزیک","word":"بازده","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نسبت انرژی مفید به کل"},
        {"category":"فیزیک","word":"ماشین ساده","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ابزار ساده برای کاهش نیرو"},
        {"category":"فیزیک","word":"اهرم","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"میله‌ای برای افزایش نیرو"},
        {"category":"فیزیک","word":"قرقره","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"وسیله‌ای برای بالا کشیدن اجسام"},{"category":"فیزیک","word":"انرژی گرمایی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"انرژی مربوط به حرکت ذرات ماده"},
        {"category":"فیزیک","word":"ظرفیت گرمایی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"مقدار گرمای لازم برای تغییر دما"},
        {"category":"فیزیک","word":"گرمای ویژه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گرمای لازم برای یک درجه تغییر دما"},
        {"category":"فیزیک","word":"انتقال انرژی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"جابجایی انرژی بین سیستم‌ها"},
        {"category":"فیزیک","word":"تعادل حرارتی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"وقتی دو جسم هم‌دما می‌شوند"},
        {"category":"فیزیک","word":"انبساط حرارتی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"افزایش حجم با افزایش دما"},
        {"category":"فیزیک","word":"انقباض حرارتی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"کاهش حجم با کاهش دما"},
        {"category":"فیزیک","word":"نقطه ذوب","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دمایی که جامد به مایع تبدیل می‌شود"},
        {"category":"فیزیک","word":"نقطه جوش","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دمایی که مایع به گاز تبدیل می‌شود"},
        {"category":"فیزیک","word":"تبخیر","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"تبدیل مایع به بخار"},
        {"category":"فیزیک","word":"میعان","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"تبدیل گاز به مایع"},
        {"category":"فیزیک","word":"تصعید","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"تبدیل مستقیم جامد به گاز"},
        {"category":"فیزیک","word":"چگالش","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"تبدیل بخار به مایع"},
        {"category":"فیزیک","word":"تابش حرارتی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"انتقال گرما از طریق امواج"},
        {"category":"فیزیک","word":"جریان همرفتی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"حرکت سیال گرم و سرد"},
        {"category":"فیزیک","word":"قانون کولن","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نیروی بین بارهای الکتریکی"},
        {"category":"فیزیک","word":"میدان گرانشی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ناحیه اثر نیروی گرانش"},
        {"category":"فیزیک","word":"مدار ماهواره","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"مسیر حرکت ماهواره دور زمین"},
        {"category":"فیزیک","word":"پرتو ایکس","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نوعی تابش پرانرژی"},
        {"category":"فیزیک","word":"رادیواکتیو","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"واپاشی هسته‌های ناپایدار"},{"category":"اقتصاد","word":"عرضه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مقدار کالا یا خدماتی که ارائه می‌شود"},
        {"category":"اقتصاد","word":"تقاضا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مقدار نیاز و خرید کالا توسط مردم"},
        {"category":"اقتصاد","word":"تورم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"افزایش عمومی قیمت‌ها"},
        {"category":"اقتصاد","word":"رکود","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"کاهش فعالیت اقتصادی"},
        {"category":"اقتصاد","word":"بازار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"محل خرید و فروش کالا"},
        {"category":"اقتصاد","word":"سرمایه","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دارایی برای تولید و سود"},
        {"category":"اقتصاد","word":"سود","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"درآمد بیشتر از هزینه"},
        {"category":"اقتصاد","word":"زیان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کمتر شدن سرمایه"},
        {"category":"اقتصاد","word":"بانک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مؤسسه مالی برای پول"},
        {"category":"اقتصاد","word":"وام","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پول قرضی با بازپرداخت"},
        {"category":"اقتصاد","word":"بهره","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"هزینه استفاده از پول قرضی"},
        {"category":"اقتصاد","word":"ارز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"واحد پول کشورها"},
        {"category":"اقتصاد","word":"دلار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ارز رسمی آمریکا"},
        {"category":"اقتصاد","word":"یورو","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"ارز کشورهای اروپایی"},
        {"category":"اقتصاد","word":"بازار بورس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"محل خرید و فروش سهام"},
        {"category":"اقتصاد","word":"سهام","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"سهم مالکیت در شرکت"},
        {"category":"اقتصاد","word":"اوراق قرضه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ابزار بدهی دولت یا شرکت"},
        {"category":"اقتصاد","word":"مالیات","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پول اجباری برای دولت"},
        {"category":"اقتصاد","word":"تولید ناخالص داخلی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ارزش کل تولید یک کشور"},
        {"category":"اقتصاد","word":"نرخ بیکاری","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"درصد افراد بدون شغل"},{"category":"اقتصاد","word":"عرضه","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مقدار کالا یا خدماتی که ارائه می‌شود"},
        {"category":"اقتصاد","word":"تقاضا","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مقدار نیاز و خرید کالا توسط مردم"},
        {"category":"اقتصاد","word":"تورم","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"افزایش عمومی قیمت‌ها"},
        {"category":"اقتصاد","word":"رکود","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"کاهش فعالیت اقتصادی"},
        {"category":"اقتصاد","word":"بازار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"محل خرید و فروش کالا"},
        {"category":"اقتصاد","word":"سرمایه","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دارایی برای تولید و سود"},
        {"category":"اقتصاد","word":"سود","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"درآمد بیشتر از هزینه"},
        {"category":"اقتصاد","word":"زیان","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"کمتر شدن سرمایه"},
        {"category":"اقتصاد","word":"بانک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"مؤسسه مالی برای پول"},
        {"category":"اقتصاد","word":"وام","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پول قرضی با بازپرداخت"},
        {"category":"اقتصاد","word":"بهره","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"هزینه استفاده از پول قرضی"},
        {"category":"اقتصاد","word":"ارز","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"واحد پول کشورها"},
        {"category":"اقتصاد","word":"دلار","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ارز رسمی آمریکا"},
        {"category":"اقتصاد","word":"یورو","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"ارز کشورهای اروپایی"},
        {"category":"اقتصاد","word":"بازار بورس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"محل خرید و فروش سهام"},
        {"category":"اقتصاد","word":"سهام","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"سهم مالکیت در شرکت"},
        {"category":"اقتصاد","word":"اوراق قرضه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ابزار بدهی دولت یا شرکت"},
        {"category":"اقتصاد","word":"مالیات","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پول اجباری برای دولت"},
        {"category":"اقتصاد","word":"تولید ناخالص داخلی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ارزش کل تولید یک کشور"},
        {"category":"اقتصاد","word":"نرخ بیکاری","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"درصد افراد بدون شغل"},{"category":"اقتصاد","word":"تورم انتظاری","difficulty":4,"rarity":5,"points":15,"synonyms":"","clue":"پیش‌بینی مردم از افزایش آینده قیمت‌ها"},
        {"category":"اقتصاد","word":"شاخص قیمت","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"معیار تغییر سطح قیمت‌ها در اقتصاد"},
        {"category":"اقتصاد","word":"سبد مصرفی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"مجموع کالاهای مصرفی یک خانوار"},
        {"category":"اقتصاد","word":"قدرت خرید","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"میزان توانایی خرید با پول"},
        {"category":"اقتصاد","word":"نقدینگی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"مقدار پول در گردش در اقتصاد"},
        {"category":"اقتصاد","word":"پول","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ابزار مبادله کالا و خدمات"},
        {"category":"اقتصاد","word":"پایه پولی","difficulty":4,"rarity":5,"points":15,"synonyms":"","clue":"پول اصلی ایجادکننده نقدینگی"},
        {"category":"اقتصاد","word":"بانک مرکزی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نهاد کنترل پول و سیاست پولی"},
        {"category":"اقتصاد","word":"سیاست پولی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"کنترل عرضه پول توسط دولت"},
        {"category":"اقتصاد","word":"سیاست مالی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"کنترل هزینه و درآمد دولت"},
        {"category":"اقتصاد","word":"تراز تجاری","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"تفاوت صادرات و واردات"},
        {"category":"اقتصاد","word":"صادرات","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"فروش کالا به خارج کشور"},
        {"category":"اقتصاد","word":"واردات","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"خرید کالا از خارج کشور"},
        {"category":"اقتصاد","word":"تعرفه","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"مالیات بر واردات کالا"},
        {"category":"اقتصاد","word":"ارزش افزوده","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"افزایش ارزش در فرآیند تولید"},
        {"category":"اقتصاد","word":"زنجیره تأمین","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"مراحل تولید تا رسیدن کالا به مصرف‌کننده"},
        {"category":"اقتصاد","word":"بهره بانکی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سود استفاده از پول بانک"},
        {"category":"اقتصاد","word":"اعتبار","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"توانایی دریافت پول یا کالا به صورت قرض"},
        {"category":"اقتصاد","word":"ورشکستگی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ناتوانی در پرداخت بدهی‌ها"},
        {"category":"اقتصاد","word":"رکود تورمی","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"همزمانی رکود و تورم در اقتصاد"},{"category":"اقتصاد","word":"بازار سرمایه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"بازاری برای خرید و فروش دارایی‌های مالی"},
        {"category":"اقتصاد","word":"بازار پول","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"بازار وام‌های کوتاه‌مدت"},
        {"category":"اقتصاد","word":"بیمه","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"جبران خسارت در برابر ریسک"},
        {"category":"اقتصاد","word":"حق بیمه","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"هزینه‌ای که برای بیمه پرداخت می‌شود"},
        {"category":"اقتصاد","word":"تورم ساختاری","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"تورم ناشی از مشکلات ساختار اقتصاد"},
        {"category":"اقتصاد","word":"تورم تقاضا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"افزایش قیمت به دلیل افزایش تقاضا"},
        {"category":"اقتصاد","word":"تورم هزینه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"افزایش قیمت به دلیل افزایش هزینه تولید"},
        {"category":"اقتصاد","word":"انحصار طبیعی","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"بازاری که یک شرکت به‌صرفه‌تر است"},
        {"category":"اقتصاد","word":"کارت اعتباری","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ابزار پرداخت غیرنقدی"},
        {"category":"اقتصاد","word":"پول الکترونیکی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"پول دیجیتال برای پرداخت آنلاین"},
        {"category":"اقتصاد","word":"رمزارز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"پول دیجیتال غیرمتمرکز"},
        {"category":"اقتصاد","word":"بیت کوین","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"اولین رمزارز معروف"},
        {"category":"اقتصاد","word":"بازار سیاه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"بازار غیرقانونی کالا و خدمات"},
        {"category":"اقتصاد","word":"یارانه پنهان","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"حمایت غیرمستقیم دولت از قیمت‌ها"},
        {"category":"اقتصاد","word":"سفته","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"تعهد پرداخت پول در آینده"},
        {"category":"اقتصاد","word":"چک","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دستور پرداخت بانکی"},
        {"category":"اقتصاد","word":"اوراق بهادار","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"اسناد مالی قابل معامله"},
        {"category":"اقتصاد","word":"بازده سرمایه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"سود حاصل از سرمایه‌گذاری"},
        {"category":"اقتصاد","word":"هزینه فرصت","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ارزش بهترین انتخاب از دست رفته"},
        {"category":"اقتصاد","word":"کارایی اقتصادی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"استفاده بهینه از منابع"},{"category":"کارتون","word":"باب اسفنجی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اسفنج زرد زیر دریا"},
        {"category":"کارتون","word":"پاتریک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"ستاره دریایی صورتی دوست باب اسفنجی"},
        {"category":"کارتون","word":"آقای خرچنگ","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"صاحب رستوران در بیکینی باتم"},
        {"category":"کارتون","word":"اختاپوس","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"همسایه بداخلاق باب اسفنجی"},
        {"category":"کارتون","word":"پلنگ صورتی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"شخصیت کارتونی معروف صورتی"},
        {"category":"کارتون","word":"تام و جری","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"گربه و موش معروف"},
        {"category":"کارتون","word":"دورا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دختر ماجراجو کارتونی"},
        {"category":"کارتون","word":"پوکویو","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"کارتون کودکانه سه‌بعدی"},
        {"category":"کارتون","word":"بن تن","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"پسر با ساعت تبدیل شونده"},
        {"category":"کارتون","word":"جوجو سیوا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"شخصیت کارتونی و موزیکال کودکانه"},
        {"category":"کارتون","word":"ناروتو","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"نینجای مو طلایی معروف"},
        {"category":"کارتون","word":"دراگون بال","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"انیمه مبارزات و اژدها"},
        {"category":"کارتون","word":"لئو و استیچ","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"دو دوست عجیب در انیمیشن"},
        {"category":"کارتون","word":"سیندرلا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دختر کفش شیشه‌ای"},
        {"category":"کارتون","word":"سفیدبرفی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"شاهزاده خانم دیزنی"},
        {"category":"کارتون","word":"یخ‌زده","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"انیمیشن السا و آنا"},
        {"category":"کارتون","word":"شیرشاه","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"انیمیشن سیمبا"},
        {"category":"کارتون","word":"راتاتویی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"موش آشپز در پاریس"},
        {"category":"کارتون","word":"شرک","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"غول سبز مهربان"},
        {"category":"کارتون","word":"ماداگاسکار","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"حیوانات فراری از باغ‌وحش"},{"category":"کارتون","word":"ریک و مورتی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"دانشمند دیوانه و نوه‌اش"},
        {"category":"کارتون","word":"فیوچراما","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"انیمیشن علمی‌تخیلی آینده"},
        {"category":"کارتون","word":"سیمپسون‌ها","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خانواده زرد رنگ معروف"},
        {"category":"کارتون","word":"ساوت پارک","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"کارتون طنز بزرگسالان"},
        {"category":"کارتون","word":"آواتار آخرین بادافزار","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"پسر کنترل‌کننده عناصر"},
        {"category":"کارتون","word":"کوررا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نسخه بعدی آواتار"},
        {"category":"کارتون","word":"پاورپاف گرلز","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"سه دختر ابرقهرمان"},
        {"category":"کارتون","word":"بن 10 اومنیورس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نسخه پیشرفته بن تن"},
        {"category":"کارتون","word":"میکی موس","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"موش معروف دیزنی"},
        {"category":"کارتون","word":"دونالد داک","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"اردک عصبی دیزنی"},
        {"category":"کارتون","word":"گوفی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"سگ بامزه دیزنی"},
        {"category":"کارتون","word":"توی استوری","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"اسباب‌بازی‌های زنده"},
        {"category":"کارتون","word":"موانا","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دختر دریانورد اقیانوس"},
        {"category":"کارتون","word":"مولان","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دختر جنگجوی چینی"},
        {"category":"کارتون","word":"شگفت‌انگیزان","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خانواده ابرقهرمان"},
        {"category":"کارتون","word":"فروزن ۲","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ادامه داستان السا"},
        {"category":"کارتون","word":"کونگ فو پاندا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"پاندای مبارز"},
        {"category":"کارتون","word":"هتل ترانسیلوانیا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"هتل هیولاها"},
        {"category":"کارتون","word":"من نفرت‌انگیز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"گرو و مینیون‌ها"},
        {"category":"کارتون","word":"مینیون‌ها","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"موجودات زرد بامزه"},{"category":"کارتون","word":"انیمه","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سبک کارتونی ژاپنی"},
        {"category":"کارتون","word":"دراغون‌کوئست","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"انیمه/بازی فانتزی"},
        {"category":"کارتون","word":"وان پیس","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"دزد دریایی با کلاه حصیری"},
        {"category":"کارتون","word":"اتک آن تایتان","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"نبرد انسان با غول‌ها"},
        {"category":"کارتون","word":"دفترچه مرگ","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"دفتر کشتن با نوشتن اسم"},
        {"category":"کارتون","word":"ناروتو شیپودن","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ادامه داستان ناروتو"},
        {"category":"کارتون","word":"بلیچ","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"شینیگامی‌ها و ارواح"},
        {"category":"کارتون","word":"دیگریمن","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"مبارزه با شیاطین"},
        {"category":"کارتون","word":"جوجوتسو کایسن","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"جادوگران و نفرین‌ها"},
        {"category":"کارتون","word":"هلسینگ","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"مبارزه با خون‌آشام‌ها"},
        {"category":"کارتون","word":"پوکمون","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"جمع‌آوری موجودات جیبی"},
        {"category":"کارتون","word":"دیجیمون","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"موجودات دیجیتالی"},
        {"category":"کارتون","word":"یوگی اوه","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"بازی کارت‌های جادویی"},
        {"category":"کارتون","word":"دراگون بال زد","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نبردهای سون گوکو"},
        {"category":"کارتون","word":"سايلور مون","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"دختر ماه مبارز"},
        {"category":"کارتون","word":"کاپیتان سوباسا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"فوتبال انیمه‌ای"},
        {"category":"کارتون","word":"شنمیکن","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"انیمه تاریک فلسفی"},
        {"category":"کارتون","word":"گینتاما","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"کمدی سامورایی"},
        {"category":"کارتون","word":"هائیکیو","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"انیمه والیبال"},
        {"category":"کارتون","word":"تایتان سین","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"مبارزه با تایتان‌ها"},{"category":"کارتون","word":"دث نوت","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"دفترچه‌ای که با نوشتن اسم باعث مرگ می‌شود"},
        {"category":"کارتون","word":"وان پانچ من","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"قهرمانی که با یک مشت می‌برد"},
        {"category":"کارتون","word":"مانستر","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"انیمه روانشناسی درباره قاتل سریالی"},
        {"category":"کارتون","word":"کایبا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"دنیای بازی کارت‌ها و رقابت‌ها"},
        {"category":"کارتون","word":"نئون جنسیس اوانجلیون","difficulty":5,"rarity":5,"points":20,"synonyms":"","clue":"ربات‌های عظیم و فلسفه عمیق"},
        {"category":"کارتون","word":"کاوبوی بیباپ","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"شکارچی جایزه در فضا"},
        {"category":"کارتون","word":"سامورایی چامپلو","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"سامورایی‌های هیپ‌هاپی"},
        {"category":"کارتون","word":"تریگان","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"هفت‌تیرکش افسانه‌ای"},
        {"category":"کارتون","word":"گوبلین اسلیر","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"مبارزه با گوبلین‌ها"},
        {"category":"کارتون","word":"فایری تیل","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"گروه جادوگران فانتزی"},
        {"category":"کارتون","word":"بلک کلاور","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"جادو و رقابت شوالیه‌ها"},
        {"category":"کارتون","word":"دیمن اسلیر","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"شمشیرزن شکارچی شیاطین"},
        {"category":"کارتون","word":"واندر وومن انیمیشن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"قهرمان زن دی‌سی"},
        {"category":"کارتون","word":"بتمن انیمیشن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"شوالیه تاریکی"},
        {"category":"کارتون","word":"سوپرمن انیمیشن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"مرد فولادی"},
        {"category":"کارتون","word":"لگویی بتمن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نسخه لگویی بتمن"},
        {"category":"کارتون","word":"لاک‌پشت‌های نینجا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"چهار لاک‌پشت مبارز"},
        {"category":"کارتون","word":"اسپایدرمن انیمیشن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"مرد عنکبوتی کارتونی"},
        {"category":"کارتون","word":"فلش انیمیشن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"قهرمان سریع‌ترین مرد"},
        {"category":"کارتون","word":"لیگ عدالت","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"تیم قهرمانان دی‌سی"},{"category":"کارتون","word":"فلینستون‌ها","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خانواده عصر حجر کارتونی"},
        {"category":"کارتون","word":"اسمورف‌ها","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"موجودات آبی کوچک"},
        {"category":"کارتون","word":"تین تین","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"خبرنگار ماجراجو"},
        {"category":"کارتون","word":"پینکی و برین","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"موش‌های آزمایشگاهی باهوش"},
        {"category":"کارتون","word":"لاک‌پشت‌های نینجا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نسخه مدرن لاک‌پشت‌ها"},
        {"category":"کارتون","word":"آدمک آهنی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"مرد آهنی انیمیشنی"},
        {"category":"کارتون","word":"هالک انیمیشن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"غول سبز خشمگین"},
        {"category":"کارتون","word":"توربو مورف","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"قهرمانان تغییر شکل‌دهنده"},
        {"category":"کارتون","word":"والت دیزنی کلاسیک","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"کارتون‌های قدیمی دیزنی"},
        {"category":"کارتون","word":"تام و جری کودکانه","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"نسخه کودکانه تام و جری"},
        {"category":"کارتون","word":"بریکینگ بد انیمه","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"نسخه کارتونی داستان مواد"},
        {"category":"کارتون","word":"فمیلی گای","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"کارتون طنز بزرگسالان"},
        {"category":"کارتون","word":"ریک و مورتی اسپین‌آف","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"دنیاهای موازی"},
        {"category":"کارتون","word":"آمفیبیا","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"دختر در دنیای قورباغه‌ها"},
        {"category":"کارتون","word":"استیون یونیورس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"پسر با قدرت جواهرات"},
        {"category":"کارتون","word":"ستاره علیه نیروهای شیطانی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"دختر جادویی بین‌بعدی"},
        {"category":"کارتون","word":"گرنیتی فالز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"تابستان اسرارآمیز"},
        {"category":"کارتون","word":"انیمانیاکس","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"کارتون طنز کلاسیک"},
        {"category":"کارتون","word":"تینی تون","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نسخه جوان لونی تونز"},
        {"category":"کارتون","word":"لونی تونز","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"باغ‌وحش کارتونی کلاسیک"},{"category":"فیلم","word":"پارک ژوراسیک","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"دایناسورها در دنیای مدرن"},
        {"category":"فیلم","word":"میان‌ستاره‌ای","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"سفر به سیارات دور"},
        {"category":"فیلم","word":"گلادیاتور","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"نبرد در روم باستان"},
        {"category":"فیلم","word":"تلقین","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نفوذ به رویاها"},
        {"category":"فیلم","word":"شجاع‌دل","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"داستان جنگ و آزادی"},
        {"category":"فیلم","word":"جان ویک","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"قاتل حرفه‌ای انتقام‌جو"},
        {"category":"فیلم","word":"ماموریت غیرممکن","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"تیم عملیات مخفی"},
        {"category":"فیلم","word":"جنگ ستارگان","difficulty":2,"rarity":2,"points":12,"synonyms":"","clue":"نبرد کهکشان‌ها"},
        {"category":"فیلم","word":"هانوک","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ابرقدرتی در دنیای انسان‌ها"},
        {"category":"فیلم","word":"بیل را بکش","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"انتقام یک قاتل"},
        {"category":"فیلم","word":"جانگوی آزاد شده","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"برده‌ای که آزاد شد"},
        {"category":"فیلم","word":"دزدان دریایی کارائیب","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ماجراجویی کاپیتان جک اسپارو"},
        {"category":"فیلم","word":"هتل رواندا","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"داستان واقعی جنگ"},
        {"category":"فیلم","word":"زندگی زیباست","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"زندگی در اردوگاه جنگی"},
        {"category":"فیلم","word":"باشگاه مشت‌زنی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"هویت دوگانه و خشونت"},
        {"category":"فیلم","word":"جزیره شاتر","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"بیمارستان روانی مرموز"},
        {"category":"فیلم","word":"رفتگان","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نفوذ در پلیس و مافیا"},
        {"category":"فیلم","word":"مخمصه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"درگیری پلیس و دزد"},
        {"category":"فیلم","word":"هفت","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"قاتل بر اساس هفت گناه"},
        {"category":"فیلم","word":"سکوت بره‌ها","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"قاتل زنجیره‌ای و FBI"},{"category":"فیلم","word":"هشت نفرت‌انگیز","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"فیلم کوئنتین تارانتینو در برف"},
        {"category":"فیلم","word":"روزی روزگاری در هالیوود","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"فیلم درباره هالیوود قدیم"},
        {"category":"فیلم","word":"پالپ فیکشن","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"فیلم غیرخطی تارانتینو"},
        {"category":"فیلم","word":"حرامزاده‌های بی‌آبرو","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"جنگ جهانی دوم با داستان متفاوت"},
        {"category":"فیلم","word":"حرکت هتل بزرگ بوداپست","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"هتل و ماجراجویی طنز"},
        {"category":"فیلم","word":"فارست گامپ","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"زندگی مرد ساده اما الهام‌بخش"},
        {"category":"فیلم","word":"نجات سرباز رایان","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ماموریت در جنگ جهانی دوم"},
        {"category":"فیلم","word":"شهر خدا","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"زندگی در محله‌های خطرناک"},
        {"category":"فیلم","word":"رفتار غیرقانونی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"جنایی و مافیایی"},
        {"category":"فیلم","word":"روانی","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"هتل و قاتل مرموز"},
        {"category":"فیلم","word":"درخشش","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"هتل ترسناک در برف"},
        {"category":"فیلم","word":"بیگانه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"موجود فضایی ترسناک"},
        {"category":"فیلم","word":"نابودگر","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ربات قاتل از آینده"},
        {"category":"فیلم","word":"ترمیناتور ۲","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نبرد انسان و ربات"},
        {"category":"فیلم","word":"غلاف تمام فلزی","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"آموزش سربازان در جنگ"},
        {"category":"فیلم","word":"حمله به قطار پول","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"سرقت بزرگ"},
        {"category":"فیلم","word":"هویت بورن","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"جاسوس بدون حافظه"},
        {"category":"فیلم","word":"هری پاتر و زندانی آزکابان","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"قسمت سوم هری پاتر"},
        {"category":"فیلم","word":"هری پاتر و جام آتش","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"مسابقه جادویی خطرناک"},
        {"category":"فیلم","word":"هری پاتر و شاهزاده دورگه","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"رازهای ولدمورت"},{"category":"فیلم","word":"پدرخوانده ۲","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ادامه داستان مافیایی کورلئونه"},
        {"category":"فیلم","word":"پدرخوانده ۳","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"پایان داستان خانواده کورلئونه"},
        {"category":"فیلم","word":"هری پاتر و محفل ققنوس","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"تشکیل گروه مقاومت جادوگران"},
        {"category":"فیلم","word":"هری پاتر و یادگاران مرگ","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نبرد نهایی با ولدمورت"},
        {"category":"فیلم","word":"جنگ ستارگان امپراتوری ضربه می‌زند","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"قسمت مهم از جنگ ستارگان"},
        {"category":"فیلم","word":"جنگ ستارگان بازگشت جدای","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"پایان سه‌گانه کلاسیک"},
        {"category":"فیلم","word":"ماتریکس ریلودد","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"ادامه دنیای ماتریکس"},
        {"category":"فیلم","word":"ماتریکس رولووشن","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"پایان سه‌گانه ماتریکس"},
        {"category":"فیلم","word":"آواتار","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دنیای پاندورا"},
        {"category":"فیلم","word":"آواتار راه آب","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"دنباله آواتار در دریا"},
        {"category":"فیلم","word":"مینیون‌ها ۲","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"ادامه ماجراجویی مینیون‌ها"},
        {"category":"فیلم","word":"شرک ۲","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"ادامه داستان غول سبز"},
        {"category":"فیلم","word":"شرک ۳","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"پادشاهی دوردور"},
        {"category":"فیلم","word":"شرک ۴","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"دنیای موازی شرک"},
        {"category":"فیلم","word":"مرد عنکبوتی","difficulty":1,"rarity":1,"points":10,"synonyms":"","clue":"قهرمان تارزن نیویورک"},
        {"category":"فیلم","word":"مرد عنکبوتی دور از خانه","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"سفر اروپایی پیتر پارکر"},
        {"category":"فیلم","word":"مرد عنکبوتی راهی به خانه نیست","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"چندجهانی اسپایدرمن‌ها"},
        {"category":"فیلم","word":"ونوم ۲","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ادامه سیمبیوت ونوم"},
        {"category":"فیلم","word":"ددپول ۲","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ادامه شوخی‌های ددپول"},
        {"category":"فیلم","word":"لوگان","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"پایان داستان ولورین"},{"category":"فیلم","word":"جانوران شگفت‌انگیز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"دنیای جادویی هری پاتر در گذشته"},
        {"category":"فیلم","word":"آلیس در سرزمین عجایب","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"دختر در دنیای خیالی عجیب"},
        {"category":"فیلم","word":"چارلی و کارخانه شکلات‌سازی","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"کارخانه شکلات جادویی"},
        {"category":"فیلم","word":"ایندیانا جونز","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ماجراجوی باستان‌شناس"},
        {"category":"فیلم","word":"مومیایی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"فرعون زنده‌شده"},
        {"category":"فیلم","word":"حلقه","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"نوار ویدئویی مرگبار"},
        {"category":"فیلم","word":"احضار","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"داستان‌های تسخیر و جن"},
        {"category":"فیلم","word":"آنابل","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"عروسک شیطانی"},
        {"category":"فیلم","word":"آن","difficulty":3,"rarity":4,"points":15,"synonyms":"","clue":"دلقک ترسناک پنی‌وایز"},
        {"category":"فیلم","word":"تلقین ۲","difficulty":4,"rarity":5,"points":18,"synonyms":"","clue":"ادامه دنیای رویاها"},
        {"category":"فیلم","word":"جومانجی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"بازی‌ای که واقعی می‌شود"},
        {"category":"فیلم","word":"جومانجی به جنگل خوش آمدید","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ورود به بازی خطرناک"},
        {"category":"فیلم","word":"جومانجی مرحله بعدی","difficulty":2,"rarity":3,"points":12,"synonyms":"","clue":"ادامه بازی جومانجی"},
        {"category":"فیلم","word":"سونیک","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"جوجه‌تیغی سریع آبی"},
        {"category":"فیلم","word":"سونیک ۲","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"ادامه ماجراجویی سونیک"},
        {"category":"فیلم","word":"موانا ۲","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"ادامه سفر دختر اقیانوس"},
        {"category":"فیلم","word":"شیرشاه ۲","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"ادامه داستان سیمبا"},
        {"category":"فیلم","word":"علاءالدین","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"چراغ جادویی و جن"},
        {"category":"فیلم","word":"زیبای خفته","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"شاهزاده خانم خوابیده"},
        {"category":"فیلم","word":"دیو و دلبر","difficulty":1,"rarity":2,"points":10,"synonyms":"","clue":"هیولا و عشق"},
    ]
    for item in words:
        if add_word(
            item["category"],
            item["word"],
            item["difficulty"],
            item["rarity"],
            item["points"],
            item["synonyms"],
            item["clue"],
        ):
            added += 1

    print(f"Added {added} words.")


if __name__ == "__main__":
    import_words()
```


================================================================================
FILE: story\story.py
================================================================================

```py

```


================================================================================
FILE: tex.py
================================================================================

```py
from pathlib import Path

# مسیر پروژه
PROJECT_DIR = Path(r"C:\Users\Nima\Desktop\kalemo")  # ← تغییر بده

OUTPUT_FILE = "project_dump.md"

# پسوندهایی که می‌خواهیم
INCLUDE_EXTENSIONS = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".sql",
    ".html",
    ".css",
    ".js",
    ".xml",
    ".csv"
}

# پوشه‌هایی که نباید بررسی شوند
EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "venv",
    ".venv",
    "env",
    "node_modules",
    "dist",
    "build"
}

with open(OUTPUT_FILE, "w", encoding="utf-8") as out:

    out.write("# Project Dump\n\n")

    for file in sorted(PROJECT_DIR.rglob("*")):

        if not file.is_file():
            continue

        if any(part in EXCLUDE_DIRS for part in file.parts):
            continue

        if file.suffix.lower() not in INCLUDE_EXTENSIONS:
            continue

        relative = file.relative_to(PROJECT_DIR)

        out.write("\n")
        out.write("=" * 80 + "\n")
        out.write(f"FILE: {relative}\n")
        out.write("=" * 80 + "\n\n")

        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = file.read_text(encoding="utf-8-sig")
            except:
                text = file.read_text(errors="ignore")

        out.write("```")
        out.write(file.suffix[1:] if file.suffix else "text")
        out.write("\n")
        out.write(text)
        out.write("\n```\n\n")

print("Done!")
print(f"Saved to: {OUTPUT_FILE}")
```


================================================================================
FILE: tools\import_words.py
================================================================================

```py
"""ابزار Import واژگان از JSON یا CSV به دیتابیس کلمو.

استفاده:
    python -m tools.import_words words.json
    python -m tools.import_words words.csv

فرمت JSON: لیستی از آبجکت‌ها:
    [{"word":"سیب","category":"میوه","difficulty":1,"rarity":1,
      "points":10,"synonyms":"","clue":""}, ...]

فرمت CSV: سطر اول هدر با ستون‌های word,category و اختیاری
    difficulty,rarity,points,synonyms,clue
فقط word و category الزامی‌اند؛ بقیه پیش‌فرض دارند.
"""
import sys, os, json, csv

def load_json(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):  # {"category":[words...]}
        recs = []
        for cat, words in data.items():
            for w in words:
                recs.append({"word": w, "category": cat})
        return recs
    return data

def load_csv(path):
    recs = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            recs.append(row)
    return recs

def main(argv):
    if len(argv) < 2:
        print("usage: python -m tools.import_words <file.json|file.csv>")
        return 1
    path = argv[1]
    if not os.path.exists(path):
        print("file not found:", path); return 1
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from core import db
    db.init()
    recs = load_json(path) if path.lower().endswith(".json") else load_csv(path)
    added, skipped = db.import_words(recs)
    print(f"✅ added: {added} | skipped (duplicate/invalid): {skipped}")
    print("categories now:", db.list_categories())
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```


================================================================================
FILE: ui\__init__.py
================================================================================

```py

```


================================================================================
FILE: ui\cards.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""کارت‌های گرافیکی متنی (HTML) برای تلگرام."""
DIV = "━━━━━━━━━━━━━━"

def _bar(value, maximum, width=10):
    if maximum <= 0:
        maximum = 1
    filled = int(round(width * min(value, maximum) / maximum))
    return "▰" * filled + "▱" * (width - filled)

def profile_card(p):
    pct_bar = _bar(p["xp"], p["xp_needed"])
    winrate = int(100 * p["wins"] / p["games"]) if p["games"] else 0
    return (
        f"<b>🪪 پروفایل {p['name']}</b>\n{DIV}\n"
        f"🏅 <b>سطح {p['level']}</b>\n"
        f"⚡️ XP: {pct_bar}  <code>{p['xp']}/{p['xp_needed']}</code>\n{DIV}\n"
        f"🪙 سکه: <b>{p['coins']:,}</b>\n"
        f"🔥 استریک: <b>{p['streak']} روز</b>\n{DIV}\n"
        f"🎮 بازی‌ها: <b>{p['games']}</b>\n"
        f"🏆 بردها: <b>{p['wins']}</b>  (<b>{winrate}%</b>)\n"
        f"⭐️ بهترین امتیاز: <b>{p['best']}</b>"
    )

def levelup_card(level, reward_coins):
    return (
        f"<b>🎚 لِوِل آپ!</b>\n{DIV}\n"
        f"رسیدی به <b>سطح {level}</b> 🎉\n"
        f"جایزه: <b>+{reward_coins} سکه</b> 🪙\n{DIV}\n"
        f"<i>یه پله بالاتر، یه ذره خفن‌تر 🔝</i>"
    )

def game_over_card(title, rows, ad=None):
    lines = "\n".join(f"{r[0]} <b>{r[1]}</b> — {r[2]} امتیاز" for r in rows)
    card = f"<b>🏁 {title}</b>\n{DIV}\n{lines}\n{DIV}"
    if ad:
        card += f"\n\n📣 <i>{ad}</i>"
    return card

def leaderboard_card(title, rows):
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, score) in enumerate(rows):
        badge = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{badge} <b>{name}</b> — {score}")
    return f"<b>🏆 {title}</b>\n{DIV}\n" + ("\n".join(lines) if lines else "هنوز کسی نیست!")

def daily_card(coins, streak, mission_text):
    return (
        f"<b>🎁 جایزه روزانه</b>\n{DIV}\n"
        f"🪙 <b>+{coins} سکه</b>\n"
        f"🔥 استریک: <b>{streak} روز</b>\n{DIV}\n"
        f"<b>🎯 مأموریت امروز:</b>\n{mission_text}"
    )

```


================================================================================
FILE: ui\keyboards.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""کیبوردهای اینلاین یکپارچه Kalemo."""
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
import config


def main_menu():
    return M([
        [B("🎮 ایجاد بازی", callback_data="m:play")],
        [B("🌳 باغچه", callback_data="m:garden"),
         B("💡 پیشنهاد کلمه", callback_data="m:suggest")],
        [B("👤 پروفایل", callback_data="m:profile"),
         B("🎯 ماموریت روزانه", callback_data="m:mission")],
        [B("🏆 لیدربورد", callback_data="m:lb"),
         B("🎁 جایزه روزانه", callback_data="m:daily")],
        [B("⚙ تنظیمات", callback_data="m:settings"),
         B("❓ راهنما", callback_data="m:help")],
    ])

def back_menu():
    return M([[B("🔙 منوی اصلی", callback_data="m:home")]])


def settings_menu():
    return M([
        [B("✏️ تغییر نام نمایشی", callback_data="m:rename")],
        [B("🔙 منوی اصلی", callback_data="m:home")],
    ])


def profile_menu():
    return M([
        [B("✏️ تغییر نام نمایشی", callback_data="m:rename")],
        [B("🔙 منوی اصلی", callback_data="m:home")],
    ])


def onboarding(step):
    if step < 3:
        return M([[B("بعدی ➡️", callback_data=f"ob:{step+1}")]])
    return M([[B("🎮 انتخاب نام و شروع", callback_data="ob:name")]])


def cancel_rename():
    return M([[B("🔙 بی‌خیال", callback_data="m:home")]])


def mission_claim(can_claim):
    rows = []
    if can_claim:
        rows.append([B("🎁 دریافت جایزه", callback_data="mission:claim")])
    rows.append([B("🔙 منوی اصلی", callback_data="m:home")])
    return M(rows)


def play_in_group():
    url = f"https://t.me/{config.BOT_USERNAME}?startgroup=true"
    return M([[B("➕ افزودن کلمو به گروه", url=url)],
             [B("🔙 منوی اصلی", callback_data="m:home")]])


# ---- پنل ادمین ----
def admin_panel():
    return M([
        [B("📊 آمار کلی", callback_data="a:stats")],
        [B("🪙 دادن سکه/XP", callback_data="a:give"),
         B("🔎 پروفایل کاربر", callback_data="a:find")],
        [B("📣 پیام همگانی", callback_data="a:bcast")],
        [B("🗂 مدیریت دسته/کلمه", callback_data="a:words")],
        [B("👥 ادمین‌های همکار", callback_data="a:admins")],
        [B("❌ بستن", callback_data="a:close")],
    ])


def admin_back():
    return M([[B("🔙 پنل ادمین", callback_data="a:home")]])


def admin_words_menu():
    return M([
        [B("📋 لیست دسته‌ها", callback_data="a:wlist")],
        [B("💡 پیشنهادها", callback_data="a:suggests")],
        [B("🔙 پنل ادمین", callback_data="a:home")],
    ])
def suggest_menu():
    return M([
        [B("💡 ثبت پیشنهاد", callback_data="m:suggest")],
        [B("🔙 منوی اصلی", callback_data="m:home")],
    ])
def admin_suggestion_kb(sid):
    return M([
        [
            B("✅ تایید", callback_data=f"a:sapp:{sid}"),
            B("❌ رد", callback_data=f"a:srej:{sid}")
        ],
        [
            B("✏️ ویرایش", callback_data=f"a:sedit:{sid}"),
            B("📂 تغییر دسته", callback_data=f"a:scat:{sid}")
        ],
        [
            B("➡ بعدی", callback_data="a:suggests"),
            B("🔙 پنل ادمین", callback_data="a:home")
        ],
    ])
```


================================================================================
FILE: ui\onboarding.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""آنبوردینگ تعاملی برای کاربر جدید."""
STEPS = {
    1: ("<b>👋 سلام! من کلمو‌ام</b>\n━━━━━━━━━━━━━━\n"
        "یه بازی کلمه‌ایِ گروهی که <b>هیچ‌وقت تکراری نمی‌شه</b>!\n"
        "من وسط بازی قانونا رو عوض می‌کنم تا حواست جمع بمونه 😏"),
    2: ("<b>🎮 بازی چطوریه؟</b>\n━━━━━━━━━━━━━━\n"
        "پنج مود داریم: کلاسیک، جای خالی، اسم‌وفامیل، قوانین متغیر و سرنخ.\n"
        "تو گروه «شروع کلمو» بنویس، دوستاتو دعوت کن و بترکونید! 🔥"),
    3: ("<b>🏅 چی گیرت میاد؟</b>\n━━━━━━━━━━━━━━\n"
        "🪙 سکه، ⚡️ XP، 🔥 استریک روزانه و رتبه تو لیدربورد!\n"
        "آخرین قدم: یه نام نمایشی برای خودت انتخاب کن 👇"),
}


def step_text(n):
    return STEPS.get(n, STEPS[3])

```


================================================================================
FILE: ui\panels.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""پنل‌های متنی + کیبوردهای بازی گروهی کلمو (همه با EditMessage)."""
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
from game.modes import MODE_ORDER, mode_meta
from game.rules import REGISTRY
from game.session import TIME_OPTIONS, DIFFICULTY_OPTIONS, time_label, difficulty_label
from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M
from core import db
DIV = "────────────────"


# ---- پنل اصلی لابی ----
def lobby_text(s):
    meta = mode_meta(s.mode_id)
    ready = "آماده‌ی شروع ✅" if s.count() >= 2 else "منتظر بازیکن… (حداقل ۲ نفر)"
    return (
        "🎮 <b>ایجاد مسابقه کلمو</b>\n"
        f"{DIV}\n"
        f"🎲 مود: <b>{meta['emoji']} {meta['name']}</b>\n"
        f"<i>{meta['desc']}</i>\n\n"
        f"⏱ زمان: <b>{time_label(s.time_limit)}</b>\n"
        f"👑 سازنده: <b>{s.host_name}</b>\n\n"
        f"👥 بازیکنان: <b>{s.count()} نفر</b>\n"
        f"{s.player_lines()}\n\n"
        f"📜 قوانین:\n{s.ruleset.describe()}\n"
        f"{DIV}\n"
        f"وضعیت: <b>{ready}</b>"
    )


def lobby_kb(s):
    rows = [
        [B("🎲 انتخاب مود", callback_data="k:mode"),
         B("⏱ زمان", callback_data="k:time")],
    ]
    if s.mode_id == "classic_choice":
        rows.append([B("📂 انتخاب دسته", callback_data="k:cat")])
    rows += [
        [B("⚙ قوانین", callback_data="k:rules")],
        [B(f"👥 عضویت ({s.count()})", callback_data="k:join")],
        [B("▶ شروع بازی", callback_data="k:start"),
         B("❌ لغو", callback_data="k:cancel")],
    ]
    return M(rows)

# ---- انتخاب مود ----
def mode_text():
    return "🎲 <b>انتخاب مود بازی</b>\n" + DIV + "\nیکی رو انتخاب کن:"


def mode_kb(current):
    rows = []
    for mid in MODE_ORDER:
        meta = mode_meta(mid)
        mark = "◉" if mid == current else "◯"
        rows.append([B(f"{mark} {meta['emoji']} {meta['name']}",
                       callback_data=f"k:setmode:{mid}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- انتخاب زمان ----
def time_text(s):
    return ("⏱ <b>زمان مسابقه</b>\n" + DIV +
            "\nمسابقه بعد از پایان این زمان خودکار تموم می‌شه.")


def time_kb(s):
    rows = []
    for sec, lbl in TIME_OPTIONS:
        mark = "◉" if sec == s.time_limit else "◯"
        rows.append([B(f"{mark} {lbl}", callback_data=f"k:settime:{sec}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- سختی (جای خالی) ----
def difficulty_kb(s):
    rows = []
    for key, lbl in DIFFICULTY_OPTIONS:
        mark = "◉" if key == s.difficulty else "◯"
        rows.append([B(f"{mark} {lbl}", callback_data=f"k:setdiff:{key}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- قوانین ----
TOGGLEABLE = ["min_len", "max_len", "exact_len", "starts_with", "ends_with",
              "must_contain", "must_not_contain", "time_limit", "bonus", "no_disturb"]

def rules_text():
    return ("⚙ <b>قوانین بازی</b>\n" + DIV +
            "\nقوانین دلخواه رو روشن/خاموش کن.\n"
            "<i>(در مود «قوانین متغیر» قوانین هر دور خودکار انتخاب می‌شن.)</i>")


def rules_kb(s):
    rows = []
    for rid in TOGGLEABLE:
        cls = REGISTRY[rid]
        on = s.ruleset.is_active(rid)
        mark = "✅" if on else "▫️"
        rows.append([B(f"{mark} {cls.label}", callback_data=f"k:toggle:{rid}")])
    rows.append([B("🔙 برگشت", callback_data="k:back")])
    return M(rows)


# ---- نمایش زنده‌ی بازی ----
def live_text(s):
    meta = mode_meta(s.mode_id)
    leader = s.leader()
    leader_line = (f"🥇 صدرنشین: <b>{leader[0]}</b> — {leader[1]} امتیاز"
                   if leader else "🥇 صدرنشین: —")
    cat_line = (
        f"📂 دسته: <b>{s.category}</b>\n"
        if s.category and s.mode_id in ("classic_random", "classic_choice", "variable")
        else ""
    )

    rules_line = ""
    if s.ruleset.rules and s.mode_id in (
        "classic_random",
        "classic_choice",
        "blank",
    ):

        rules_line = f"📜 قوانین:\n{s.ruleset.describe()}\n"
    return (
        f"🎮 <b>مسابقه‌ی کلمو — {meta['emoji']} {meta['name']}</b>\n"
        f"{DIV}\n"
        f"{cat_line}{rules_line}"
        f"👥 بازیکنان: <b>{s.count()}</b>\n"
        f"⏱ باقی‌مانده: <b>{s.remaining_label()}</b>\n"
        f"{leader_line}\n"
        f"{DIV}\n"
        f"<b>{prompt_of(s)}</b>"
    )


def prompt_of(s):
    if s.question and "prompt" in s.question:
        return s.question["prompt"]
    return "در حال آماده‌سازی…"


def running_kb(s=None):
    return M([[B("🏁 پایان مسابقه", callback_data="k:end")]])


# ---- تنظیمات گروه (حالت تمرکز) ----
def settings_text(s):
    state = "روشن ✅" if s.focus_mode else "خاموش ▫️"
    return ("🧹 <b>تنظیمات مسابقه</b>\n" + DIV +
            f"\nحالت تمرکز: <b>{state}</b>\n"
            "<i>وقتی روشنه، پیام‌های نامرتبط هنگام مسابقه پاک می‌شن.</i>")


def settings_kb(s):
    label = "🧹 خاموش‌کردن حالت تمرکز" if s.focus_mode else "🧹 روشن‌کردن حالت تمرکز"
    return M([[B(label, callback_data="k:focus")],
             [B("🔙 برگشت", callback_data="k:back")]])


def category_text():
    return (
        "📂 <b>انتخاب دسته</b>\n"
        + DIV +
        "\nیک دسته برای مسابقه انتخاب کن."
    )

def category_kb(categories, current=None, page=0, per_page=8):
    total = len(categories)
    start = page * per_page
    shown = categories[start:start + per_page]

    rows = []

    for cat, cnt in shown:
        mark = "◉" if cat == current else "◯"
        rows.append([
            B(
                f"{mark} {cat} ({cnt})",
                callback_data=f"k:setcat:{page}:{cat}"
            )
        ])

    nav = []

    if page > 0:
        nav.append(
            B("⬅ قبلی", callback_data=f"k:catpage:{page-1}")
        )

    if start + per_page < total:
        nav.append(
            B("بعدی ➡", callback_data=f"k:catpage:{page+1}")
        )

    if nav:
        rows.append(nav)

    rows.append([
        B("🔙 برگشت", callback_data="k:back")
    ])

    return M(rows)

def answer_ok_text(score, found, total):
    total = max(1, int(total or 0))
    found = max(0, min(int(found or 0), total))

    pct = round(found * 100 / total)

    filled = round(pct / 100 * 16)

    bar = (
        "█" * filled +
        "░" * (16 - filled)
    )

    return (
        f"✅ درست\n"
        f"⭐ امتیاز: {score}\n\n"
        f"{bar} {pct}%\n\n"
        f"{found} / {total}"
    )


def finish_text(s, reason=None):
    import html
    import time

    ranking = s.ranking()

    duration = 0
    if s.started_at:
        duration = max(
            0,
            int(time.time() - s.started_at)
        )

    m, sec = divmod(duration, 60)

    lines = [
        "🏆 <b>رتبه‌بندی</b>",
        DIV,
    ]

    if not ranking:
        lines.append("امتیازی ثبت نشد.")

    for i, (uid, info) in enumerate(ranking, 1):
        name = html.escape(
            info.get("name", "بازیکن")
        )

        score = int(info.get("score", 0))
        ok = int(s.correct_by_user.get(uid, 0))
        bad = int(s.wrong_by_user.get(uid, 0))

        lines.append(f"{i}. <b>{name}</b>")
        lines.append(f"⭐ امتیاز: {score}")
        lines.append(f"📊 درست: {ok}   ❌ اشتباه: {bad}")

    lines += [
        DIV,
        f"⏱ مدت مسابقه: <b>{m:02d}:{sec:02d}</b>",
        f"📂 دسته مسابقه: <b>{html.escape(s.category or '—')}</b>",
    ]

    return "\n".join(lines)

def namefamily_category_text():
    return (
        "✍️ <b>انتخاب دسته‌های اسم‌وفامیل</b>\n"
        + DIV +
        "\n\n"
        "هر تعداد دسته خواستی انتخاب کن.\n"
        "اگر هیچ دسته‌ای انتخاب نکنی، ربات ۶ دسته تصادفی انتخاب می‌کند."
    )

def namefamily_category_kb(s):
    cats = db.list_categories()
    rows = []
    for cat, cnt in cats:
        mark = "✅" if cat in s.namefamily_categories else "⬜"
        rows.append([
            B(f"{mark} {cat}", callback_data=f"k:nftoggle:{cat}")
        ])

    rows.append([B("🎲 انتخاب تصادفی", callback_data="k:nfrandom")])
    rows.append([B("✅ تایید", callback_data="k:nfdone")])
    rows.append([B("◀ بازگشت", callback_data="k:back")])

    return M(rows)
```


================================================================================
FILE: ui\persona.py
================================================================================

```py
# -*- coding: utf-8 -*-
"""شخصیت Kalemo (کلمو). همه متن‌ها از اینجا می‌آیند تا لحن یکپارچه بماند."""
import random

NAME = "کلمو"
LINES = {
    "welcome": [
        "سلام رفیق! 👋 من {name}‌ام، استاد بازی‌های کلمه‌ای.\n",
        "به‌به! یه بازیکن تازه‌نفس 🔥 من {name}‌ام؛ بزن بریم ببینم چند مرده حلاجی!",
    ],
    "win": [
        "🏆 ایوللل! بردی رفیق! مغزت داره دود می‌کنه از بس تیزه 🔥",
        "🏆 برنده شدی! اعتراف می‌کنم، ازت انتظار نداشتم انقدر بترکونی 😎",
        "🏆 قهرمااان! اسمتو با خط درشت می‌نویسم رو تابلوی افتخار ✨",
    ],
    "lose": [
        "😅 این دور رو باختی، ولی بین خودمون بمونه... نزدیک بود! دوباره؟",
        "💔 آخ! این یکی نشد. ولی قهرمان واقعی کسیه که پا می‌شه. یالا یه دور دیگه!",
        "🙃 باختی، ولی من بهت ایمان دارم. دفعه بعد جبران می‌کنی، مطمئنم.",
    ],
    "record": [
        "🚀 رکورد جدیییید! این بهترین اجرای تاریخته! غوغا کردی 🎉",
        "🌟 رکوردتو شکستی! انگار امروز روز توئه. حالا حالاها کسی بهت نمی‌رسه!",
    ],
    "levelup": [
        "🎚 لِوِل آپ! رسیدی به سطح {level}! داری حرفه‌ای می‌شی ها 😍",
        "✨ سطح {level} باز شد! یه پله بالاتر، یه ذره خفن‌تر 🔝",
    ],
    "daily_login": [
        "🎁 خوش اومدی! جایزه ورود امروزت: {coins} سکه 🪙\nاستریکت شد {streak} روز! 🔥",
        "☀️ سلام به روی ماهت! {coins} سکه گرفتی و {streak} روزه که پیداتو می‌کنی 🔥",
    ],
    "streak_break": ["😢 آخی، استریکت پاره شد! اشکال نداره، از امروز دوباره شروع می‌کنیم 💪"],
    "mission_done": [
        "✅ مأموریت انجام شد! بیا جایزتو بگیر: {reward} 🎉",
        "🎯 ماموریت تیک خورد! {reward} مال خودت. کارت درسته!",
    ],
    "error": [
        "🤖 اوپس! یه چیزی قاطی شد. دوباره امتحان کن رفیق.",
        "😬 یه لحظه قاطی کردم! یه بار دیگه بزن لطفاً.",
    ],
    "game_start": [
        "🎬 بازی شروع شد! کمربندا رو ببندین 🔥",
        "🚦 سه، دو، یک... بریییم! 🏁",
    ],
    "game_end": ["🎬 و... تمام! دمتون گرم، ترکوندین 👏"],
    "timeout": [
        "⏰ وقت تموم شد! این دفعه سریع‌تر، باشه؟ 😉",
        "⏰ زمان پرید رفت! اشکال نداره، دور بعد جبران کن.",
    ],
    "good_answer": [
        "✅ دمت گرم! +{pts}",
        "✅ آفرین! دقیقاً همینو می‌خواستم. +{pts}",
        "✅ نــایس! +{pts} 🔥",
    ],
}

def say(key, **kwargs):
    options = LINES.get(key, LINES["error"])
    text = random.choice(options)
    kwargs.setdefault("name", NAME)
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError):
        return text

```


================================================================================
FILE: web.py
================================================================================

```py
from flask import Flask
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "Kalemo Bot is alive!", 200

def run():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
```

