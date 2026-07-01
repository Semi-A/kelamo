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