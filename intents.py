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
# - All patterns are case-insensitive (re.IGNORECASE).

def _p(pattern: str) -> re.Pattern:
    """Compile a pattern with IGNORECASE."""
    return re.compile(pattern, re.IGNORECASE)


INTENTS: list[Intent] = [
    # ------------------------------------------------------------------
    # Tier 0 — read-only / local, instant, no confirmation
    # ------------------------------------------------------------------
    Intent(
        name="tell_name",
        patterns=[
            _p(r"\b(?:what(?:'?s| is) your (?:name|identity))\b"),
            _p(r"\bwho are you\b"),
            _p(r"\bwhat do I call you\b"),
            _p(r"\bwhat(?:'?s| is) your name\b"),
            _p(r"\btell me your name\b"),
            _p(r"\bintroduce yourself\b"),
        ],
        tier=0,
        handler="tell_name",
    ),
    Intent(
        name="tell_time",
        patterns=[
            _p(r"\b(?:what(?:'?s| is) the )?(?:current )?time\b"),
            _p(r"\btell (?:me )?the time\b"),
            _p(r"\bwhat time is it\b"),
        ],
        tier=0,
        handler="tell_time",
    ),
    Intent(
        name="tell_date",
        patterns=[
            _p(r"\b(?:what(?:'?s| is) )?(?:the |today(?:'?s)? )?date\b"),
            _p(r"\bwhat day is (?:it|today)\b"),
            _p(r"\btoday(?:'?s)? date\b"),
        ],
        tier=0,
        handler="tell_date",
    ),
    Intent(
        name="open_youtube",
        patterns=[
            _p(r"\b(?:open|go to|launch|show) youtube\b"),
        ],
        tier=0,
        handler="open_youtube",
    ),
    Intent(
        name="open_google",
        patterns=[
            _p(r"\b(?:open|go to|launch|show) google\b"),
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
            _p(r"\b(?:search|find|look up)(?: on| in)? youtube (?:for )?(?P<query>.+)"),
            _p(r"\byoutube search (?:for )?(?P<query>.+)"),
            _p(r"\b(?:play|search) (?P<query>.+?) on youtube\b"),
        ],
        tier=1,
        handler="search_youtube",
        extract=_extract_query,
    ),
    Intent(
        name="search_google",
        patterns=[
            _p(r"\b(?:search|google|look up)(?: on| in)? google (?:for )?(?P<query>.+)"),
            _p(r"\bgoogle (?:search (?:for )?)?(?P<query>.+)"),
            _p(r"\bsearch (?:for |the )?(?P<query>.+)"),
        ],
        tier=1,
        handler="search_google",
        extract=_extract_query,
    ),
    Intent(
        name="open_website",
        patterns=[
            _p(r"\b(?:open|go to|launch|visit|navigate to) (?P<url>[\w.-]+\.(?:com|org|net|io|dev|ai|co|me|edu|gov)\S*)"),
        ],
        tier=1,
        handler="open_website",
        extract=_extract_url,
    ),
    Intent(
        name="open_app",
        patterns=[
            _p(r"\b(?:open|launch|start|run) (?P<app>[\w\s]+?)(?:\s+app)?$"),
        ],
        tier=1,
        handler="open_app",
        extract=_extract_app,
    ),
    Intent(
        name="play_music",
        patterns=[
            _p(r"\b(?:play|start)(?: some| my)? music\b"),
            _p(r"\bplay(?: some)? songs?\b"),
            _p(r"\bopen (?:music|spotify|my music)\b"),
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
            _p(r"\b(?:close|quit|exit|kill|stop) (?P<app>[\w\s]+?)(?:\s+app)?$"),
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

def route(text: str) -> Optional[MatchResult]:
    """
    Match text against all intents. Returns MatchResult on first hit, None on miss.
    Cost: microseconds — don't worry about pattern count.
    """
    text = text.strip()
    for intent in INTENTS:
        for pattern in intent.patterns:
            match = pattern.search(text)
            if match:
                args = {}
                if intent.extract and match:
                    try:
                        args = intent.extract(match)
                    except (IndexError, AttributeError):
                        pass
                return MatchResult(intent=intent, args=args, raw_text=text)
    return None
