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
    
