"""Interactive test script for STT and Recorder."""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from recorder import Recorder
from stt import STT


def main():
    print("=" * 60)
    print("  LOQI STT Test Harness")
    print("=" * 60)

    # Initialize components
    stt = STT()
    recorder = Recorder()

    print("\n[!] Please speak into your microphone.")
    print("    (Recording will automatically stop when you stop speaking)")
    print("-" * 60)

    try:
        # Record audio
        audio_bytes = recorder.record()

        if not audio_bytes:
            print("No audio recorded (or silence).")
            return

        print(f"\n[+] Audio recorded: {len(audio_bytes)} bytes")

        # Transcribe
        print("[-] Transcribing...")
        text = stt.transcribe(audio_bytes)

        print("\n" + "=" * 60)
        print(f"  Transcription Result: \"{text}\"")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\nTest cancelled by user.")
    finally:
        recorder.cleanup()

if __name__ == "__main__":
    main()
