"""
LOQI — Intent Router

Regex/keyword-based intent matching. Each intent has:
- name: unique identifier
- patterns: list of compiled regexes (case-insensitive)
- tier: permission level (0-3) per v2 doc section 4
- handler: name of the action function in actions.py
- extract: function to pull args from the regex match

Router checks patterns in order, returns first match.
If nothing matches → LLM fallback.

Patterns are matched against the WHOLE normalized utterance, never a
substring — see _p() for why.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Intent:
    """A single routable intent."""
    name: str
    patterns: list[re.Pattern]
    tier: int                       # 0, 1, 2, 3
    handler: str                    # action function name in actions.py
    extract: Callable = None        # (match) -> dict of extracted args


@dataclass
class MatchResult:
    """Result of a successful intent match."""
    intent: Intent
    args: dict = field(default_factory=dict)
    raw_text: str = ""


# ---------------------------------------------------------------------------
# Extractor helpers
# ---------------------------------------------------------------------------

def _extract_query(match: re.Match) -> dict:
    """Pull a search query from a regex match group named 'query'."""
    return {"query": match.group("query").strip()}


def _extract_app(match: re.Match) -> dict:
    """Pull an app name from a regex match group named 'app'."""
    return {"app_name": match.group("app").strip().lower()}


def _extract_url(match: re.Match) -> dict:
    """Pull a URL/domain from a regex match group named 'url'."""
    url = match.group("url").strip().lower()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return {"url": url}


def _extract_contact_and_message(match: re.Match) -> dict:
    """Pull contact name and message text."""
    result = {"contact": match.group("contact").strip()}
    try:
        result["message"] = match.group("message").strip()
    except IndexError:
        pass
    return result


# ---------------------------------------------------------------------------
# Intent definitions — top 10+ daily commands
# ---------------------------------------------------------------------------
# Pattern design notes:
# - Regex cost is microseconds regardless of count — don't under-build.
# - More patterns here = fewer expensive Groq round-trips.
# - All patterns are case-insensitive and FULL-UTTERANCE anchored (see _p).

# Conversational filler STT commonly picks up around a command. Allowed by
# the anchors so "please open youtube" and "hey loqi, what time is it" still
# match without loosening the anchoring itself.
# Whisper transcribes the wake word as "Loki"/"Loqi"/"Low-key" fairly freely.
_PREFIX = (
    r"(?:(?:hey|ok|okay)\s+)?(?:lo[qk]i|low[\s-]?key)[,.]?\s+"   # wake word echo
    r"|(?:can|could|would)\s+you\s+(?:please\s+)?"
    r"|please\s+"
    r"|i\s+want\s+(?:you\s+)?to\s+"
)
_SUFFIX = r"(?:\s+(?:please|for me|thanks|thank you))*"


def _p(pattern: str) -> re.Pattern:
    """
    Compile a case-insensitive pattern anchored to the full utterance.

    Anchoring is the whole point. With an unanchored `.search()`, a pattern
    like `\\btime\\b` matches "what's the TIME complexity of quicksort" and
    hijacks a question that belongs to the LLM. Requiring the pattern to
    cover the entire (normalized) utterance means keywords buried in longer
    sentences fall through to the fallback, which is the correct behavior.

    An optional leading filler group and trailing politeness group keep
    natural phrasing working despite the strict anchors.
    """
    return re.compile(rf"^(?:{_PREFIX})?(?:{pattern}){_SUFFIX}$", re.IGNORECASE)


INTENTS: list[Intent] = [
    # ------------------------------------------------------------------
    # Tier 0 — read-only / local, instant, no confirmation
    # ------------------------------------------------------------------
    Intent(
        name="tell_name",
        patterns=[
            _p(r"what(?:'?s| is) your (?:name|identity)"),
            _p(r"who are you"),
            _p(r"what (?:do|should) i call you"),
            _p(r"tell me your name"),
            _p(r"introduce yourself"),
        ],
        tier=0,
        handler="tell_name",
    ),
    Intent(
        name="tell_time",
        patterns=[
            _p(r"what(?:'?s| is) the (?:current )?time(?: (?:right )?now)?"),
            _p(r"what time is it(?: (?:right )?now)?"),
            _p(r"tell me the (?:current )?time"),
            _p(r"do you (?:have|know) the time"),
            _p(r"(?:the )?(?:current )?time"),
        ],
        tier=0,
        handler="tell_time",
    ),
    Intent(
        name="tell_date",
        patterns=[
            _p(r"what(?:'?s| is) (?:the |today'?s )?date(?: today)?"),
            _p(r"what(?:'?s| is) the date today"),
            _p(r"what day is it(?: today)?"),
            _p(r"what day of the week is it"),
            _p(r"tell me the date"),
            _p(r"today'?s date"),
            _p(r"(?:the )?date"),
        ],
        tier=0,
        handler="tell_date",
    ),
    Intent(
        name="open_youtube",
        patterns=[
            _p(r"(?:open|go to|launch|show|pull up) youtube"),
        ],
        tier=0,
        handler="open_youtube",
    ),
    Intent(
        name="open_google",
        patterns=[
            _p(r"(?:open|go to|launch|show|pull up) google"),
        ],
        tier=0,
        handler="open_google",
    ),

    # ------------------------------------------------------------------
    # Tier 1 — reversible actions, no confirmation
    # ------------------------------------------------------------------
    Intent(
        name="search_youtube",
        patterns=[
            _p(r"(?:search|find|look up)(?: on| in)? youtube (?:for )?(?P<query>.+?)"),
            _p(r"youtube search (?:for )?(?P<query>.+?)"),
            _p(r"(?:play|search|find|look up) (?P<query>.+?) on youtube"),
        ],
        tier=1,
        handler="search_youtube",
        extract=_extract_query,
    ),
    Intent(
        name="search_google",
        patterns=[
            _p(r"(?:search|google|look up)(?: on| in)? google (?:for )?(?P<query>.+?)"),
            _p(r"google (?:search )?(?:for )?(?P<query>.+?)"),
            _p(r"(?:search|look up) (?:for |the )?(?P<query>.+?)"),
        ],
        tier=1,
        handler="search_google",
        extract=_extract_query,
    ),
    Intent(
        name="open_website",
        patterns=[
            _p(r"(?:open|go to|launch|visit|navigate to) (?P<url>[\w.-]+\.(?:com|org|net|io|dev|ai|co|me|edu|gov|in|uk)\S*)"),
        ],
        tier=1,
        handler="open_website",
        extract=_extract_url,
    ),
    Intent(
        name="open_app",
        patterns=[
            _p(r"(?:open|launch|start|run) (?:the |my )?(?P<app>[\w\s]+?)(?:\s+app)?"),
        ],
        tier=1,
        handler="open_app",
        extract=_extract_app,
    ),
    Intent(
        name="play_music",
        patterns=[
            _p(r"(?:play|start)(?: some| my)? music"),
            _p(r"play(?: some)? songs?"),
            _p(r"open (?:music|spotify|my music)"),
        ],
        tier=1,
        handler="play_music",
    ),

    # ------------------------------------------------------------------
    # Tier 2 — sends/writes, confirmation required
    # ------------------------------------------------------------------
    Intent(
        name="close_app",
        patterns=[
            _p(r"(?:close|quit|exit|kill|stop) (?:the |my )?(?P<app>[\w\s]+?)(?:\s+app)?"),
        ],
        tier=2,
        handler="close_app",
        extract=_extract_app,
    ),

    # ------------------------------------------------------------------
    # More intents added here as usage grows (see section 5 of v2 doc)
    # ------------------------------------------------------------------
]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")
_TRAILING_PUNCT = re.compile(r"[.!?,;:\s]+$")


def normalize(text: str) -> str:
    """
    Clean up an STT transcript so anchored patterns can match it.

    Whisper returns capitalized, punctuated prose ("What time is it?"), and
    sometimes wraps things in quotes. Anchored patterns would miss all of
    that, so strip it here rather than bloating every pattern.
    """
    text = _WHITESPACE.sub(" ", text.strip())
    text = text.strip(" \t\"'“”‘’")
    text = _TRAILING_PUNCT.sub("", text)
    return text


def route(text: str) -> Optional[MatchResult]:
    """
    Match text against all intents. Returns MatchResult on first hit, None on miss.
    Cost: microseconds — don't worry about pattern count.
    """
    text = normalize(text)
    if not text:
        return None

    for intent in INTENTS:
        for pattern in intent.patterns:
            match = pattern.search(text)
            if match:
                args = {}
                if intent.extract:
                    try:
                        args = intent.extract(match)
                    except (IndexError, AttributeError):
                        pass
                return MatchResult(intent=intent, args=args, raw_text=text)
    return None
