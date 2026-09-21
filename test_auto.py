"""Automated regression test for the intent router — no interactive input, no side effects.

Covers two things:
  1. Commands that MUST route to a specific intent.
  2. Ordinary questions that MUST NOT match any intent (they belong to the LLM).

Group 2 is the important one. Before the patterns were anchored to the full
utterance, a bare keyword anywhere in a sentence hijacked the router --
"what's the time complexity of quicksort" answered with the wall clock.

Usage:
    python test_auto.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from intents import route

# (utterance, expected_intent_name or None for "should fall through to LLM")
MUST_MATCH = [
    # --- Tier 0 ---
    ("who are you",                 "tell_name"),
    ("introduce yourself",          "tell_name"),
    ("what time is it",             "tell_time"),
    ("tell me the time",            "tell_time"),
    ("What's the time?",            "tell_time"),
    ("what's the current time",     "tell_time"),
    ("what's the date",             "tell_date"),
    ("what day is it today",        "tell_date"),
    ("today's date",                "tell_date"),
    ("open youtube",                "open_youtube"),
    ("go to youtube",               "open_youtube"),
    ("open google",                 "open_google"),

    # --- Tier 1 ---
    ("search youtube for cats",     "search_youtube"),
    ("play despacito on youtube",   "search_youtube"),
    ("youtube search for lofi",     "search_youtube"),
    ("google how to cook pasta",    "search_google"),
    ("search for python tutorials", "search_google"),
    ("open reddit.com",             "open_website"),
    ("go to github.com",            "open_website"),
    ("open notepad",                "open_app"),
    ("launch chrome",               "open_app"),
    ("open visual studio code",     "open_app"),
    ("play music",                  "play_music"),

    # --- Tier 2 ---
    ("close chrome",                "close_app"),
    ("kill spotify",                "close_app"),

    # --- Natural phrasing: wake-word echo, politeness, punctuation ---
    ("hey loqi, what time is it",   "tell_time"),
    ("Hey Loki open youtube",       "open_youtube"),
    ("please open youtube",         "open_youtube"),
    ("can you open notepad",        "open_app"),
    ("open youtube please",         "open_youtube"),
]

# These are ordinary questions. Any match here is a routing bug.
MUST_FALL_THROUGH = [
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

passed = failed = 0

print("=" * 68)
print("  Intent Router — Regression Test")
print("=" * 68)
print("\n  Commands that must route:\n")

for text, expected in MUST_MATCH:
    result = route(text)
    actual = result.intent.name if result else None
    if actual == expected:
        args = f"   args={result.args}" if result.args else ""
        print(f"  ✅ {text!r:46} → {actual}{args}")
        passed += 1
    else:
        print(f"  ❌ {text!r:46} → got {actual}, expected {expected}")
        failed += 1

print("\n  Questions that must fall through to the LLM:\n")

for text in MUST_FALL_THROUGH:
    result = route(text)
    if result is None:
        print(f"  ✅ {text!r:46} → fallback")
        passed += 1
    else:
        print(f"  ❌ {text!r:46} → HIJACKED by {result.intent.name} {result.args}")
        failed += 1

total = len(MUST_MATCH) + len(MUST_FALL_THROUGH)
print(f"\n  Results: {passed} passed, {failed} failed out of {total}")
sys.exit(1 if failed else 0)
