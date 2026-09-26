"""
test_barge_in.py — Barge-In Regression Test

Two test modes, run from project root with the venv active:

    python test_barge_in.py --catch     # Test A: catch-rate (say stop-phrase 10x)
    python test_barge_in.py --bleed     # Test B: false-trigger from speaker bleed

TARGET:
    Catch rate  : ≥8/10  (was ~1-2/5 before threading fix)
    False triggers: 0/5  (bleed-only playback, zero human speech)

Usage note: in --catch mode, the script plays a long TTS clip in a loop and
prompts you to speak. Say any stop-phrase ("stop", "cancel", etc.) when
instructed. The script records whether it caught each attempt and prints a
final score.
"""

import argparse
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def load_components():
    print("  Loading STT...")
    from stt import STT
    stt = STT()
    print("  Loading TTS...")
    from tts import TTS
    tts = TTS()
    return stt, tts


# ---------------------------------------------------------------------------
# Synthetic TTS playback helper
# Plays a looping tone / silence so we have "TTS is active" without
# needing a real sentence every time.
# ---------------------------------------------------------------------------

SAMPLE_RATE = 24_000  # Kokoro / sounddevice output rate

def _play_long_tts(tts, duration_s: float = 6.0, stop_event: threading.Event | None = None):
    """
    Speak a long sentence (or repeat until stop_event fires or duration elapses).
    Uses real Kokoro so speaker bleed test reflects actual conditions.
    """
    sentence = (
        "This is a test of the barge-in system. "
        "While this sentence is playing, you can say a stop phrase to interrupt. "
        "The listener should catch it within one attempt. "
        "Keep listening for your cue."
    )
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            break
        tts.speak(sentence)
        if stop_event is not None and stop_event.is_set():
            break
    tts.reset()  # clear _stopped flag if stop() was called


# ---------------------------------------------------------------------------
# Test A — Catch rate
# ---------------------------------------------------------------------------

def test_catch_rate(stt, tts, trials: int = 10):
    """
    Play TTS for a window. Prompt user to say a stop-phrase.
    Count how many of <trials> attempts were caught.
    """
    from barge_in import BargeInListener

    print()
    print("=" * 60)
    print("  TEST A: CATCH RATE")
    print(f"  You will be prompted {trials} times.")
    print("  On each cue, say 'stop' or 'cancel' clearly.")
    print("=" * 60)
    input("  Press Enter when ready...\n")

    caught = 0
    missed = 0

    for i in range(1, trials + 1):
        print(f"  [{i}/{trials}] TTS starting — say a stop-phrase now...")

        stop_event = threading.Event()
        listener = BargeInListener(stt=stt, stop_event=stop_event)
        listener.start()

        # Play TTS in a background thread so main thread can watch stop_event.
        tts_thread = threading.Thread(
            target=_play_long_tts,
            args=(tts,),
            kwargs={"duration_s": 7.0, "stop_event": stop_event},
            daemon=True,
        )
        tts_thread.start()

        # Wait up to 7 s for either a catch or TTS to finish naturally.
        fired = stop_event.wait(timeout=7.0)

        if fired:
            # Barge-in fired: kill audio immediately.
            tts.stop()
            tts.reset()
            listener.stop()
            caught += 1
            print(f"  ✅ CAUGHT ({i}/{trials})\n")
        else:
            listener.stop()
            tts.reset()
            missed += 1
            print(f"  ❌ MISSED — stop-phrase not detected ({i}/{trials})\n")

        tts_thread.join(timeout=2.0)
        time.sleep(0.5)  # brief gap between trials

    print()
    print("=" * 60)
    print(f"  CATCH RATE: {caught}/{trials}")
    if caught >= 8:
        print("  ✅ PASS — barge-in is reliable")
    elif caught >= 5:
        print("  ⚠️  PARTIAL — improvement but below target (≥8/10)")
    else:
        print("  ❌ FAIL — threading fix not sufficient, investigate further")
    print("=" * 60)

    return caught, trials


# ---------------------------------------------------------------------------
# Test B — False-trigger / speaker bleed
# ---------------------------------------------------------------------------

def test_false_triggers(stt, tts, responses: int = 5):
    """
    Play TTS responses with NO human speech. Count barge-in false triggers.
    Any trigger here is pure speaker bleed through the mic.
    """
    from barge_in import BargeInListener

    print()
    print("=" * 60)
    print("  TEST B: FALSE TRIGGERS (speaker bleed)")
    print(f"  Playing {responses} TTS responses. DO NOT SPEAK.")
    print("  Any barge-in trigger here = false positive from bleed.")
    print("=" * 60)
    input("  Press Enter when ready — stay silent throughout...\n")

    false_triggers = 0

    for i in range(1, responses + 1):
        print(f"  [{i}/{responses}] Playing TTS — stay silent...")

        stop_event = threading.Event()
        listener = BargeInListener(stt=stt, stop_event=stop_event)
        listener.start()

        # Play the full response — do not stop early.
        _play_long_tts(tts, duration_s=8.0, stop_event=stop_event)

        fired = stop_event.is_set()
        listener.stop()
        tts.reset()

        if fired:
            false_triggers += 1
            print(f"  ❌ FALSE TRIGGER on response {i}\n")
        else:
            print(f"  ✅ Clean — no false trigger ({i}/{responses})\n")

        time.sleep(0.3)

    print()
    print("=" * 60)
    print(f"  FALSE TRIGGERS: {false_triggers}/{responses}")
    if false_triggers == 0:
        print("  ✅ PASS — VAD/onset threshold sufficient against bleed")
    elif false_triggers <= 1:
        print("  ⚠️  MARGINAL — mostly clean, 1 bleed slip")
    else:
        print("  ❌ FAIL — bleed still getting through; consider AEC or higher onset")
    print("=" * 60)

    return false_triggers, responses


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Barge-in regression tests")
    parser.add_argument("--catch", action="store_true", help="Test A: catch-rate (say stop-phrase 10x)")
    parser.add_argument("--bleed", action="store_true", help="Test B: false-trigger from speaker bleed")
    parser.add_argument("--trials", type=int, default=10, help="Number of trials for --catch (default 10)")
    parser.add_argument("--responses", type=int, default=5, help="Number of TTS responses for --bleed (default 5)")
    args = parser.parse_args()

    if not args.catch and not args.bleed:
        parser.print_help()
        print("\n  Run with --catch, --bleed, or both.")
        sys.exit(0)

    print()
    print("  Loading components (STT + TTS)...")
    stt, tts = load_components()
    print("  ✅ Ready.\n")

    results = {}

    if args.catch:
        caught, total = test_catch_rate(stt, tts, trials=args.trials)
        results["catch"] = f"{caught}/{total}"

    if args.bleed:
        ft, total = test_false_triggers(stt, tts, responses=args.responses)
        results["false_triggers"] = f"{ft}/{total}"

    if len(results) > 1:
        print()
        print("  SUMMARY")
        print(f"  Catch rate     : {results.get('catch', 'skipped')}")
        print(f"  False triggers : {results.get('false_triggers', 'skipped')}")


if __name__ == "__main__":
    main()
