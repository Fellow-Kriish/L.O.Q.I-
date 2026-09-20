"""Interactive test script for Wake Word Listener."""
import sys
import time
sys.stdout.reconfigure(encoding="utf-8")

from wakeword import WakeWordListener

def on_wake(model, score):
    print(f"\n✅ Wake word detected! [model: {model}, score: {score:.3f}]")

def main():
    print("=" * 60)
    print("  LOQI Wake Word Test Harness")
    print("=" * 60)
    
    print("\n[!] Loading openWakeWord model...")
    try:
        listener = WakeWordListener(on_detected=on_wake)
    except Exception as e:
        print(f"❌ Failed to initialize WakeWordListener: {e}")
        return

    print("\n[!] Starting listener...")
    listener.start()
    
    print("[-] Say 'Hey Loki' to test detection.")
    print("    (Press Ctrl+C to stop)")
    
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nTest cancelled by user.")
    finally:
        listener.stop()

if __name__ == "__main__":
    main()
