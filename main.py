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

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

# Suppress noisy warnings from underlying ML libraries
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="huggingface_hub")

import argparse
import contextlib
import threading
import time

import metrics
import timers
from actions import ActionUnavailable, execute
from barge_in import BargeInListener
from confirm import confirm_action
from fallback_log import log_fallback
from intents import is_wake_echo, route


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
            _announce_due(speak)
            try:
                user_input = input("  Press Enter to speak (or type 'quit'): ").strip()
                if user_input.lower() in ("quit", "exit", "q"):
                    print("  Goodbye!")
                    break

                with metrics.turn() as t:
                    # Record
                    with t.stage(metrics.RECORD):
                        wav_bytes = recorder.record()
                    if not wav_bytes:
                        print("  No audio captured.")
                        continue

                    # Transcribe
                    with t.stage(metrics.STT):
                        text = stt.transcribe(wav_bytes)
                    if not text:
                        print("  Couldn't understand that.")
                        continue
                    print(f"  📝 You said: \"{text}\"")

                    # Process
                    _process_command(text, speak, brain, recorder=recorder, stt=stt, tts=tts, turn=t)

                    print(f"  ⏱️ {t.summary()}")

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
                # Wait for the wake word — or a half-second poll tick when a
                # timer is running, so an idle session still announces on time.
                if wake_event.wait(timeout=timers.poll_interval_s()):
                    wake_event.clear()
                else:
                    _announce_due(speak)
                    continue

                with metrics.turn() as t:
                    # Pause wake word listener during interaction
                    wake_listener.pause()

                    # Acknowledgment
                    with t.stage(metrics.ACK):
                        speak("Yes?")

                    # Record
                    with t.stage(metrics.RECORD):
                        wav_bytes = recorder.record()
                    if not wav_bytes:
                        print("  No audio captured.")
                        wake_listener.resume()
                        continue

                    # Transcribe
                    with t.stage(metrics.STT):
                        text = stt.transcribe(wav_bytes)
                    if not text:
                        with t.stage(metrics.ACK):
                            speak("I didn't catch that.")
                        wake_listener.resume()
                        continue
                    print(f"  📝 You said: \"{text}\"")

                    # Process command
                    _process_command(text, speak, brain, recorder=recorder, stt=stt, tts=tts, turn=t)

                    # Resume wake word listening
                    wake_listener.resume()
                    print(f"  ⏱️ {t.summary()}")

        except KeyboardInterrupt:
            print("\n  Shutting down...")
            wake_listener.stop()
            recorder.cleanup()
            print("  Goodbye!")


def _announce_due(speak_fn) -> None:
    """
    Speak any timer alerts that came due while the loop was idle.

    Full mode polls every half second while timers run. The input()-driven
    modes can only check between inputs: an alert that fires while input()
    blocks waits for the next Enter — the price of never driving TTS from a
    second thread.
    """
    for alert in timers.pop_due():
        print(f"  ⏰ {alert}")
        speak_fn(alert)


def _process_command(text: str, speak_fn, brain, recorder=None, stt=None, tts=None, turn=None):
    """
    Route text through intent router → confirm gate → action.

    Falls back to the Groq LLM in two cases: the router found no intent, or it
    found one whose action turned out to have no handle on the target. Both
    mean the same thing from the user's side — the deterministic path can't
    answer this — so both take the same exit.

    A bare wake-word echo ("hey loki" said as the whole utterance) carries no
    request: return to listening without spending a cloud call on it.
    """
    if is_wake_echo(text):
        print("  👂 Just the wake word — back to listening.")
        if turn:
            turn.path = "wake_echo"
        return

    if turn is None:
        def t_stage(name: str) -> contextlib.AbstractContextManager:
            return contextlib.nullcontext()
    else:
        t_stage = turn.stage

    with t_stage(metrics.ROUTE):
        result = route(text)

    if result is not None:
        intent = result.intent
        if turn:
            turn.intent = intent.name
            turn.tier = intent.tier
        print(f"  ✅ Intent: {intent.name} (Tier {intent.tier})")

        # A phrase the user can actually parse when tier 2/3 speaks it back.
        action_desc = intent.description(result.args)

        with t_stage(metrics.CONFIRM):
            confirmed = confirm_action(
                intent.tier, action_desc,
                tts_fn=speak_fn,
                recorder_fn=recorder.record if recorder else None,
                stt_fn=stt.transcribe if stt else None,
            )
        if not confirmed:
            if turn:
                turn.path = "cancelled"
            speak_fn("Okay, cancelled.")
            return

        try:
            with t_stage(metrics.ACTION):
                success, response = execute(intent.handler, **result.args)
        except ActionUnavailable as e:
            # The router read the intent correctly, but the action layer can't
            # do the job — "open photoshop" when Photoshop isn't installed.
            # The LLM gives a better answer than an apology would.
            print(f"  ↪ {intent.name} can't handle this ({e}) → Groq fallback")
        else:
            if turn:
                turn.path = "action"
            print(f"  {'✅' if success else '❌'} {response}")
            with t_stage(metrics.TTS):
                speak_fn(response)
            return
    else:
        print("  🧠 No intent match → Groq fallback")

    if turn:
        turn.path = "llm"
    _ask_brain(text, speak_fn, brain, tts=tts, stt=stt, turn=turn)


def _ask_brain(text: str, speak_fn, brain, tts=None, stt=None, turn=None) -> None:
    """
    Stream an LLM answer, speaking it sentence by sentence as it arrives.

    Voice mode hands the Groq sentence iterator straight to tts.speak_pipelined(),
    which synthesizes sentence N+1 on its own thread while N plays. That thread is
    also what pulls the iterator, so the Groq stream keeps draining during playback
    too. Speaking each sentence on *this* thread instead serializes network,
    synthesis and playback — a second of dead air between every sentence.

    Text mode (tts is None) keeps the serial loop: there is no audio to overlap and
    printing is instant.

    Supports barge-in: if stt and tts are provided, a concurrent listener
    watches for stop-phrases and kills playback + the Groq stream immediately.

    Logged to the fallback file either way — that log is the backlog of
    intents the router should learn to handle deterministically.
    """
    stop_event = threading.Event()

    # Start barge-in listener if we have the audio components.
    listener: BargeInListener | None = None
    if tts is not None and stt is not None:
        listener = BargeInListener(stt=stt, stop_event=stop_event)
        listener.start()

    # Create a wrapper around ask_stream to time the LLM stream duration
    def _timed_stream():
        t0 = time.perf_counter()
        yield from brain.ask_stream(text, stop_event=stop_event)
        if turn:
            turn.mark(metrics.LLM_TOTAL, (time.perf_counter() - t0) * 1000.0)

    sentences = _timed_stream()
    spoken: list[str] = []

    try:
        if tts is not None:
            def on_play(sentence: str) -> None:
                if turn:
                    turn.first(metrics.LLM_FIRST)
                print(f"  💬 {sentence}")
                spoken.append(sentence)

            tts.speak_pipelined(sentences, stop_event=stop_event, on_play=on_play, turn=turn)
        else:
            for sentence in sentences:
                if stop_event.is_set():
                    break
                print(f"  💬 {sentence}")
                spoken.append(sentence)
                speak_fn(sentence)
    finally:
        # Always clean up the listener, whether we finished or were interrupted.
        if listener is not None:
            listener.stop()

    if stop_event.is_set():
        # Kill any audio still playing and wipe the TTS stopped flag so
        # the acknowledgment line can play.
        if tts is not None:
            tts.stop()   # cuts current sounddevice playback immediately
            tts.reset()  # re-enable speak() for the ack line
        print("  🛑 Barge-in — stopping.")
        speak_fn("Okay.")
        return  # return to wake-word idle state; no fallback log for partial response

    log_fallback(text, " ".join(spoken).strip())


def _text_loop():
    """Text-only interaction loop — no audio at all."""
    from brain import Brain
    brain = Brain()

    def speak(text: str):
        print(f"  🔊 {text}")

    while True:
        _announce_due(speak)
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

        _process_command(text, speak, brain)


if __name__ == "__main__":
    main()
