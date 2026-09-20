"""
LOQI — Speech-to-Text (faster-whisper)

Loads the model ONCE at startup, keeps it resident. Never reloads per request.
Transcribes WAV audio (in-memory bytes or file path) to text.
"""

import io
import tempfile
import os

from faster_whisper import WhisperModel

import config


class STT:
    """faster-whisper speech-to-text wrapper."""

    def __init__(
        self,
        model_size: str = config.STT_MODEL_SIZE,
        device: str = config.STT_DEVICE,
        compute_type: str = config.STT_COMPUTE_TYPE,
    ):
        print(f"  Loading STT model ({model_size}, {device}, {compute_type})...")
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        print(f"  ✅ STT model loaded.")

    def transcribe(self, audio: bytes | str) -> str:
        """
        Transcribe audio to text.

        Args:
            audio: Either WAV file bytes (from Recorder) or a file path string.

        Returns:
            Transcribed text (stripped, joined segments).
        """
        # faster-whisper needs a file path or file-like object
        if isinstance(audio, bytes):
            # Write to a temp file, transcribe, then clean up
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            try:
                tmp.write(audio)
                tmp.close()
                return self._transcribe_file(tmp.name)
            finally:
                os.unlink(tmp.name)
        else:
            return self._transcribe_file(audio)

    def _transcribe_file(self, path: str) -> str:
        """Transcribe a WAV file and return the joined text."""
        segments, info = self.model.transcribe(
            path,
            beam_size=5,
            language="en",
            vad_filter=True,  # Use Silero VAD to filter out silence
        )

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        result = " ".join(text_parts).strip()
        return result
