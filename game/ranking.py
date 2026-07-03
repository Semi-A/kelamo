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
