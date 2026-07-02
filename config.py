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
