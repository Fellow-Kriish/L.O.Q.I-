"""
LOQI — Barge-In Listener

Runs a lightweight concurrent listener WHILE TTS is speaking.
Detects stop-phrases spoken by the user and fires a threading.Event so the
caller can immediately kill audio playback, cancel the Groq stream, and wipe
the TTS queue.

Architecture — producer / consumer (v2)
────────────────────────────────────────
Two threads, split so the mic is NEVER unread:

  [Reader thread]       — owns the PyAudio stream, reads mic 100% of the time,
                          runs WebRTC VAD, assembles speech bursts, enqueues them.
                          Never pauses for transcription (zero blind window).

  [Transcriber thread]  — pops bursts, calls stt.transcribe_quick() (beam_size=1,
                          no internal VAD), matches stop-phrases, fires stop_event.

v1 was serial (capture → transcribe → capture). The ~300-500 ms Whisper takes
caused a blind window where any speech was lost. Users had to hit the narrow
gap between calls, explaining the 1-in-4 hit rate. v2 fixes this.

False-trigger guard (speaker bleed)
────────────────────────────────────
• VAD aggressiveness = 3 (strictest) — speaker output is lower energy at mic.
• Onset threshold = 4 consecutive frames (120 ms) — bleed is intermittent,
  human "stop" is sustained.
• Real STT confirm: Kokoro TTS almost never says "stop", "cancel", "okay loki".

Usage (from main.py)
────────────────────
    stop_event = threading.Event()
    listener = BargeInListener(stt=stt, stop_event=stop_event)
    listener.start()
    ...speak / stream Groq...
    listener.stop()        # call when TTS finishes normally (no barge-in)
"""

from __future__ import annotations

import collections
import contextlib
import io
import queue
import threading
import wave
from typing import TYPE_CHECKING

import pyaudio
import webrtcvad

from audio_devices import resolve_mic_index
from logging_setup import get_logger

if TYPE_CHECKING:
    from stt import STT

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stop-phrase list — keep ≤6, short, unambiguous.
# Matched as lowercased substrings in the whisper transcript.
# ---------------------------------------------------------------------------
STOP_PHRASES: tuple[str, ...] = (
    "okay loki",
    "ok loki",
    "stop",
    "never mind",
    "nevermind",
    "cancel",
)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
_SAMPLE_RATE = 16_000
_CHUNK_MS = 30
_CHUNK_SAMPLES = int(_SAMPLE_RATE * _CHUNK_MS / 1_000)   # 480 samples

_VAD_AGGRESSIVENESS = 3    # 0-3; 3 = strictest -> fewer bleed false triggers
_SPEECH_ONSET_FRAMES = 4   # 4x30ms = 120ms sustained speech before buffering
                            # (speaker bleed is intermittent -- this filters it)
_SILENCE_END_FRAMES = 12   # 12x30ms = 360ms silence -> end of burst
_MAX_CAPTURE_FRAMES = 100  # hard cap ~3 s; avoids runaway on sustained noise

_BURST_QUEUE_SIZE = 3      # max bursts buffered between reader and transcriber


class BargeInListener:
    """
    Listens on the mic concurrently with TTS playback.

    Call start() before TTS begins, stop() when TTS finishes normally.
    If the user says a stop-phrase, stop_event is set and both internal
    threads exit — the caller must react to stop_event.
    """

    def __init__(
        self,
        stt: STT,
        stop_event: threading.Event,
        stop_phrases: tuple[str, ...] = STOP_PHRASES,
    ) -> None:
        self._stt = stt
        self._stop_event = stop_event
        self._stop_phrases = stop_phrases
        self._running = threading.Event()
        self._burst_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=_BURST_QUEUE_SIZE)
        self._reader_thread: threading.Thread | None = None
        self._transcriber_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Start both background threads (reader + transcriber)."""
        self._running.set()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="barge-in-reader",
            daemon=True,
        )
        self._transcriber_thread = threading.Thread(
            target=self._transcriber_loop,
            name="barge-in-transcriber",
            daemon=True,
        )
        self._reader_thread.start()
        self._transcriber_thread.start()
        log.debug("Barge-in listener started (reader + transcriber).")

    def stop(self) -> None:
        """Signal both threads to exit cleanly (called when TTS finishes normally)."""
        self._running.clear()
        # Unblock the transcriber if it's waiting on an empty queue.
        with contextlib.suppress(queue.Full):
            self._burst_queue.put_nowait(None)
        for t in (self._reader_thread, self._transcriber_thread):
            if t and t.is_alive():
                t.join(timeout=1.5)
        log.debug("Barge-in listener stopped.")

    # --------------------------------------------------------- reader thread

    def _reader_loop(self) -> None:
        """
        Owns the PyAudio stream. Reads mic frames 100% of the time.
        Assembles speech bursts and puts them on the queue for transcription.
        Never pauses for transcription — zero blind window on mic side.
        """
        pa = pyaudio.PyAudio()
        vad = webrtcvad.Vad(_VAD_AGGRESSIVENESS)

        try:
            mic_idx = resolve_mic_index(pa)
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=_SAMPLE_RATE,
                input=True,
                input_device_index=mic_idx,
                frames_per_buffer=_CHUNK_SAMPLES,
            )
        except Exception as e:
            log.warning("Barge-in reader: could not open mic stream (%s).", e)
            pa.terminate()
            return

        try:
            while self._running.is_set() and not self._stop_event.is_set():
                burst = self._capture_burst(stream, vad)
                if burst is None:
                    continue
                # Drop silently if transcriber is backed up.
                try:
                    self._burst_queue.put_nowait(burst)
                except queue.Full:
                    log.debug("Barge-in: burst queue full, dropping burst.")
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
            pa.terminate()
            # Sentinel: unblock transcriber.
            with contextlib.suppress(queue.Full):
                self._burst_queue.put_nowait(None)

    # ------------------------------------------------------ transcriber thread

    def _transcriber_loop(self) -> None:
        """Pops bursts from the queue, transcribes, fires stop_event on match."""
        while self._running.is_set() and not self._stop_event.is_set():
            try:
                burst = self._burst_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if burst is None:
                break  # sentinel from reader shutdown or stop()

            transcript = self._stt.transcribe_quick(burst)
            log.debug("Barge-in transcript: %r", transcript)

            if self._matches_stop_phrase(transcript):
                log.info("Barge-in triggered on %r.", transcript)
                self._stop_event.set()
                break

    # --------------------------------------------------------- burst capture

    def _capture_burst(self, stream, vad: webrtcvad.Vad) -> bytes | None:
        """
        Wait for a speech burst and return WAV bytes.
        Returns None if the listener was stopped mid-capture.
        """
        frames: list[bytes] = []
        onset_count = 0
        silence_count = 0
        in_speech = False

        # Pre-speech ring buffer: keeps audio before onset so we don't clip
        # the very start of the word.
        pre_buf: collections.deque[bytes] = collections.deque(maxlen=_SPEECH_ONSET_FRAMES + 2)

        while self._running.is_set() and not self._stop_event.is_set():
            try:
                chunk = stream.read(_CHUNK_SAMPLES, exception_on_overflow=False)
            except OSError:
                break

            is_speech = vad.is_speech(chunk, _SAMPLE_RATE)

            if not in_speech:
                pre_buf.append(chunk)
                if is_speech:
                    onset_count += 1
                    if onset_count >= _SPEECH_ONSET_FRAMES:
                        in_speech = True
                        frames.extend(pre_buf)
                else:
                    onset_count = 0
            else:
                frames.append(chunk)
                if is_speech:
                    silence_count = 0
                else:
                    silence_count += 1

                if silence_count >= _SILENCE_END_FRAMES:
                    break  # natural end of burst
                if len(frames) >= _MAX_CAPTURE_FRAMES:
                    break  # safety cap

        if not frames or not self._running.is_set():
            return None

        return _frames_to_wav(frames)

    # --------------------------------------------------------- phrase matching

    def _matches_stop_phrase(self, transcript: str) -> bool:
        """Return True if any stop-phrase is a substring of the transcript."""
        if not transcript:
            return False
        return any(phrase in transcript for phrase in self._stop_phrases)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _frames_to_wav(frames: list[bytes]) -> bytes:
    """Convert raw 16-bit PCM frames to in-memory WAV bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()
