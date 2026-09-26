"""
LOQI — Wake Word Listener (openWakeWord)

Runs on its own thread. Continuously reads 80ms chunks from the mic,
feeds them to the wake word model. On detection, signals the main loop
to start recording.

Thread is never blocked by TTS playback or in-flight LLM calls.
"""

import threading
from collections.abc import Callable

import numpy as np
import pyaudio
from openwakeword.model import Model as OWWModel

import config
from audio_devices import resolve_mic_index


class WakeWordListener:
    """Background thread that listens for the wake word."""

    def __init__(
        self,
        model_path: str = config.WAKE_WORD_MODEL,
        threshold: float = config.WAKE_WORD_THRESHOLD,
        sample_rate: int = config.AUDIO_SAMPLE_RATE,
        chunk_samples: int = config.WAKE_WORD_CHUNK_SAMPLES,
        on_detected: Callable[..., None] | None = None,
    ):
        """
        Args:
            model_path: Built-in model name (e.g. "hey_jarvis") or path to .onnx file.
            threshold: Detection confidence threshold (0.0 – 1.0).
            sample_rate: Audio sample rate (must be 16000).
            chunk_samples: Samples per chunk (1280 = 80ms at 16kHz).
            on_detected: Callback function called when wake word is detected.
                         Signature: on_detected(model_name: str, score: float)
        """
        self.model_path = model_path
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.chunk_samples = chunk_samples
        self.on_detected = on_detected

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._paused.set()  # Start unpaused

        # Load model
        print(f"  Loading wake word model ({model_path})...")
        self.oww_model = OWWModel(wakeword_models=[model_path], inference_framework='onnx')
        print("  ✅ Wake word model loaded.")

    def start(self):
        """Start the listener thread."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._paused.set()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        assert self._thread is not None
        self._thread.start()
        print("  👂 Wake word listener started.")

    def stop(self):
        """Stop the listener thread."""
        self._stop_event.set()
        self._paused.set()  # Unpause so thread can exit
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        print("  ⏹️  Wake word listener stopped.")

    def pause(self):
        """Temporarily pause listening (e.g., while LOQI is speaking)."""
        self._paused.clear()

    def resume(self):
        """Resume listening after pause."""
        # Reset model predictions to avoid false triggers from residual audio
        self.oww_model.reset()
        self._paused.set()

    def _listen_loop(self):
        """Main listener loop — runs on its own thread."""
        pa = pyaudio.PyAudio()
        stream = None

        def _open_stream():
            mic_idx = resolve_mic_index(pa)
            try:
                return pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=self.sample_rate,
                    input=True,
                    input_device_index=mic_idx,
                    frames_per_buffer=self.chunk_samples,
                )
            except Exception as e:
                # The resolved device can still be busy or claimed by another app.
                if mic_idx is None:
                    raise
                print(f"  ⚠️  Failed to open mic index {mic_idx} ({e}). Falling back to default.")
                return pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=self.sample_rate,
                    input=True,
                    input_device_index=None,
                    frames_per_buffer=self.chunk_samples,
                )

        try:
            stream = _open_stream()

            while not self._stop_event.is_set():
                if not self._paused.is_set():
                    # Close the PyAudio stream to prevent buffer buildup and host errors
                    if stream:
                        try:
                            stream.stop_stream()
                            stream.close()
                        except Exception:
                            pass
                        stream = None

                    # Block until resumed
                    self._paused.wait()

                    if self._stop_event.is_set():
                        break

                    # Resumed: reopen the stream
                    try:
                        stream = _open_stream()
                    except Exception as e:
                        print(f"  ❌ Failed to reopen mic stream: {e}")
                        break

                try:
                    if stream:
                        chunk_bytes = stream.read(self.chunk_samples, exception_on_overflow=False)
                    else:
                        continue
                except OSError:
                    continue

                # Convert to int16 numpy array
                chunk = np.frombuffer(chunk_bytes, dtype=np.int16)

                # Get prediction
                prediction = self.oww_model.predict(chunk)

                # Check each model's score
                for model_name, score in prediction.items():
                    if score > self.threshold:
                        print(f"  🔔 Wake word detected! ({model_name}: {score:.3f})")
                        # Reset model to avoid re-triggering
                        self.oww_model.reset()
                        # Call the callback
                        if self.on_detected:
                            self.on_detected(model_name, score)
                        break

        except Exception as e:
            print(f"  ❌ Wake word listener error: {e}")
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            pa.terminate()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
