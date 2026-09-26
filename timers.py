"""
LOQI — Timers Skill (local countdowns, no threads)

Timers are pure bookkeeping: (label, duration, due) entries in a dict. No
background thread fires the alert, on purpose. TTS playback is not safe to
drive from two threads at once — Kokoro synthesis serializes on a lock but
``sd.play`` does not — so alerts are spoken by the MAIN loop, which polls
``pop_due()`` while it idles between wake words (full mode) or between
inputs (text / no-wake modes). An alert that comes due mid-turn simply waits
for the turn to end, which is also what a polite human does.

Utterance parsing lives here too: duration ("10 minutes", "forty five
seconds", "half an hour", "an hour and a half") and an optional label
("set a tea timer for 3 minutes", "set a timer for 5 minutes for the eggs").

Failures follow the house rule from brain.py: never raise into the caller —
(False, reason) gets spoken. A missing duration is the one exception, raised
as ActionUnavailable from the action layer (see actions.set_timer): "set a
timer" with no length is a request this layer cannot fill, and the LLM
fallback will at least ask how long.
"""

from __future__ import annotations

import itertools
import re
import threading
import time
from dataclasses import dataclass

from logging_setup import get_logger

log = get_logger(__name__)

_MIN_DURATION_S = 1.0
_MAX_DURATION_S = 24 * 3600.0
_MAX_ACTIVE = 8
_MAX_LABEL_WORDS = 3


# --------------------------------------------------------------- durations
_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUM_WORDS: dict[str, float] = {
    **_ONES, **_TEENS, **_TENS, "a": 1.0, "an": 1.0, "half": 0.5,
}

# Number forms: digits ("10"), articles ("an hour"), simple words ("twenty"),
# compounds ("forty five"). "half" takes an optional article so "half an hour"
# is one number (0.5), not "an hour" (1) with "half" silently dropped.
_NUM_WORD_RE = (
    r"half(?:[\s-]+an?)?"
    rf"|(?:(?:{'|'.join(_TENS)})[\s-]+(?:{'|'.join(_ONES)}))"
    rf"|{'|'.join(_TENS)}"
    rf"|{'|'.join(_TEENS)}"
    rf"|{'|'.join(_ONES)}"
    r"|an?"
    r"|\d+(?:\.\d+)?"
)
_DURATION_RE = re.compile(
    rf"(?P<num>\b{_NUM_WORD_RE})[\s-]+(?P<unit>hours?|hrs?|minutes?|mins?|seconds?|secs?)\b",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "hour": 3600.0, "hours": 3600.0, "hr": 3600.0, "hrs": 3600.0,
    "minute": 60.0, "minutes": 60.0, "min": 60.0, "mins": 60.0,
    "second": 1.0, "seconds": 1.0, "sec": 1.0, "secs": 1.0,
}

# "an hour and a half" and friends — consumed before plain findall so the
# "an hour" inside them is not counted a second time.
_HALF_PHRASE_RE = re.compile(
    r"\b(?:(?P<num>\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s+)?"
    r"(?P<unit>hours?|minutes?)\s+and\s+(?:a|an)\s+half\b",
    re.IGNORECASE,
)
# The same idea with the unit at the end: "two and a half hours". A separate
# regex because the words between number and unit differ ("and a half" vs
# "and"), and trying to cover both in one pattern makes it match plain
# "ten and twenty minutes" style noise.
_HALF_BEFORE_RE = re.compile(
    r"\b(?P<num>\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"and\s+a\s+half\s+(?P<unit>hours?|minutes?)\b",
    re.IGNORECASE,
)


def _word_to_number(word: str) -> float:
    """A number word ("seven", "forty five", "half an") to its value."""
    word = word.strip().lower()
    if word.startswith("half"):
        return 0.5
    parts = re.split(r"[\s-]+", word)
    if len(parts) == 2:
        tens = _TENS.get(parts[0])
        ones = _ONES.get(parts[1])
        if tens is not None and ones is not None:
            return float(tens + ones)
    return float(_NUM_WORDS.get(word, 0.0))


def parse_duration(text: str) -> float | None:
    """Total seconds named anywhere in ``text``; None when no duration appears."""
    total = 0.0
    found = False

    def _consume_half(match: re.Match) -> str:
        nonlocal total, found
        num_text = (match.group("num") or "1").lower()
        num = float(num_text) if num_text.isdigit() else _word_to_number(num_text)
        unit = match.group("unit").lower().rstrip("s")
        total += (num + 0.5) * _UNIT_SECONDS[unit]
        found = True
        return " "

    remainder = _HALF_BEFORE_RE.sub(_consume_half, _HALF_PHRASE_RE.sub(_consume_half, text))
    for match in _DURATION_RE.finditer(remainder):
        num_text = match.group("num").lower()
        try:
            num = float(num_text)
        except ValueError:
            num = _word_to_number(num_text)
        total += num * _UNIT_SECONDS[match.group("unit").lower()]
        found = True
    return total if found else None


def _join_spoken(parts: list[str]) -> str:
    """Join phrase parts the way they are spoken: 'a and b', 'a, b and c'."""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def format_duration(total_s: float) -> str:
    """Render seconds as a spoken phrase: 3725 → '1 hour, 2 minutes and 5 seconds'."""
    total = max(0, int(round(total_s)))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds or not parts:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return _join_spoken(parts)


# ------------------------------------------------------------------ labels
# Stripped when looking for a label: command scaffolding, politeness, and the
# wake-word forms Whisper produces (see intents._PREFIX / _SUFFIX).
_LABEL_STOPWORDS = frozenset({
    "set", "start", "create", "make", "timer", "timers", "a", "an", "the",
    "my", "our", "for", "of", "me", "up", "and", "on", "please", "thanks",
    "thank", "you", "hey", "ok", "okay", "loki", "loqi", "low", "key",
    "low-key", "can", "could", "would", "i", "want", "to",
})


def extract_timer_args(text: str) -> tuple[float | None, str]:
    """
    (seconds, label) from a timer-shaped utterance.

    The label is whatever remains after duration spans and scaffolding words
    are removed — "tea" in "set a tea timer for 3 minutes". Capped at a few
    words: a long remainder is a compound sentence, not a name.
    """
    seconds = parse_duration(text)
    stripped = _HALF_BEFORE_RE.sub(" ", _HALF_PHRASE_RE.sub(" ", text.lower()))
    remainder = _DURATION_RE.sub(" ", stripped)
    words = [w for w in remainder.split() if w not in _LABEL_STOPWORDS]
    label = " ".join(words) if 0 < len(words) <= _MAX_LABEL_WORDS else ""
    return seconds, label


# ------------------------------------------------------------- timer store
@dataclass
class _Timer:
    id: int
    label: str
    duration_s: float
    due: float  # time.monotonic() deadline


# Single voice loop, one command at a time — but the text-mode alert thread
# also pops, so the store is locked anyway. Cheap to be correct.
_timers: dict[int, _Timer] = {}
_lock = threading.Lock()
_ids = itertools.count(1)


def _attributive_duration(total_s: float) -> str:
    """Duration as an adjective: 600 → '10 minute', for timer names."""
    total = max(0, int(round(total_s)))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hour")
    if minutes:
        parts.append(f"{minutes} minute")
    if seconds:
        parts.append(f"{seconds} second")
    return " ".join(parts) or "0 second"


def _name(timer: _Timer) -> str:
    """How the timer is referred to in speech: 'tea timer', '10 minute timer'."""
    if timer.label:
        return f"{timer.label} timer"
    return f"{_attributive_duration(timer.duration_s)} timer"


def start(duration_s: float, label: str = "") -> tuple[bool, str]:
    """Set a countdown timer. Returns (True, spoken ack) or (False, reason)."""
    label = " ".join(label.split())
    if duration_s < _MIN_DURATION_S:
        return False, "A timer needs to be at least one second."
    if duration_s > _MAX_DURATION_S:
        return False, "I can only set timers up to 24 hours."
    with _lock:
        if len(_timers) >= _MAX_ACTIVE:
            return False, f"You already have {len(_timers)} timers running."
        timer = _Timer(next(_ids), label, duration_s, time.monotonic() + duration_s)
        _timers[timer.id] = timer
    ack = f"Timer set for {format_duration(duration_s)}"
    if label:
        ack += f" for {label}"
    return True, ack + "."


def _matching(query: str) -> list[_Timer]:
    """
    Timers a cancel query refers to — by set duration, or by label substring.

    An empty query matches nothing: the empty string is a substring of every
    label, and "cancel the timer" must reach the soonest-due fallback below,
    not cancel everything at once.
    """
    if not query:
        return []
    duration = parse_duration(query)
    if duration is not None:
        return [t for t in _timers.values() if abs(t.duration_s - duration) < 0.5]
    return [t for t in _timers.values() if query in t.label.lower()]


def cancel(query: str = "", all_timers: bool = False) -> tuple[bool, str]:
    """
    Cancel timers by scope: all of them, a named/duration match, or — when
    the user just says "the timer" — whichever fires soonest.
    """
    query = " ".join(query.split()).lower()
    with _lock:
        if not _timers:
            return True, "There are no timers running."

        if all_timers:
            count = len(_timers)
            _timers.clear()
            return True, "Cancelled your timer." if count == 1 else f"Cancelled {count} timers."

        matches = _matching(query)
        if matches:
            for t in matches:
                del _timers[t.id]
            if len(matches) == 1:
                return True, f"Cancelled the {_name(matches[0])}."
            return True, f"Cancelled {len(matches)} timers."

        if not query:
            # "cancel the timer" — the salient one is the next to fire.
            soonest = min(_timers.values(), key=lambda t: t.due)
            del _timers[soonest.id]
            return True, f"Cancelled the {_name(soonest)}."

        if parse_duration(query) is not None:
            return True, f"I don't have a {query} timer running."
        return True, f"I don't have a timer for {query}."


def status() -> tuple[bool, str]:
    """Spoken summary of the running timers, soonest first."""
    with _lock:
        if not _timers:
            return True, "There are no timers running."
        now = time.monotonic()
        ordered = sorted(_timers.values(), key=lambda t: t.due)
        if len(ordered) == 1:
            remaining = ordered[0].due - now
            if remaining <= 0:
                return True, f"Your {_name(ordered[0])} is done."
            return True, f"Your {_name(ordered[0])} has {format_duration(remaining)} left."
        parts = [
            f"the {_name(t)} has {format_duration(max(0.0, t.due - now))} left"
            for t in ordered
        ]
        return True, f"You have {len(ordered)} timers: {_join_spoken(parts)}."


def pop_due() -> list[str]:
    """Remove and return the spoken alerts for every expired timer."""
    with _lock:
        now = time.monotonic()
        due = sorted((t for t in _timers.values() if now >= t.due), key=lambda t: t.due)
        for t in due:
            del _timers[t.id]
        return [f"Your {_name(t)} is done." for t in due]


def poll_interval_s() -> float | None:
    """
    How often the idle loop should wake to check for due timers.

    None when no timers are running — the loop then sleeps exactly as long
    as it did before this skill existed.
    """
    with _lock:
        return 0.5 if _timers else None
