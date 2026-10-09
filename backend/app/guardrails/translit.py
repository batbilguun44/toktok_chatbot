"""Mongolian script normalization.

Users often type Mongolian with Latin letters ("zahialga hezee ireh ve",
"zakhialga", "huleelgiin hugatsaa"). Spelling is informal and lossy (ө/о, ү/у
both become o/u), so for keyword search we map Cyrillic and Latin text into the
same lossy "phonetic key" space instead of trying to restore exact Cyrillic.
The LLM classifier additionally rewrites the query into proper Cyrillic.
"""

import re

_CYR_TO_KEY = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "o", "ж": "j",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "ө": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ү": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}

# Order matters: multi-letter Latin spellings first.
_LATIN_TO_KEY = [
    ("kh", "h"), ("x", "h"), ("w", "v"), ("q", "o"), ("ö", "o"), ("ü", "u"),
    ("ye", "e"), ("yo", "o"), ("ii", "ii"), ("y", "i"),
    ("c", "ts"), ("tss", "ts"),
]

# Best-effort Latin -> Cyrillic for display / LLM hints (ambiguous by nature).
_LATIN_TO_CYR = [
    ("sh", "ш"), ("ch", "ч"), ("ts", "ц"), ("kh", "х"), ("ya", "я"), ("yu", "ю"),
    ("yo", "ё"), ("ye", "е"), ("ai", "ай"), ("ei", "эй"), ("ii", "ий"), ("oi", "ой"),
    ("ui", "уй"), ("a", "а"), ("b", "б"), ("v", "в"), ("w", "в"), ("g", "г"),
    ("d", "д"), ("e", "э"), ("z", "з"), ("i", "и"), ("k", "к"), ("l", "л"),
    ("m", "м"), ("n", "н"), ("o", "о"), ("ö", "ө"), ("q", "ө"), ("p", "п"),
    ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("ü", "ү"), ("f", "ф"),
    ("h", "х"), ("x", "х"), ("j", "ж"), ("c", "ц"), ("y", "ы"),
]

_LATIN_RE = re.compile(r"[a-zA-Z]")
_CYR_RE = re.compile(r"[а-яА-ЯөӨүҮёЁ]")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def script_of(text: str) -> str:
    """'cyrillic', 'latin', 'mixed' or 'none'."""
    lat = len(_LATIN_RE.findall(text))
    cyr = len(_CYR_RE.findall(text))
    if not lat and not cyr:
        return "none"
    if cyr and lat:
        return "cyrillic" if cyr >= 3 * lat else "latin" if lat >= 3 * cyr else "mixed"
    return "cyrillic" if cyr else "latin"


def phonetic_key(text: str) -> str:
    """Lossy normalization shared by Cyrillic and Latin-transliterated Mongolian."""
    out = []
    for ch in text.lower():
        out.append(_CYR_TO_KEY.get(ch, ch))
    s = "".join(out)
    for src, dst in _LATIN_TO_KEY:
        s = s.replace(src, dst)
    # Collapse doubled vowels used inconsistently in Latin spelling ("zahialgaa").
    s = re.sub(r"([aeiou])\1+", r"\1", s)
    return s


def key_tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(phonetic_key(text))


def latin_to_cyrillic(text: str) -> str:
    """Best-effort transliteration; used as a hint, never as ground truth."""
    result = []
    for word in re.split(r"(\W+)", text):
        if not _LATIN_RE.search(word):
            result.append(word)
            continue
        w, i, buf = word.lower(), 0, []
        while i < len(w):
            for src, dst in _LATIN_TO_CYR:
                if w.startswith(src, i):
                    buf.append(dst)
                    i += len(src)
                    break
            else:
                buf.append(w[i])
                i += 1
        result.append("".join(buf))
    return "".join(result)
