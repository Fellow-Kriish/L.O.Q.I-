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

import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Intent:
    """A single routable intent."""
    name: str
    patterns: list[re.Pattern]
    tier: int                       # 0, 1, 2, 3
    handler: str                    # action function name in actions.py
    extract: Callable | None = None  # (match) -> dict of extracted args
    describe: str | None = None     # spoken action template, e.g. "close {app_name}"

    def description(self, args: dict) -> str:
        """
        A human phrase for this action, for the confirm gate to speak back.

        Tier 2 and 3 read this aloud before doing anything, so it has to be a
        sentence fragment a person can actually parse — "close chrome", not
        "close_app: {'app_name': 'chrome'}". Falls back to the intent name when
        no template is set, which is fine for the untiered intents that are
        never spoken back.
        """
        if not self.describe:
            return self.name
        try:
            return self.describe.format(**args)
        except (KeyError, IndexError):
            # Template and extractor disagree. Say something safe rather than
            # crashing the gate that exists to keep the user in control.
            return self.name


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


def _extract_place(match: re.Match) -> dict:
    """Pull an optional place name from a regex group named 'place'."""
    place = match.group("place") or ""
    return {"place": place.strip()}


def _extract_timer_set(match: re.Match) -> dict:
    """The whole utterance — timers.extract_timer_args strips scaffolding itself."""
    return {"query": match.group(0)}


def _extract_timer_cancel(match: re.Match) -> dict:
    """Whatever sits between the verb and the word 'timer' — scope or label."""
    return {"query": (match.group("query") or "").strip()}


def _extract_contact_and_message(match: re.Match) -> dict:
    """Pull contact name and message text."""
    result = {"contact": match.group("contact").strip()}
    with contextlib.suppress(IndexError):
        result["message"] = match.group("message").strip()
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
    # Weather MUST precede the search intents: search_google's broad
    # "look up ..." pattern would otherwise eat "look up the weather in delhi"
    # and open a Google results page instead of answering.
    Intent(
        name="weather",
        patterns=[
            _p(r"(?:what(?:'?s| is)|how(?:'?s| is)) the weather(?: (?:like|today|outside|now|there))*(?: (?:in|for|at) (?P<place>.+?))?"),
            _p(r"(?:will it|is it going to|does it) rain(?: (?:today|tonight|now))*(?: (?:in|for|at) (?P<place>.+?))?"),
            _p(r"(?:check|look up|tell me) (?:the )?(?:weather|temperature)(?: (?:in|for|at) (?P<place>.+?))?"),
            _p(r"weather(?: (?:report|forecast|today|outside|now|update))*(?: (?:in|for|at) (?P<place>.+?))?"),
            # "at which water boils" is a question clause, not a place — refuse
            # question words so "what's the temperature at which water boils"
            # keeps falling through to the LLM instead of geocoding nonsense.
            _p(r"(?:what(?:'?s| is) the )?temperature(?: (?:outside|right now|now|today|there))*(?: (?:in|for|at) (?!which\b|what\b|how\b)(?P<place>.+?))?"),
        ],
        tier=0,
        handler="weather",
        extract=_extract_place,
    ),

    # Timer status needs "timer" in the pattern somewhere: without it,
    # "how much time is left in the match" (a question for the LLM) would
    # match, and "left"-only patterns hijack exactly the kind of question
    # the anchoring exists to protect.
    Intent(
        name="timer_status",
        patterns=[
            _p(r"how much time(?: is)? left.*\btimers?\b.*"),
            _p(r"how long(?: is)? left.*\btimers?\b.*"),
            _p(r"what(?:'?s| is) left.*\btimers?\b.*"),
            _p(r"how much longer.*\btimers?\b.*"),
            _p(r"how many timers\b.*"),
            _p(r"timers? status.*"),
            _p(r"(?:is|are)\b.*\btimers?\b.*\b(?:done|finished|up|ringing|over)\b.*"),
            _p(r"(?:check|list) (?:the |my |all (?:the |my )?)?timers?\b.*"),
        ],
        tier=0,
        handler="timer_status",
    ),

    # ------------------------------------------------------------------
    # Tier 1 — reversible actions, no confirmation
    # ------------------------------------------------------------------
    Intent(
        name="search_youtube",
        patterns=[
            _p(r"(?:search|find|look up)(?: on| in)? youtube (?:for )?(?P<query>.+?)"),
            _p(r"youtube search (?:for )?(?P<query>.+?)"),
            # "search(?: for)?" — without it, "search for cats on youtube"
            # captures the query as "for cats".
            _p(r"(?:play|search(?: for)?|find|look up) (?P<query>.+?) on youtube"),
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
    # Timer intents MUST precede open_app and close_app: open_app's
    # "start ..." pattern would eat "start a timer" (launching an app named
    # "a timer"), and close_app's "stop/kill ..." would eat "stop the timer".
    # Both require the word "timer" in the utterance so "start music" and
    # "stop the music" keep their existing routes.
    Intent(
        name="timer_set",
        patterns=[
            _p(r"(?:set|start|create|make)\b.*\btimers?\b.*"),
        ],
        tier=1,
        handler="set_timer",
        extract=_extract_timer_set,
    ),
    Intent(
        name="timer_cancel",
        patterns=[
            _p(r"(?:cancel|stop|clear|kill|end)(?: the| my)?\s*(?P<query>.*?)\s*timers?\b.*"),
        ],
        tier=1,
        handler="cancel_timer",
        extract=_extract_timer_cancel,
    ),
    # play_music MUST precede open_app: open_app's "start ..." pattern would
    # otherwise eat "start music" and try to launch an app named "music",
    # falling through to the LLM instead of playing.
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
    Intent(
        name="open_app",
        patterns=[
            _p(r"(?:open|launch|start|run) (?:the |my )?(?P<app>[\w\s]+?)(?:\s+app)?"),
        ],
        tier=1,
        handler="open_app",
        extract=_extract_app,
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
        describe="close {app_name}",
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


def route(text: str) -> MatchResult | None:
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
                    with contextlib.suppress(IndexError, AttributeError):
                        args = intent.extract(match)
                return MatchResult(intent=intent, args=args, raw_text=text)
    return None


# An utterance that is nothing but the wake word echoed back ("hey loki",
# "loki", "low key"). Whisper transcribes the spoken wake word freely, so this
# shape reaches the router, matches nothing, and burns a cloud round-trip plus
# a fallback-log entry on something that carries no request at all.
_WAKE_ECHO = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:lo[qk]i|low[\s-]?key)$",
    re.IGNORECASE,
)


def is_wake_echo(text: str) -> bool:
    """True when the utterance is only the wake word itself — no request in it."""
    return _WAKE_ECHO.match(normalize(text)) is not None
