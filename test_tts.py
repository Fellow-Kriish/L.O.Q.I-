"""Quick automated test for TTS."""
import sys

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

from tts import TTS


def main():
    print("=" * 60)
    print("  LOQI TTS Test Harness")
    print("=" * 60)

    try:
        # Initialize component
        tts = TTS()

        print("\n[!] Synthesizing and playing text...")
        tts.speak("Hello there! This is a test of the text to speech system.")
        print("  ✅ TTS playback finished successfully.")

    except Exception as e:
        print(f"\n❌ Error during TTS playback: {e}")

if __name__ == "__main__":
    main()
