"""
LOQI — Fallback Logger

Logs every utterance that falls through to the Groq LLM fallback.
Used for the manual router-improvement loop (v2 doc section 5):
  - Review the log periodically.
  - When a phrase repeats, add it as a new regex pattern in intents.py.

Format: JSON Lines (.jsonl), one entry per fallback.
"""

import json
from datetime import datetime

import config


def log_fallback(text: str, response_summary: str = ""):
    """
    Log a fallback-triggering utterance.

    Args:
        text: The transcribed text that didn't match any intent.
        response_summary: Brief summary of what the LLM responded (optional).
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "text": text,
        "response_summary": response_summary[:200] if response_summary else "",
    }

    try:
        with open(config.FALLBACK_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  ⚠️  Failed to write fallback log: {e}")


def read_log(n: int = 50) -> list[dict]:
    """Read the last N fallback log entries."""
    entries = []
    try:
        with open(config.FALLBACK_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except FileNotFoundError:
        return []

    return entries[-n:]


def show_frequent(min_count: int = 2):
    """
    Print utterances that appear multiple times in the fallback log.
    These are candidates for promotion to intents.py.
    """
    from collections import Counter

    entries = read_log(n=10000)
    texts = [e["text"].lower().strip() for e in entries]
    counts = Counter(texts)

    repeated = [(text, count) for text, count in counts.most_common() if count >= min_count]

    if not repeated:
        print("  No repeated fallback utterances found yet.")
        return

    print(f"\n  Repeated fallback utterances (≥{min_count} times):")
    print(f"  {'Count':<8} {'Utterance'}")
    print(f"  {'-----':<8} {'-' * 50}")
    for text, count in repeated:
        print(f"  {count:<8} {text}")
    print()
