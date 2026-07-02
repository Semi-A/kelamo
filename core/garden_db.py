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
