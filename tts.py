"""
LOQI — Text-to-Speech (Kokoro-82M)

Loads KPipeline ONCE at startup, keeps resident.
Supports sentence-boundary chunking for streaming playback:
  - Split text on sentence boundaries (.!?)
  - Synthesize + play each sentence immediately
  - speak_pipelined() overlaps synthesis of sentence N+1 with playback of
    sentence N via a bounded producer-consumer queue — zero dead-air gap.

Canned-line cache: a handful of lines are spoken verbatim on almost every
interaction — "Yes?" fires on *every* wake. Those are synthesized once in a
background thread at startup and replayed from memory, so the wake
acknowledgement is instant instead of paying synthesis latency each time. That
first synthesis also absorbs the one-off espeak-ng/model warm-up cost that would
otherwise land on the user's first real response.

Requires espeak-ng installed on the system.
"""

import contextlib
import os
import queue
import re
import threading
import time
import warnings

import numpy as np
import sounddevice as sd

# Suppress noisy ML library warnings before importing Kokoro
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

from kokoro import KPipeline

import config
import metrics
from logging_setup import get_logger

log = get_logger(__name__)

# Fixed lines spoken verbatim by main.py / confirm.py. Tier 2/3 confirmations are
# f-strings built from the action description and so cannot be pre-synthesized.
CANNED_LINES: tuple[str, ...] = (
    "Yes?",                         # every wake — the latency that matters most
    "I didn't catch that.",
    "Okay, cancelled.",
    "Okay.",                        # barge-in acknowledgment — spoken after every interrupt
    "Please say yes to confirm, or say nothing to cancel.",
)

# Kokoro pads roughly 0.4 s of leading and 0.5 s of trailing silence onto every
# clip. speak() blocks on playback, so for "Yes?" that is ~0.9 s of dead air
# before the recorder starts listening. Cached lines are fixed, known content,
# so trimming their edges is safe. Live/streamed speech is left untouched —
# inter-sentence silence there is natural prosody, not padding.
_TRIM_THRESHOLD = 0.01   # fraction of peak amplitude counted as speech
_TRIM_PAD_MS = 30        # keep a little padding so it never sounds clipped


class TTS:
    """Kokoro TTS wrapper with sentence-level streaming and a canned-line cache."""

    def __init__(
        self,
        lang_code: str = config.TTS_LANG_CODE,
        voice: str = config.TTS_VOICE,
        sample_rate: int = config.TTS_SAMPLE_RATE,
        precache: bool = True,
    ):
        log.info("  Loading TTS model (Kokoro, voice=%s)...", voice)
        self.pipeline = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M")
        self.voice = voice
        self.sample_rate = sample_rate

        # One synthesis/playback at a time: the pipeline is not reentrant and
        # two overlapping voices would be unintelligible anyway.
        self._lock = threading.RLock()
        self._cache: dict[str, np.ndarray] = {}

        # Barge-in: set by stop() to cut playback mid-word.
        self._stopped = threading.Event()

        log.info("  ✅ TTS model loaded.")

        if precache:
            # Off the startup path: the cache fills while the user reads the banner.
            threading.Thread(target=self._warm, name="tts-warm", daemon=True).start()

    # ------------------------------------------------------------------ public
    def speak(self, text: str, turn=None) -> None:
        """Synthesize and play text aloud. Blocks until playback finishes."""
        if not text or not text.strip():
            return
        if self._stopped.is_set():
            return

        key = text.strip()
        with self._lock:
            if self._stopped.is_set():
                return
            cached = self._cache.get(key)
            if cached is not None:
                t0 = time.perf_counter()
                self._play(cached)
                if turn:
                    turn.mark(metrics.TTS, (time.perf_counter() - t0) * 1000.0)
                return

            for _, _, audio in self.pipeline(text, voice=self.voice):
                if audio is not None and len(audio) > 0:
                    if self._stopped.is_set():
                        return
                    t0 = time.perf_counter()
                    self._play(audio)
                    if turn:
                        turn.mark(metrics.TTS, (time.perf_counter() - t0) * 1000.0)

    def stop(self) -> None:
        """
        Immediately stop all playback and prevent any further speak() calls
        from playing audio. Called by barge-in on interrupt.

        Clears the stopped flag on the next speak() so the acknowledgment
        line ("okay") can still play after reset() is called.
        """
        self._stopped.set()
        sd.stop()  # cuts the current sounddevice playback mid-word

    def reset(self) -> None:
        """Clear the stopped flag so normal speech can resume."""
        self._stopped.clear()

    def speak_streamed(self, text_generator) -> None:
        """
        Speak text as it arrives from a streaming source (e.g., Groq LLM).
        Serial fallback — for text-mode where latency doesn't matter.
        For voice mode, prefer speak_pipelined() which overlaps synthesis
        with playback.

        Args:
            text_generator: An iterable/generator yielding sentence strings
                            as they become available.
        """
        for sentence in text_generator:
            self.speak(sentence)

    def speak_pipelined(
        self,
        sentence_iter,
        stop_event: threading.Event | None = None,
        on_play=None,
        turn=None,
    ) -> None:
        """
        Overlap synthesis with playback — synthesize sentence N+1 while N plays.

        Architecture (same producer-consumer pattern as barge-in):

          [Synth thread]    — pulls sentences from the iterator, synthesizes
                              each to audio (lock held only during synthesis),
                              pushes (text, audio) to a bounded queue.

          [Caller thread]   — pops from the queue, plays audio. While sd.wait()
                              blocks here, the synth thread is already working
                              on the next sentence — zero dead-air gap.

        The lock is acquired per-synthesis, NOT across playback, so synthesis of
        sentence N+1 runs concurrently with playback of sentence N.

        Args:
            sentence_iter: Iterator yielding sentence strings (e.g. brain.ask_stream()).
            stop_event: Optional barge-in Event. When set, synthesis and playback
                        both bail immediately.
            on_play: Optional callback(sentence_text) called just before each
                     sentence starts playing — use for logging / printing.
        """
        # Bounded: synth can run ≤3 sentences ahead of playback. If playback is
        # slow, synth blocks on put(). If synth is slow, playback blocks on get().
        # Either way, no unbounded pile-up.
        _PIPELINE_QUEUE_SIZE = 3
        audio_q: queue.Queue[tuple[str, np.ndarray] | None] = queue.Queue(
            maxsize=_PIPELINE_QUEUE_SIZE
        )

        def _synth_worker():
            """Consume sentences, synthesize to audio, push to queue."""
            try:
                for sentence in sentence_iter:
                    if self._stopped.is_set():
                        break
                    if stop_event is not None and stop_event.is_set():
                        break
                    if not sentence or not sentence.strip():
                        continue

                    key = sentence.strip()
                    cached = self._cache.get(key)
                    if cached is not None:
                        audio = cached
                    else:
                        # Lock held ONLY during synthesis — released before queue.put
                        # and therefore before playback of this audio starts.
                        with self._lock:
                            if self._stopped.is_set():
                                break
                            t0 = time.perf_counter()
                            audio = self._synth(sentence)
                            if turn:
                                turn.mark(metrics.TTS, (time.perf_counter() - t0) * 1000.0)

                    if audio.size == 0:
                        continue
                    if self._stopped.is_set():
                        break
                    if stop_event is not None and stop_event.is_set():
                        break

                    # Blocks if queue full (synth 3 sentences ahead) — that's fine,
                    # it just means we're far enough ahead already. Bounded, though:
                    # on barge-in the playback loop exits without draining, so an
                    # untimed put() would park this thread here for the life of the
                    # process. Retry against the stop flags instead.
                    while True:
                        try:
                            audio_q.put((sentence, audio), timeout=0.25)
                            break
                        except queue.Full:
                            if self._stopped.is_set() or (
                                stop_event is not None and stop_event.is_set()
                            ):
                                return
            except Exception as e:
                log.error("TTS synth worker error: %s", e)
            finally:
                # Sentinel: tell playback loop there's nothing left. Skipped when
                # stopping — the playback loop has already exited on the same flag,
                # and the queue is full and undrained, so waiting out the timeout
                # would only delay the barge-in acknowledgment the caller speaks
                # next.
                stopping = self._stopped.is_set() or (
                    stop_event is not None and stop_event.is_set()
                )
                if not stopping:
                    with contextlib.suppress(queue.Full):
                        audio_q.put(None, timeout=2.0)

        synth_thread = threading.Thread(
            target=_synth_worker, name="tts-synth-pipeline", daemon=True
        )
        synth_thread.start()

        try:
            while True:
                if self._stopped.is_set():
                    break
                if stop_event is not None and stop_event.is_set():
                    break

                try:
                    item = audio_q.get(timeout=0.5)
                except queue.Empty:
                    # Synth thread may still be working or iterator may still be
                    # waiting for the next Groq sentence.
                    if not synth_thread.is_alive():
                        break  # synth thread exited — nothing more coming
                    continue

                if item is None:
                    break  # sentinel — no more sentences

                sentence, audio = item
                if self._stopped.is_set():
                    break
                if stop_event is not None and stop_event.is_set():
                    break

                if on_play is not None:
                    on_play(sentence)
                t0 = time.perf_counter()
                self._play(audio)
                if turn:
                    turn.mark(metrics.TTS, (time.perf_counter() - t0) * 1000.0)
        finally:
            # Make sure synth thread exits if we bailed early (barge-in).
            # It checks _stopped / stop_event on each iteration, so it will
            # exit on its own, but we join to be clean.
            synth_thread.join(timeout=3.0)

    def synthesize(self, text: str) -> np.ndarray:
        """Synthesize text to a numpy audio array without playing it."""
        with self._lock:
            return self._synth(text)

    # --------------------------------------------------------------- internals
    def _play(self, audio: np.ndarray) -> None:
        if self._stopped.is_set():
            return
        sd.play(audio, samplerate=self.sample_rate)
        sd.wait()

    def _synth(self, text: str) -> np.ndarray:
        """Synthesize to an array. Caller must hold the lock."""
        parts = [
            audio
            for _, _, audio in self.pipeline(text, voice=self.voice)
            if audio is not None and len(audio) > 0
        ]
        if not parts:
            return np.array([], dtype=np.float32)
        return np.concatenate(parts)

    def _warm(self) -> None:
        """Pre-synthesize the canned lines. Runs once, on a daemon thread."""
        for line in CANNED_LINES:
            try:
                with self._lock:
                    audio = self._synth(line)
            except Exception as e:
                # Missing espeak-ng, no model, etc. Not fatal: speak() will just
                # synthesize on demand (and surface the real error then).
                log.debug("TTS pre-cache stopped at %r (%s: %s).", line, type(e).__name__, e)
                return
            if audio.size:
                self._cache[line] = _trim_silence(audio, self.sample_rate)
        log.debug("TTS pre-cached %d canned lines.", len(self._cache))


def _trim_silence(
    audio: np.ndarray,
    sample_rate: int,
    threshold: float = _TRIM_THRESHOLD,
    pad_ms: int = _TRIM_PAD_MS,
) -> np.ndarray:
    """Strip leading/trailing silence, keeping a short pad on each side."""
    envelope = np.abs(audio)
    peak = float(envelope.max()) if envelope.size else 0.0
    if peak <= 0.0:
        return audio

    voiced = np.nonzero(envelope > threshold * peak)[0]
    if voiced.size == 0:
        return audio

    pad = int(sample_rate * pad_ms / 1000)
    start = max(0, int(voiced[0]) - pad)
    end = min(audio.size, int(voiced[-1]) + 1 + pad)
    return audio[start:end]


def split_sentences(text: str) -> list[str]:
    """
    Split text into sentences on boundaries: . ! ? followed by whitespace
    or end-of-string. Keeps the punctuation with the sentence.

    Public utility for callers that need to pre-split text before passing
    it to speak() or speak_streamed().
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]
