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
