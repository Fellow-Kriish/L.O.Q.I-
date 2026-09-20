"""Quick automated test for the intent router — no interactive input needed."""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from intents import route

tests = [
    ("open youtube",          "open_youtube"),
    ("go to youtube",         "open_youtube"),
    ("search youtube for cats", "search_youtube"),
    ("open google",           "open_google"),
    ("google how to cook pasta", "search_google"),
    ("search for python tutorials", "search_google"),
    ("what time is it",       "tell_time"),
    ("tell me the time",      "tell_time"),
    ("what's the date",       "tell_date"),
    ("open notepad",          "open_app"),
    ("launch chrome",         "open_app"),
    ("play music",            "play_music"),
    ("open reddit.com",       "open_website"),
    ("go to github.com",      "open_website"),
    ("close chrome",          "close_app"),
    ("explain quantum physics",  None),  # should NOT match → fallback
    ("write me a poem",         None),  # should NOT match → fallback
]

passed = 0
failed = 0

for text, expected_handler in tests:
    result = route(text)
    actual = result.intent.handler if result else None

    if actual == expected_handler:
        print(f"  ✅ PASS: \"{text}\" → {actual}")
        passed += 1
    else:
        print(f"  ❌ FAIL: \"{text}\" → got {actual}, expected {expected_handler}")
        if result:
            print(f"           matched: {result.intent.name}, args: {result.args}")
        failed += 1

print(f"\n  Results: {passed} passed, {failed} failed out of {len(tests)}")
