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
