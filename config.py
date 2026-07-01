# -*- coding: utf-8 -*-
"""پیکربندی Kalemo (کلمو).
مقادیر حساس از متغیرهای محیطی خوانده می‌شوند تا توکن داخل کد قرار نگیرد.
"""
import os
from dotenv import load_dotenv
# توکن ربات (از @BotFather) — حتماً به‌صورت متغیر محیطی ست شود.
load_dotenv()
BOT_TOKEN = os.getenv("KALEMO_BOT_TOKEN")

# یوزرنیم ربات (بدون @) — برای لینک افزودن به گروه
BOT_USERNAME = os.getenv("KALEMO_BOT_USERNAME")

# آیدی عددی ادمین‌های اصلی (owner). با کاما جدا کنید: "123,456"
ADMIN_IDS = {
    int(x) for x in os.environ.get("KALEMO_ADMINS", "").replace(" ", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

# مسیر دیتابیس
DB_PATH = os.environ.get("KALEMO_DB", "kalemo.db")

# فاصله زمانی مجاز برای تغییر نام نمایشی (ثانیه) — پیش‌فرض ۷ روز
NAME_CHANGE_COOLDOWN = int(os.environ.get("KALEMO_NAME_COOLDOWN", str(7 * 24 * 3600)))
