"""Input / output guardrails that run outside the model.

These are cheap deterministic checks. The intent classifier adds a model-based
safety label on top; the two are combined in the orchestrator.
"""

import re
from dataclasses import dataclass, field

_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PASSWORD_RE = re.compile(r"(?i)(нууц\s*үг|password|nuuts\s*ug|pin)\s*[:=]?\s*\S+")
# Mongolian citizen registration number: two Cyrillic letters + 8 digits.
_REGNO_RE = re.compile(r"\b[А-ЯӨҮа-яөү]{2}\s?\d{8}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?976[ -]?)?[5-9]\d{3}[ -]?\d{4}(?!\d)")
_SECRET_RE = re.compile(r"(sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})")

_INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|rules)",
    r"disregard (the )?(system|previous) (prompt|instructions)",
    r"(reveal|show|print|repeat) (me )?(your|the) (system prompt|instructions|rules)",
    r"you are now (?!.*customer)",
    r"developer mode|jailbreak|DAN mode",
    r"</?(system|instructions?)>",
    r"өмнөх (бүх )?(заав|дүр)\S* (үл тоо|март|бүү дага)",
    r"систем(ийн)? (промпт|заав)\S* (харуул|хэл|бич)",
    r"дотоод (мэдээл|заав)\S* (бүгдийг )?(харуул|өг)",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.IGNORECASE)


@dataclass
class InputCheck:
    text: str  # text forwarded to the model (secrets masked)
    flags: list[str] = field(default_factory=list)
    blocked: bool = False


def check_input(text: str, max_chars: int) -> InputCheck:
    flags: list[str] = []
    t = text.strip()
    if not t:
        return InputCheck(text="", flags=["empty"], blocked=True)
    if len(t) > max_chars:
        t = t[:max_chars]
        flags.append("truncated")
    if _CARD_RE.search(t):
        t = _CARD_RE.sub("[КАРТЫН ДУГААР]", t)
        flags.append("card_number_masked")
    if _PASSWORD_RE.search(t):
        t = _PASSWORD_RE.sub(lambda m: f"{m.group(1)}: [НУУЦЛАГДСАН]", t)
        flags.append("password_masked")
    if _INJECTION_RE.search(t):
        flags.append("injection_heuristic")
    return InputCheck(text=t, flags=flags)


def redact_for_logs(text: str) -> str:
    """PII-minimized copy for analytics storage and traces."""
    t = _CARD_RE.sub("[CARD]", text)
    t = _REGNO_RE.sub("[REGNO]", t)
    t = _PHONE_RE.sub("[PHONE]", t)
    t = _SECRET_RE.sub("[SECRET]", t)
    return t


def scrub_output(text: str) -> str:
    """Last-line filter on streamed model output."""
    return _SECRET_RE.sub("[НУУЦЛАГДСАН]", text)
