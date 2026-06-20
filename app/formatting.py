"""
formatting.py — Output-mode handling and TTS-friendly text rendering.

MedBook can reply in two modes:

  * ``ui``  — rich text for a screen: markdown, emoji, formatted slot lists,
              raw booking IDs and digits are all fine.
  * ``tts`` — clean spoken text for a text-to-speech engine: no markdown, no
              emoji, and every number/time/price/date spelled out as words so
              the engine pronounces them naturally instead of reading symbols.

The system prompt nudges the model toward the right style (see
``app.assistant.build_system_prompt``), but ``to_tts`` is the deterministic
guarantee: it rewrites whatever the model produced into spoken-friendly text.
"""

from __future__ import annotations

import re

from num2words import num2words

# ── Output modes ────────────────────────────────────────────────────────────
UI = "ui"
TTS = "tts"
_VALID_MODES = {UI, TTS}


def normalize_mode(value: str | None) -> str:
    """Coerce an arbitrary mode string to a known mode, defaulting to ``ui``."""
    if value and value.strip().lower() in _VALID_MODES:
        return value.strip().lower()
    return UI


# ── Digit helpers ───────────────────────────────────────────────────────────
_DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _digit_by_digit(digits: str) -> str:
    """'9876' -> 'nine eight seven six' (for phone numbers / long IDs)."""
    return " ".join(_DIGIT_WORDS[d] for d in digits if d in _DIGIT_WORDS)


def _cardinal(n: int | float) -> str:
    return num2words(n)


# ── Markdown / emoji stripping ──────────────────────────────────────────────
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")          # [label](url) -> label
_BARE_URL = re.compile(r"https?://\S+")
_HEADER = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)  # # Heading
_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"[*`#]")                          # leftover * ` #

# Common emoji / pictograph unicode ranges.
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # symbols & pictographs, supplemental, emoji
    "\U00002600-\U000027BF"   # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "\U00002190-\U000021FF"   # arrows
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U00002B00-\U00002BFF"   # misc symbols & arrows
    "\U0000200D"              # zero-width joiner
    "]+",
    flags=re.UNICODE,
)


# ── Number / time / currency / date patterns ────────────────────────────────
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\s*([AaPp][Mm])?")
_HOUR_MERIDIEM = re.compile(r"\b(\d{1,2})\s*([AaPp][Mm])\b")
_CURRENCY = re.compile(
    r"(?:₹|\bRs\.?|\$)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_LONG_DIGITS = re.compile(r"\+?\d[\d\s-]{5,}\d")   # 7+ digit runs: phones / IDs
_ORDINAL = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)
_PERCENT = re.compile(r"\b(\d+(?:\.\d+)?)\s*%")
_DECIMAL = re.compile(r"\b\d+\.\d+\b")
_INTEGER = re.compile(r"\b\d+\b")


def _meridiem(token: str | None) -> str:
    return f" {token.upper()}" if token else ""


def _say_time(m: re.Match) -> str:
    hour, minute, mer = int(m.group(1)), int(m.group(2)), m.group(3)
    hour_word = _cardinal(hour)
    if minute == 0:
        body = hour_word
    elif minute < 10:
        body = f"{hour_word} oh {_cardinal(minute)}"
    else:
        body = f"{hour_word} {_cardinal(minute)}"
    return f"{body}{_meridiem(mer)}"


def _say_hour_meridiem(m: re.Match) -> str:
    return f"{_cardinal(int(m.group(1)))}{_meridiem(m.group(2))}"


def _say_currency(m: re.Match) -> str:
    raw = m.group(0)
    amount = m.group(1)
    unit = "dollars" if "$" in raw else "rupees"
    subunit = "cents" if unit == "dollars" else "paise"
    if "." in amount:
        whole, frac = amount.split(".", 1)
        frac = (frac + "00")[:2]  # normalise to 2 digits
        return f"{_cardinal(int(whole))} {unit} and {_cardinal(int(frac))} {subunit}"
    return f"{_cardinal(int(amount))} {unit}"


def _say_date(m: re.Match) -> str:
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    month_name = _MONTHS[month - 1] if 1 <= month <= 12 else _cardinal(month)
    day_ord = num2words(day, to="ordinal")
    year_words = num2words(year, to="year")
    return f"{month_name} {day_ord}, {year_words}"


def _say_long_digits(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return _digit_by_digit(digits)


def _say_ordinal(m: re.Match) -> str:
    return num2words(int(m.group(1)), to="ordinal")


def _say_percent(m: re.Match) -> str:
    value = m.group(1)
    n = float(value) if "." in value else int(value)
    return f"{_cardinal(n)} percent"


def _numbers_to_words(text: str) -> str:
    # Order matters: specific composite patterns first, generic last.
    text = _DATE.sub(_say_date, text)
    text = _TIME.sub(_say_time, text)
    text = _HOUR_MERIDIEM.sub(_say_hour_meridiem, text)
    text = _CURRENCY.sub(_say_currency, text)
    text = _LONG_DIGITS.sub(_say_long_digits, text)
    text = _ORDINAL.sub(_say_ordinal, text)
    text = _PERCENT.sub(_say_percent, text)
    text = _DECIMAL.sub(lambda m: _cardinal(float(m.group(0))), text)
    text = _INTEGER.sub(lambda m: _cardinal(int(m.group(0))), text)
    return text


def _join_lines(text: str) -> str:
    """Flatten multi-line / list output into sentence flow for speech."""
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if parts and parts[-1][-1] not in ".!?:,;":
            parts[-1] += "."
        parts.append(line)
    return " ".join(parts)


_WS = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?;:])")


def to_tts(text: str) -> str:
    """Rewrite assistant text into clean, spoken-friendly TTS output.

    Strips markdown and emoji, flattens lists into sentences, and spells every
    number, time, price, and date as words. Conservative by design: only
    formatting markers and digit-bearing tokens are transformed, so ordinary
    prose passes through intact.
    """
    if not text:
        return ""

    text = _MD_LINK.sub(r"\1", text)
    text = _BARE_URL.sub("", text)
    text = _HEADER.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _LIST_MARKER.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = _EMOJI.sub("", text)

    text = _join_lines(text)
    text = _numbers_to_words(text)

    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _WS.sub(" ", text).strip()
    return text
