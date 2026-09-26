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
    # --- Tier 0: weather ---
    ("what's the weather", "weather"),
    ("what's the weather today", "weather"),
    ("what is the weather like in mumbai", "weather"),
    ("how's the weather in delhi", "weather"),
    ("will it rain today", "weather"),
    ("is it going to rain tonight", "weather"),
    ("check the weather", "weather"),
    ("look up the weather in bangalore", "weather"),
    ("temperature", "weather"),
    ("what's the temperature outside", "weather"),
    ("weather in chennai", "weather"),
    # --- Tier 0: timer status ---
    ("how much time is left on the timer", "timer_status"),
    ("how much time is left on my tea timer", "timer_status"),
    ("how long is left on the timer", "timer_status"),
    ("what's left on the timer", "timer_status"),
    ("is the timer done", "timer_status"),
    ("timer status", "timer_status"),
    ("how many timers do i have", "timer_status"),
    ("check the timer", "timer_status"),
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
    ("start music", "play_music"),
    ("start some music", "play_music"),
    ("start my music", "play_music"),
    ("open music", "play_music"),
    ("open my music", "play_music"),
    ("play music", "play_music"),
    ("search for cats on youtube", "search_youtube"),
    ("find lofi beats on youtube", "search_youtube"),
    # --- Tier 1: timers ---
    ("set a timer for 10 minutes", "timer_set"),
    ("set a tea timer for 3 minutes", "timer_set"),
    ("set a timer for half an hour", "timer_set"),
    ("set a timer for an hour and a half", "timer_set"),
    ("set a timer for two and a half hours", "timer_set"),
    ("start a timer for forty five seconds", "timer_set"),
    ("make a 5 minute timer", "timer_set"),
    ("cancel the timer", "timer_cancel"),
    ("cancel my tea timer", "timer_cancel"),
    ("cancel all timers", "timer_cancel"),
    ("stop the timer", "timer_cancel"),
    ("cancel the 10 minute timer", "timer_cancel"),
    ("hey loqi set a timer for 5 minutes", "timer_set"),
    ("can you set a timer for 2 minutes", "timer_set"),
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
    "what's the temperature at which water boils",
    "how do rainbows form",
    "what is the weather like on mars",
    "will it rain more this decade than the last one",
    "how much time will it take to learn python",
    "how much time is left in the match",
    "who invented the timer",
    "how do timers work",
    "set a reminder for my dentist appointment",
    "what's the difference between a timer and an alarm",
    "how much petrol is left in the tank",
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
