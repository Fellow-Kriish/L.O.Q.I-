"""
LOQI — CLI Test Harness for the Intent Router

Type commands, see matched intent + extracted args + action result.
No audio dependencies — pure text in/out. This is build step 1's deliverable.

Usage:
    python test_router.py
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

from intents import route
from actions import execute
from confirm import confirm_action


def main():
    print("=" * 60)
    print("  LOQI Intent Router — Test Harness")
    print("  Type a command to test routing. Type 'quit' to exit.")
    print("  Type 'list' to see all known intents.")
    print("=" * 60)
    print()

    from intents import INTENTS

    while True:
        try:
            text = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not text:
            continue

        if text.lower() in ("quit", "exit", "q"):
            print("Bye.")
            break

        if text.lower() == "list":
            print("\nKnown intents:")
            for intent in INTENTS:
                tier_label = {0: "T0 (instant)", 1: "T1 (instant)", 2: "T2 (confirm)", 3: "T3 (confirm+repeat)"}
                print(f"  {intent.name:<20s}  {tier_label.get(intent.tier, '?'):<20s}  → {intent.handler}")
            print()
            continue

        # Route the text
        result = route(text)

        if result is None:
            print(f"  ❌ No match → would fall back to Groq LLM")
            print()
            continue

        # Show match details
        print(f"  ✅ Matched: {result.intent.name}")
        print(f"     Tier:    {result.intent.tier}")
        print(f"     Handler: {result.intent.handler}")
        if result.args:
            print(f"     Args:    {result.args}")

        # Check confirm gate
        action_desc = f"{result.intent.handler}({result.args})" if result.args else result.intent.handler
        if not confirm_action(result.intent.tier, action_desc):
            print(f"  ⛔ Action denied by confirm gate.")
            print()
            continue

        # Execute the action
        success, response = execute(result.intent.handler, **result.args)
        status = "✅" if success else "❌"
        print(f"  {status} Response: {response}")
        print()


if __name__ == "__main__":
    main()
