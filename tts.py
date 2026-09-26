"""
LOQI — Text-to-Speech (Kokoro-82M)

Loads KPipeline ONCE at startup, keeps resident.
Supports sentence-boundary chunking for streaming playback:
  - Split text on sentence boundaries (.!?)
  - Synthesize + play each sentence immediately
  - Enables overlapped playback with Groq streaming (sentence 1 plays
    while sentence 2 is still generating/synthesizing)

Canned-line cache: a handful of lines are spoken verbatim on almost every
interaction — "Yes?" fires on *every* wake. Those are synthesized once in a
background thread at startup and replayed from memory, so the wake
acknowledgement is instant instead of paying synthesis latency each time. That
first synthesis also absorbs the one-off espeak-ng/model warm-up cost that would
otherwise land on the user's first real response.

Requires espeak-ng installed on the system.
"""

import os
import re
import threading
import warnings

import numpy as np
import sounddevice as sd

# Suppress noisy ML library warnings before importing Kokoro
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

from kokoro import KPipeline

import config
from logging_setup import get_logger

log = get_logger(__name__)

# Fixed lines spoken verbatim by main.py / confirm.py. Tier 2/3 confirmations are
# f-strings built from the action description and so cannot be pre-synthesized.
CANNED_LINES: tuple[str, ...] = (
    "Yes?",                         # every wake — the latency that matters most
    "I didn't catch that.",
    "Okay, cancelled.",
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

        log.info("  ✅ TTS model loaded.")

        if precache:
            # Off the startup path: the cache fills while the user reads the banner.
            threading.Thread(target=self._warm, name="tts-warm", daemon=True).start()

    # ------------------------------------------------------------------ public
    def speak(self, text: str) -> None:
        """Synthesize and play text aloud. Blocks until playback finishes."""
        if not text or not text.strip():
            return

        key = text.strip()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._play(cached)
                return

            for _, _, audio in self.pipeline(text, voice=self.voice):
                if audio is not None and len(audio) > 0:
                    self._play(audio)

    def speak_streamed(self, text_generator) -> None:
        """
        Speak text as it arrives from a streaming source (e.g., Groq LLM).

        Args:
            text_generator: An iterable/generator yielding sentence strings
                            as they become available.
        """
        for sentence in text_generator:
            self.speak(sentence)

    def synthesize(self, text: str) -> np.ndarray:
        """Synthesize text to a numpy audio array without playing it."""
        with self._lock:
            return self._synth(text)

    # --------------------------------------------------------------- internals
    def _play(self, audio: np.ndarray) -> None:
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
