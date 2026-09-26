"""
LOQI — Speech-to-Text (faster-whisper)

Loads the model ONCE at startup, keeps it resident. Never reloads per request.
Transcribes WAV audio (in-memory bytes from the recorder, or a file path).

Device selection is automatic: with ``device="auto"`` it uses CUDA when a GPU is
present and falls back to CPU otherwise. If a CUDA load fails outright (missing
cuDNN, out of VRAM, driver mismatch) it degrades to CPU/int8 rather than crashing
— a voice assistant must still start.

In-memory audio is decoded directly to a numpy float32 array (the recorder always
emits 16 kHz mono 16-bit PCM), avoiding a temp-file round-trip and the associated
Windows unlink race.
"""

from __future__ import annotations

import io
import wave

import numpy as np
from faster_whisper import WhisperModel

import config
from logging_setup import get_logger

log = get_logger(__name__)

# faster-whisper wants float32 samples in [-1, 1]; 16-bit PCM is [-32768, 32767].
_INT16_TO_FLOAT = 1.0 / 32768.0


class STT:
    """faster-whisper speech-to-text wrapper."""

    def __init__(
        self,
        model_size: str = config.STT_MODEL_SIZE,
        device: str = config.STT_DEVICE,
        compute_type: str = config.STT_COMPUTE_TYPE,
        initial_prompt: str = config.STT_INITIAL_PROMPT,
    ):
        self.initial_prompt = initial_prompt or None
        device, compute_type = self._resolve_device(device, compute_type)

        try:
            self.model = self._load(model_size, device, compute_type)
        except Exception as e:
            if device != "cpu":
                log.warning(
                    "STT failed to init on %s (%s: %s). Falling back to CPU/int8.",
                    device, type(e).__name__, e,
                )
                device, compute_type = "cpu", "int8"
                self.model = self._load(model_size, device, compute_type)
            else:
                raise

        self.model_size, self.device, self.compute_type = model_size, device, compute_type
        log.info("  ✅ STT ready: %s on %s (%s).", model_size, device, compute_type)

    # ------------------------------------------------------------------ public
    def transcribe(self, audio: bytes | str) -> str:
        """
        Transcribe audio to text.

        Args:
            audio: WAV bytes (from the recorder) or a file path string.

        Returns:
            Transcribed text (stripped, segments joined).
        """
        if isinstance(audio, bytes):
            try:
                audio_input: np.ndarray | str | io.BytesIO = _decode_wav(audio)
            except Exception as e:
                # Unexpected container/format — let faster-whisper's own decoder
                # (PyAV) handle it from a file-like object.
                log.warning("Direct WAV decode failed (%s); using fallback decoder.", e)
                audio_input = io.BytesIO(audio)
        else:
            audio_input = audio

        return self._run(audio_input)

    # --------------------------------------------------------------- internals
    def _run(self, audio_input: np.ndarray | str | io.BytesIO) -> str:
        try:
            return self._transcribe_segments(audio_input)
        except RuntimeError as e:
            # Catches missing CUDA runtime libraries (cublas64_12.dll,
            # cudnn, etc.) that only surface on the first encode() call
            # even though the model loaded without error.
            if self.device != "cpu":
                log.warning(
                    "STT inference failed on %s (%s). Falling back to CPU/int8.",
                    self.device, e,
                )
                self._fallback_to_cpu()
                return self._transcribe_segments(audio_input)
            raise

    def _transcribe_segments(self, audio_input: np.ndarray | str | io.BytesIO) -> str:
        segments, _info = self.model.transcribe(
            audio_input,
            beam_size=5,
            language="en",
            vad_filter=True,            # Silero VAD trims non-speech
            initial_prompt=self.initial_prompt,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def _fallback_to_cpu(self) -> None:
        """Reload the model on CPU/int8 after a GPU failure."""
        self.device, self.compute_type = "cpu", "int8"
        self.model = self._load(self.model_size, self.device, self.compute_type)
        log.info("  ✅ STT re-initialized on CPU/int8.")

    def _load(self, model_size: str, device: str, compute_type: str) -> WhisperModel:
        log.info("  Loading STT model: %s on %s (%s)...", model_size, device, compute_type)
        return WhisperModel(model_size, device=device, compute_type=compute_type)

    @staticmethod
    def _resolve_device(device: str, compute_type: str) -> tuple[str, str]:
        """Resolve 'auto' to cuda/cpu and coerce an unsupported CPU compute type."""
        if device == "auto":
            device = "cuda" if _cuda_available() else "cpu"
        if device == "cpu" and compute_type in ("float16", "fp16", "auto", ""):
            # CPU cannot run float16; use the standard CPU quantization.
            compute_type = "int8"
        return device, compute_type


def _cuda_available() -> bool:
    """True if CTranslate2 reports a usable CUDA device (no torch dependency)."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _decode_wav(audio: bytes) -> np.ndarray:
    """Decode 16-bit PCM WAV bytes to a mono float32 array in [-1, 1]."""
    with wave.open(io.BytesIO(audio), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        raise ValueError(f"expected 16-bit PCM, got {sampwidth * 8}-bit")

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) * _INT16_TO_FLOAT
    if n_channels > 1:
        # Downmix to mono by averaging channels.
        samples = samples.reshape(-1, n_channels).mean(axis=1)
    return samples
