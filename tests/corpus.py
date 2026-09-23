"""
Golden corpus for the intent router regression tests.

Two lists, kept deliberately separate:

* ``MUST_MATCH``  — (utterance, expected_intent_name). These MUST route to the
  named intent. Add a line here whenever you add or broaden an intent.
* ``MUST_FALL_THROUGH`` — ordinary questions/statements that MUST NOT match any
  intent; they belong to the LLM. This is the precision guarantee: before the
  patterns were anchored to the full utterance, a bare keyword anywhere in a
  sentence hijacked the router ("what's the time complexity of quicksort"
  answered with the wall clock). Every new pattern must keep this list green.

Shared between ``test_router.py`` (per-utterance assertions) and the fuzz guard
so both grow from one source of truth.
"""

from __future__ import annotations

# (utterance, expected_intent_name)
MUST_MATCH: list[tuple[str, str]] = [
    # --- Tier 0 ---
    ("who are you", "tell_name"),
    ("introduce yourself", "tell_name"),
    ("what time is it", "tell_time"),
    ("tell me the time", "tell_time"),
    ("What's the time?", "tell_time"),
    ("what's the current time", "tell_time"),
    ("what's the date", "tell_date"),
    ("what day is it today", "tell_date"),
    ("today's date", "tell_date"),
    ("open youtube", "open_youtube"),
    ("go to youtube", "open_youtube"),
    ("open google", "open_google"),
    # --- Tier 1 ---
    ("search youtube for cats", "search_youtube"),
    ("play despacito on youtube", "search_youtube"),
    ("youtube search for lofi", "search_youtube"),
    ("google how to cook pasta", "search_google"),
    ("search for python tutorials", "search_google"),
    ("open reddit.com", "open_website"),
    ("go to github.com", "open_website"),
    ("open notepad", "open_app"),
    ("launch chrome", "open_app"),
    ("open visual studio code", "open_app"),
    ("play music", "play_music"),
    # --- Tier 2 ---
    ("close chrome", "close_app"),
    ("kill spotify", "close_app"),
    # --- Natural phrasing: wake-word echo, politeness, punctuation ---
    ("hey loqi, what time is it", "tell_time"),
    ("Hey Loki open youtube", "open_youtube"),
    ("please open youtube", "open_youtube"),
    ("can you open notepad", "open_app"),
    ("open youtube please", "open_youtube"),
]

# Ordinary questions. Any match here is a routing bug.
MUST_FALL_THROUGH: list[str] = [
    "what's the time complexity of quicksort",
    "how much time will it take to learn python",
    "what is the best time of year to visit japan",
    "i have a date tonight",
    "tell me about the release date of gta 6",
    "who founded google in 1998",
    "how do i close a bank account",
    "explain quantum physics",
    "write me a poem",
    "what is love",
    "should i open a roth ira",
    "what does it mean to run a marathon",
]
