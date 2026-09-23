"""
LOQI — Main Loop

The top-level orchestrator. Ties everything together:
  wake word → record → transcribe → route → act/fallback → speak

Usage:
    python main.py              # Full mode (wake word + voice)
    python main.py --text       # Text-only mode (typed input, no mic)
    python main.py --no-wake    # Voice mode but no wake word (press Enter to speak)
"""

import os
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8")

# Suppress noisy warnings from underlying ML libraries
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="huggingface_hub")

import argparse
import threading

from actions import execute
from confirm import confirm_action
from fallback_log import log_fallback
from intents import route


def main():
    parser = argparse.ArgumentParser(description="LOQI Voice Assistant")
    parser.add_argument("--text", action="store_true", help="Text-only mode (no mic/TTS)")
    parser.add_argument("--no-wake", action="store_true", help="Voice mode without wake word (press Enter to speak)")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  ╔═══════════════════════════════════════════════╗")
    print("  ║            L . O . Q . I .   v 0.1             ║")
    print("  ║    Local Operations & Query Interface          ║")
    print("  ╚═══════════════════════════════════════════════╝")
    print("=" * 60)
    print()

    # ------------------------------------------------------------------
    # Text-only mode — no audio dependencies at all
    # ------------------------------------------------------------------
    if args.text:
        print("  Mode: TEXT ONLY (no mic, no TTS)")
        print("  Type commands. Type 'quit' to exit.\n")
        _text_loop()
        return

    # ------------------------------------------------------------------
    # Voice modes — load audio components
    # ------------------------------------------------------------------
    print("  Loading components...")

    # STT (load once, keep resident)
    from stt import STT
    stt = STT()

    # TTS (load once, keep resident)
    from tts import TTS
    tts = TTS()

    # Recorder
    from recorder import Recorder
    recorder = Recorder()

    # Brain (Groq fallback)
    from brain import Brain
    brain = Brain()

    def speak(text: str):
        """TTS speak wrapper."""
        tts.speak(text)

    if args.no_wake:
        # ----------------------------------------------------------
        # No-wake mode: press Enter to start recording
        # ----------------------------------------------------------
        print("\n  Mode: VOICE (no wake word)")
        print("  Press Enter to speak, then talk. Type 'quit' to exit.\n")

        while True:
            try:
                user_input = input("  Press Enter to speak (or type 'quit'): ").strip()
                if user_input.lower() in ("quit", "exit", "q"):
                    print("  Goodbye!")
                    break

                # Record
                wav_bytes = recorder.record()
                if not wav_bytes:
                    print("  No audio captured.")
                    continue

                # Transcribe
                text = stt.transcribe(wav_bytes)
                if not text:
                    print("  Couldn't understand that.")
                    continue
                print(f"  📝 You said: \"{text}\"")

                # Process
                _process_command(text, speak, brain, recorder=recorder, stt=stt)

            except KeyboardInterrupt:
                print("\n  Goodbye!")
                break

        recorder.cleanup()

    else:
        # ----------------------------------------------------------
        # Full mode: wake word triggered
        # ----------------------------------------------------------
        print("\n  Mode: FULL (wake word + voice)")

        from wakeword import WakeWordListener

        wake_event = threading.Event()

        def on_wake(model_name: str, score: float):
            """Called when wake word is detected."""
            wake_event.set()

        wake_listener = WakeWordListener(on_detected=on_wake)
        wake_listener.start()

        speak("L.O.Q.I. online. Say the wake word when you need me.")
        print("  Say the wake word to activate. Press Ctrl+C to quit.\n")

        try:
            while True:
                # Wait for wake word
                wake_event.wait()
                wake_event.clear()

                # Pause wake word listener during interaction
                wake_listener.pause()

                # Acknowledgment
                speak("Yes?")

                # Record
                wav_bytes = recorder.record()
                if not wav_bytes:
                    print("  No audio captured.")
                    wake_listener.resume()
                    continue

                # Transcribe
                text = stt.transcribe(wav_bytes)
                if not text:
                    speak("I didn't catch that.")
                    wake_listener.resume()
                    continue
                print(f"  📝 You said: \"{text}\"")

                # Process command
                _process_command(text, speak, brain, recorder=recorder, stt=stt)

                # Resume wake word listening
                wake_listener.resume()

        except KeyboardInterrupt:
            print("\n  Shutting down...")
            wake_listener.stop()
            recorder.cleanup()
            print("  Goodbye!")


def _process_command(text: str, speak_fn, brain, recorder=None, stt=None):
    """
    Route text through intent router → action/confirm → execute.
    Falls back to Groq LLM if no intent matches.
    """
    result = route(text)

    if result:
        # --- Intent matched ---
        intent = result.intent
        print(f"  ✅ Intent: {intent.name} (Tier {intent.tier})")

        # Build action description for confirm gate
        action_desc = f"{intent.name}: {result.args}" if result.args else intent.name

        # Check permission tier
        if not confirm_action(
            intent.tier, action_desc,
            tts_fn=speak_fn,
            recorder_fn=recorder.record if recorder else None,
            stt_fn=stt.transcribe if stt else None,
        ):
            speak_fn("Okay, cancelled.")
            return

        # Execute the action
        success, response = execute(intent.handler, **result.args)
        print(f"  {'✅' if success else '❌'} {response}")
        speak_fn(response)

    else:
        # --- No match → Groq LLM fallback ---
        print("  🧠 No intent match → Groq fallback")

        # Stream response, speak sentence-by-sentence
        full_response = ""
        for sentence in brain.ask_stream(text):
            print(f"  💬 {sentence}")
            speak_fn(sentence)
            full_response += sentence + " "

        # Log the fallback for manual router improvement
        log_fallback(text, full_response.strip())


def _text_loop():
    """Text-only interaction loop — no audio at all."""
    from brain import Brain
    brain = Brain()

    while True:
        try:
            text = input("  You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye!")
            break

        if not text:
            continue
        if text.lower() in ("quit", "exit", "q"):
            print("  Goodbye!")
            break

        _process_command(text, lambda t: print(f"  🔊 {t}"), brain)


if __name__ == "__main__":
    main()
