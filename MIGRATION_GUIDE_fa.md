# مهاجرت کلمو از SQLite به PostgreSQL (روی Render، سازگار با ایران)

این راهنما تمام مراحل انتقال ربات تلگرام «کلمو» از SQLite به PostgreSQL را
قدم‌به‌قدم توضیح می‌دهد. **رفتار و امکانات ربات دقیقاً مثل قبل باقی می‌ماند** —
فقط لایه‌ی ذخیره‌سازی عوض شده است.

---

## ۱) چرا این مهاجرت لازم بود؟

روی پلن رایگان Render، فایل‌سیستم **موقتی (ephemeral)** است. هر بار که سرویس
دیپلوی مجدد یا ری‌استارت می‌شود، فایل `kalemo.db` پاک می‌شود و همه‌ی بازیکن‌ها،
سکه‌ها، پیشرفت‌ها و باغچه‌ها از بین می‌روند. PostgreSQLِ Render یک دیتابیس
**پایدار و جدا از سرویس** است، بنابراین داده‌ها باقی می‌مانند.

---

## ۲) چه چیزهایی تغییر کرد؟

نکته‌ی کلیدی: **تمام کدهای SQLite فقط در دو فایل بودند** — `core/db.py` و
`core/garden_db.py`. بقیه‌ی پروژه (هندلرها، مودهای بازی، UI، سرویس‌ها) همگی
از توابع `db.*` استفاده می‌کنند. چون **امضای همه‌ی توابع عمومی بدون تغییر مانده**،
هیچ فایل دیگری نیاز به تغییر ندارد.

فایل‌هایی که باید جایگزین شوند:

| فایل | تغییر |
| --- | --- |
| `core/db.py` | بازنویسی کامل با psycopg ۳ + connection pool |
| `core/garden_db.py` | بازنویسی کامل با psycopg ۳ |
| `config.py` | افزودن `DB_POOL_MAX` و توضیح `DATABASE_URL` |
| `requirements.txt` | حذف `SQLAlchemy` (استفاده نمی‌شد)، افزودن `psycopg-pool` |

فایل‌هایی که **دست‌نخورده** می‌مانند: `main.py`, `web.py`, همه‌ی `handlers/*`,
همه‌ی `game/*`, `features/*`, `ui/*`, `seeds.py`, `runtime.txt`.

### جزئیات فنی تغییرات کد

| مورد در SQLite | معادل در PostgreSQL |
| --- | --- |
| `sqlite3.connect(path)` | `ConnectionPool(DATABASE_URL)` |
| `row_factory = sqlite3.Row` | `row_factory = dict_row` |
| placeholder `?` | placeholder `%s` |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY` |
| `INTEGER` برای `user_id/chat_id` | `BIGINT` (آی‌دی‌های تلگرام بزرگ‌اند) |
| `cursor.lastrowid` | `INSERT ... RETURNING id` سپس `fetchone()["id"]` |
| `INSERT OR IGNORE` | `INSERT ... ON CONFLICT DO NOTHING` |
| `executescript(...)` | `execute(...)` (psycopg چند دستور را می‌پذیرد) |
| `sqlite3.IntegrityError` | `psycopg.errors.UniqueViolation` (به‌صورت `db.IntegrityError` هم در دسترس است) |
| `PRAGMA table_info(t)` | کوئری روی `information_schema.columns` |
| `PRAGMA foreign_keys=ON` | حذف شد (کلیدهای خارجی در Postgres به‌صورت پیش‌فرض اعمال می‌شوند) |

> نکته درباره‌ی UPSERT: در Postgres وقتی در بخش `DO UPDATE` به ستونی ارجاع می‌دهید
> که هم در جدول و هم در ردیف جدید وجود دارد، باید نام جدول را ذکر کنید
> (مثلاً `garden_seeds.qty + 1` به‌جای `qty + 1`). این نکته اصلاح شده است.

### سازگاری با کد قدیمی
اگر جایی در پروژه `sqlite3.IntegrityError` را می‌گرفتید، حالا از `db.IntegrityError`
استفاده کنید (که به `psycopg.errors.UniqueViolation` اشاره می‌کند). در کد فعلی شما
این خطا فقط داخل خودِ `db.py` استفاده می‌شود، پس نیازی به تغییر جای دیگری نیست.

---

## ۳) داده‌های موجود

شما انتخاب کردید که با یک **دیتابیس خالی تازه** شروع کنید. با اولین اجرا،
تابع `db.init()` (که در `build_app()` داخل `main.py` صدا زده می‌شود) به‌صورت
خودکار جدول‌ها را می‌سازد و بانک کلمات NameFamily و دسته‌های پیش‌فرض را seed می‌کند.
پس نیازی به هیچ اسکریپت انتقال داده نیست. (بازیکن‌ها و پیشرفت‌ها از صفر شروع می‌شوند.)

---

## ۴) چرا این گزینه از ایران کار می‌کند؟

- **دیتابیس روی خود Render** است و از طریق **Internal Database URL** به سرویس ربات
  وصل می‌شود. این اتصال داخل شبکه‌ی Render برقرار می‌شود، نه از داخل ایران.
- شما فقط برای **دیپلوی و مدیریت پنل Render** به اینترنت وصل می‌شوید؛ اگر
  render.com برایتان باز نشد، از یک VPN/پروکسی فقط برای باز کردن پنل استفاده کنید.
  خودِ ربات و دیتابیس در دیتاسنتر Render اجرا می‌شوند و به فیلترینگ ایران ربطی ندارند.
- سرویس‌هایی که معمولاً از ایران دردسر دارند (مثلاً بعضی پنل‌های ابری) اینجا دور
  زده می‌شوند چون کل استک روی Render است.

---

## ۵) ساخت دیتابیس PostgreSQL روی Render

1. وارد داشبورد Render شوید → **New +** → **PostgreSQL**.
2. یک نام بگذارید (مثلاً `kalemo-db`)، **Region** را همان ریجن سرویس ربات انتخاب کنید
   (مهم است که هر دو در یک ریجن باشند تا Internal URL کار کند)، و **Free** را انتخاب کنید.
3. **Create Database** را بزنید و چند دقیقه صبر کنید تا وضعیت «Available» شود.
4. وارد صفحه‌ی دیتابیس شوید → بخش **Connections**:
   - **Internal Database URL** را کپی کنید (این را برای سرویس ربات استفاده می‌کنیم).
   - (اختیاری) **External Database URL** برای اتصال از بیرون است.

> ⚠️ دیتابیس رایگان Render بعد از **۳۰ روز منقضی** می‌شود. برای پروژه‌ی جدی
> بعداً پلن پولی بگیرید یا از Neon (رایگان و بدون انقضا) استفاده کنید. اگر روزی
> به Neon رفتید، فقط کافی است `DATABASE_URL` را عوض کنید؛ کد بدون تغییر کار می‌کند.
> (برای Neon معمولاً باید `?sslmode=require` به انتهای URL اضافه شود.)

---

## ۶) تنظیم متغیرهای محیطی

### روی Render (سرویس ربات — Web Service)
وارد سرویس ربات شوید → **Environment** → **Add Environment Variable** و این‌ها را بسازید:

| Key | Value |
| --- | --- |
| `DATABASE_URL` | همان **Internal Database URL** که کپی کردید |
| `KALEMO_BOT_TOKEN` | توکن ربات از @BotFather |
| `KALEMO_BOT_USERNAME` | یوزرنیم ربات بدون @ |
| `KALEMO_ADMINS` | آیدی عددی ادمین‌ها، با کاما: `1053046454` |
| `DB_POOL_MAX` | `5` (اختیاری) |

`PORT` را Render خودش ست می‌کند؛ لازم نیست دستی بگذارید (کد از `os.environ["PORT"]` می‌خواند).

### روی سیستم خودتان (تست محلی)
یک فایل `.env` کنار پروژه بسازید:

```
KALEMO_BOT_TOKEN=123:ABC
KALEMO_BOT_USERNAME=KalemoBot
KALEMO_ADMINS=1053046454
DATABASE_URL=postg://user:pass@host:5432/dbname
DB_POOL_MAX=5
```

> اگر می‌خواهید محلی هم واقعاً به Postgress وصل شوید، می‌توانید External Database URL
> را در `.env` بگذارید. فایل `.env` را حتماً در `.gitignore` قرار دهید.

---

## ۷) جایگزینی فایل‌ها

فایل‌های موجود در پوشه‌ی `kalemo_pg/` را جایگزین فایل‌های هم‌نام در پروژه کنید:

```
kalemo_pg/config.py            →  config.py
kalemo_pg/requirements.txt     →  requirements.txt
kalemo_pg/core/db.py           →  core/db.py
kalemo_pg/core/garden_db.py    →  core/garden_db.py
```

بقیه‌ی فایل‌ها را دست نزنید.

---

## ۸) دیپلوی روی Render

تنظیمات سرویس ربات (Web Service):

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python main.py`
- **Instance Type:** Free

بعد از set کردن متغیرهای محیطی، **Manual Deploy → Deploy latest commit** را بزنید.
با اولین اجرا، `db.init()` جدول‌ها را می‌سازد و کلمات را seed می‌کند. در لاگ باید ببینید:

```
Menu button & commands registered.
Kalemo is running…
```

اگر `DATABASE_URL` ست نشده باشد، ربات با پیام واضح
«DATABASE_URL تنظیم نشده است...» بالا نمی‌آید — این عمدی است تا زود متوجه شوید.

---

## ۹) بررسی سلامت

- به آدرس سرویس (`https://<your-service>.onrender.com/`) بروید؛ باید ببینید
  `Kalemo Bot is alive!`.
- در تلگرام `/start` بزنید؛ باید پروفایل و منو بیاید.
- یک بازی گروهی یا NameFamily انجام دهید و مطمئن شوید سکه/XP ذخیره می‌شود.
- سرویس را یک‌بار **Restart** کنید و دوباره `/profile` بزنید — این بار **داده‌ها
  باقی می‌مانند** (که کل هدف مهاجرت بود). ✅

---

## ۱۰) استفاده از Supabase به‌جای Render Postgres

خبر خوب: کدی که نوشتیم (`core/db.py`) از هر آدرس استاندارد PostgreSQL پشتیبانی
می‌کند، پس نیازی به تغییر هیچ کد دیگری نیست — فقط کافی‌ست `DATABASE_URL` درست
تنظیم شود. اما ⚠️ **نکته‌ی خیلی مهم**: کانکشن‌استرینگی که فرستادید
(`db.pzalhcdhctrzesxqsyvz.supabase.co:5432`) آدرس **مستقیم (Direct Connection)**
سوپابیس است که این روزها **فقط IPv6** است. سرورهای Render (پلن رایگان) از
شبکه‌ی IPv4 خارج می‌شوند، پس این اتصال معمولاً با خطای timeout/connection refused
روی Render شکست می‌خورد. راه‌حل: به‌جای Direct Connection از **Connection Pooler**
سوپابیس استفاده کنید که IPv4-friendly است.

### ۱۰.۱) گرفتن آدرس درست از پنل Supabase

1. وارد پروژه‌ی Supabase شوید → **Project Settings** (⚙️) → **Database**.
2. بخش **Connection string** را باز کنید.
3. تب **Connection pooling** را انتخاب کنید (نه «Direct connection»).
4. حالت (Mode) را روی **Session** بگذارید (چون کد ما هر بار یک تراکنش کامل
   با `commit/rollback` انجام می‌دهد و به‌جز pool خودمان، pooler هم لازم داریم؛
   Session mode برای این الگو مطمئن‌تر است. اگر بعداً خواستید Transaction mode
   را هم امتحان کنید مشکلی نیست).
5. آدرسی که نشان داده می‌شود شبیه این است (پورت و هاست فرق می‌کند با Direct):

   ```
   postgresql://postgres.pzalhcdhctrzesxqsyvz:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

   (دقت کنید: یوزرنیم اینجا به‌صورت `postgres.<project-ref>` است، نه فقط `postgres`.
   هاست هم `pooler.supabase.com` است، نه `supabase.co`.)

6. به‌جای `[YOUR-PASSWORD]` پسورد دیتابیسی که هنگام ساخت پروژه تعیین کردید را
   بگذارید. اگر یادتان نیست، در همان صفحه دکمه‌ی **Reset database password**
   هست.

7. در انتهای این آدرس عبارت `?sslmode=require` را اضافه کنید (Supabase روی
   اتصال بدون SSL را می‌بندد):

   ```
   postgresql://postgres.pzalhcdhctrzesxqsyvz:MyStrongPass123@aws-0-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=require
   ```

### ۱۰.۲) ست‌کردن در Render

وارد سرویس ربات در Render شوید → **Environment** → مقدار `DATABASE_URL` را
دقیقاً با همین آدرس نهایی (پولر + پسورد واقعی + `?sslmode=require`) جایگزین کنید.
بقیه‌ی متغیرها (`KALEMO_BOT_TOKEN`, `KALEMO_BOT_USERNAME`, `KALEMO_ADMINS`,
`DB_POOL_MAX`) همان‌طور که در بخش ۶ گفته شد باقی می‌مانند.

> اگر پسورد شما در خودش کاراکترهای خاص دارد (مثل `@`, `:`, `/`, `#`) باید
> URL-encode شود، وگرنه اتصال parse نمی‌شود. مثلاً `@` می‌شود `%40`.
> ساده‌ترین راه: یک پسورد ساده‌ی فقط حروف/عدد برای دیتابیس بسازید تا این مشکل
> پیش نیاید (از همان دکمه‌ی Reset database password).

### ۱۰.۳) دیپلوی و تست

1. **Manual Deploy → Deploy latest commit** را در Render بزنید.
2. در لاگ سرویس دنبال خط `Kalemo is running…` بگردید؛ اگر خطای اتصال دیدید
   (`could not translate host name` یا `timeout`) یعنی هنوز از Direct Connection
   استفاده می‌کنید — به قدم ۱۰.۱ برگردید و مطمئن شوید از آدرس Pooler استفاده
   کرده‌اید.
3. در تلگرام `/start` بزنید و یک بازی انجام دهید.
4. برای اطمینان از ماندگاری داده: در پنل Supabase → **Table Editor** بروید و
   جدول `players` را باز کنید؛ باید ردیف بازیکن‌تان را ببینید.
5. سرویس Render را **Restart** کنید و دوباره `/profile` بزنید — چون Supabase
   منقضی نمی‌شود، **هیچ‌وقت** داده پاک نمی‌شود، حتی بعد از دیپلوی‌های مکرر. ✅

### ۱۰.۴) چرا Supabase انتخاب خوبی است
- دیتابیس رایگان Supabase **منقضی نمی‌شود** (برخلاف Render Postgres رایگان که
  بعد از ۳۰ روز پاک می‌شود) — پروژه فقط اگر ۷ روز کامل هیچ فعالیتی نداشته باشد
  به حالت Pause می‌رود که با یک بازدید از پنل یا اولین ریکوئست دوباره فعال می‌شود.
- از ایران معمولاً در دسترس است (چون فقط سرویس ربات روی Render با آن صحبت
  می‌کند، نه دستگاه شما مستقیماً).
- کد فعلی نیازی به هیچ تغییری برای Supabase نداشت؛ فقط `DATABASE_URL` عوض شد.

---

## ۱۱) اشکال‌زدایی

| علامت | علت محتمل | راه‌حل |
| --- | --- | --- |
| `RuntimeError: DATABASE_URL تنظیم نشده` | متغیر محیطی ست نشده | در Environment سرویس، `DATABASE_URL` را بگذارید |
| `connection refused` / timeout | استفاده از External URL یا ریجن متفاوت | از **Internal** URL و همان ریجن استفاده کنید |
| `SSL required` | provider خارجی (مثل Neon) | به انتهای URL `?sslmode=require` اضافه کنید |
| `too many connections` | pool بزرگ | `DB_POOL_MAX` را کم کنید (مثلاً `3`) |
| جدول‌ها ساخته نمی‌شوند | `db.init()` صدا زده نشده | مطمئن شوید `main.py` بدون خطا `build_app()` را اجرا می‌کند |
| `could not translate host name "db.xxx.supabase.co"` | استفاده از Direct Connection که IPv6-only است | آدرس **Pooler** (`*.pooler.supabase.com`) را از بخش ۱۰ استفاده کنید |
| `password authentication failed` | پسورد اشتباه یا کاراکتر خاص URL-encode نشده | پسورد را Reset کنید و یک پسورد ساده (فقط حروف/عدد) بگذارید |
| `SSL connection required` | فراموش‌کردن `?sslmode=require` | به انتهای `DATABASE_URL` این را اضافه کنید |
