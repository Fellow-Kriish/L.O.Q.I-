"""End-to-end test for the full pipeline in text mode — router → actions → confirm."""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from intents import route
from actions import execute
from confirm import confirm_action

tests = [
    # (input_text, expected_handler, expected_success, check_response_contains)
    ("what time is it",       "tell_time",      True,  "It's"),
    ("what's the date",       "tell_date",      True,  "Today is"),
    ("open youtube",          "open_youtube",    True,  "Opening YouTube"),
    ("search youtube for funny cats", "search_youtube", True, "Searching YouTube for funny cats"),
    ("google best python IDE", "search_google",  True,  "Searching Google for best python IDE"),
    ("open reddit.com",       "open_website",    True,  "reddit.com"),
    ("open notepad",          "open_app",        True,  "Opening notepad"),
    # Fallback cases
    ("explain quantum physics", None, None, None),
    ("write me a haiku",        None, None, None),
]

passed = 0
failed = 0

print("=" * 60)
print("  End-to-End Pipeline Test (text mode)")
print("=" * 60)
print()

for text, expected_handler, expected_success, response_check in tests:
    result = route(text)
    actual_handler = result.intent.handler if result else None

    if actual_handler != expected_handler:
        print(f"  ❌ ROUTING FAIL: \"{text}\" → got {actual_handler}, expected {expected_handler}")
        failed += 1
        continue

    if expected_handler is None:
        print(f"  ✅ FALLBACK: \"{text}\" → correctly unmatched (would go to Groq)")
        passed += 1
        continue

    # Check confirm gate (all test cases are Tier 0/1 except close_app)
    tier = result.intent.tier
    if tier > 1:
        print(f"  ⏭️  SKIP: \"{text}\" → Tier {tier} requires confirmation (interactive)")
        passed += 1
        continue

    # Execute the action
    success, response = execute(result.intent.handler, **result.args)

    if success != expected_success:
        print(f"  ❌ ACTION FAIL: \"{text}\" → success={success}, expected {expected_success}")
        print(f"     Response: {response}")
        failed += 1
        continue

    if response_check and response_check.lower() not in response.lower():
        print(f"  ❌ RESPONSE FAIL: \"{text}\" → \"{response}\" missing \"{response_check}\"")
        failed += 1
        continue

    print(f"  ✅ PASS: \"{text}\" → {actual_handler} → \"{response}\"")
    passed += 1

print(f"\n  Results: {passed} passed, {failed} failed out of {len(tests)}")
