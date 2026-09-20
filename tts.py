"""
LOQI — Text-to-Speech (Kokoro-82M)

Loads KPipeline ONCE at startup, keeps resident.
Supports sentence-boundary chunking for streaming playback:
  - Split text on sentence boundaries (.!?)
  - Synthesize + play each sentence immediately
  - Enables overlapped playback with Groq streaming (sentence 1 plays
    while sentence 2 is still generating/synthesizing)

Requires espeak-ng installed on the system.
"""

import re
import os
import warnings
import numpy as np
import sounddevice as sd

# Suppress noisy ML library warnings before importing Kokoro
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

from kokoro import KPipeline

import config


class TTS:
    """Kokoro TTS wrapper with sentence-level streaming."""

    def __init__(
        self,
        lang_code: str = config.TTS_LANG_CODE,
        voice: str = config.TTS_VOICE,
        sample_rate: int = config.TTS_SAMPLE_RATE,
    ):
        print(f"  Loading TTS model (Kokoro, voice={voice})...")
        self.pipeline = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M")
        self.voice = voice
        self.sample_rate = sample_rate
        print(f"  ✅ TTS model loaded.")

    def speak(self, text: str):
        """
        Synthesize and play text aloud. Blocks until playback finishes.

        For short texts (single sentence), synthesizes and plays directly.
        For longer texts, uses sentence-level chunking.
        """
        if not text or not text.strip():
            return

        # Kokoro's pipeline returns a generator of (graphemes, phonemes, audio)
        # Each call to the generator produces one segment of audio
        for _, _, audio in self.pipeline(text, voice=self.voice):
            if audio is not None and len(audio) > 0:
                # Play audio and wait for it to finish
                sd.play(audio, samplerate=self.sample_rate)
                sd.wait()

    def speak_streamed(self, text_generator):
        """
        Speak text as it arrives from a streaming source (e.g., Groq LLM).

        Args:
            text_generator: An iterable/generator yielding sentence strings
                            as they become available.

        This enables overlapped playback: sentence 1 plays while sentence 2
        is still being generated/synthesized.
        """
        for sentence in text_generator:
            sentence = sentence.strip()
            if not sentence:
                continue
            self.speak(sentence)

    def synthesize(self, text: str) -> np.ndarray:
        """
        Synthesize text to a numpy audio array without playing it.

        Returns:
            numpy array of audio samples at self.sample_rate.
        """
        audio_parts = []
        for _, _, audio in self.pipeline(text, voice=self.voice):
            if audio is not None and len(audio) > 0:
                audio_parts.append(audio)

        if not audio_parts:
            return np.array([], dtype=np.float32)

        return np.concatenate(audio_parts)


def split_sentences(text: str) -> list[str]:
    """
    Split text into sentences on boundaries: . ! ? followed by whitespace
    or end-of-string. Keeps the punctuation with the sentence.

    Used by brain.py to chunk Groq streaming output.
    """
    # Split on sentence-ending punctuation followed by space or end
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]
