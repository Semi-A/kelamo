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