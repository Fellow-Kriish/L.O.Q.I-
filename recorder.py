"""
LOQI — Microphone Recorder with VAD-based Endpoint Detection

Records 16kHz mono 16-bit PCM from the default mic.
Uses webrtcvad to detect speech start/end — stops recording after
sustained silence, not a fixed timer.

Returns raw audio bytes ready for faster-whisper.

pyaudio and webrtcvad are imported lazily — at first mic open / first
record() — not at module load. The endpointing logic in record() is pure
Python and is tested in CI, which installs no audio wheels; a top-level import
would make the whole module unimportable there and the tests would only ever
skip.
"""

from __future__ import annotations

import collections
import io
import wave
from typing import TYPE_CHECKING

import config
from audio_devices import resolve_mic_index

if TYPE_CHECKING:
    import pyaudio
    import webrtcvad


class Recorder:
    """VAD-based microphone recorder."""

    def __init__(
        self,
        sample_rate: int = config.AUDIO_SAMPLE_RATE,
        channels: int = config.AUDIO_CHANNELS,
        chunk_ms: int = config.AUDIO_CHUNK_MS,
        vad_aggressiveness: int = config.VAD_AGGRESSIVENESS,
        silence_timeout_ms: int = config.VAD_SILENCE_TIMEOUT_MS,
        min_speech_ms: int = config.VAD_MIN_SPEECH_MS,
        max_utterance_ms: int = config.VAD_MAX_UTTERANCE_MS,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.chunk_samples = int(sample_rate * chunk_ms / 1000)
        self.chunk_bytes = self.chunk_samples * 2  # 16-bit = 2 bytes/sample

        self.silence_timeout_ms = silence_timeout_ms
        self.min_speech_ms = min_speech_ms
        self.max_utterance_ms = max_utterance_ms

        self.vad_aggressiveness = vad_aggressiveness
        # Built on first record() so constructing a Recorder needs no audio
        # stack. Tests replace it with a scripted VAD before that happens.
        self.vad: webrtcvad.Vad | None = None
        self._pa: pyaudio.PyAudio | None = None
        # Annotated rather than left to inference: bare `= None` infers the type
        # as None, and every later assignment of a real stream then only passes
        # because _open_stream is unannotated and so goes unchecked.
        self._stream: pyaudio.Stream | None = None

    def _open_stream(self):
        """Open the PyAudio mic stream."""
        import pyaudio

        if self._pa is None:
            self._pa = pyaudio.PyAudio()
        assert self._pa is not None
        mic_idx = resolve_mic_index(self._pa)
        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self.channels,
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
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=None,
                frames_per_buffer=self.chunk_samples,
            )

    def _close_stream(self):
        """Close the mic stream (not PyAudio itself — keep it alive)."""
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

    def record(self, wake_timeout_ms: int = config.VAD_WAKE_TIMEOUT_MS) -> bytes:
        """
        Record audio from mic until speech ends (VAD-based).

        Flow:
        1. Wait for speech to start (VAD detects voice).
        2. Keep recording while speech continues.
        3. Stop after `silence_timeout_ms` of continuous silence.
        4. Return WAV bytes (in-memory, 16kHz mono 16-bit).

        Args:
            wake_timeout_ms: If no speech onset is detected within this time
                after a wake-word trigger, abort and return empty bytes.
                Prevents false-positive wake events from deadlocking the
                assistant (wake listener stays paused while we wait forever).

        Returns:
            WAV file content as bytes, or empty bytes on timeout/no speech.
        """
        if self.vad is None:
            import webrtcvad

            self.vad = webrtcvad.Vad(self.vad_aggressiveness)
        vad = self.vad

        self._open_stream()

        frames: list[bytes] = []
        speech_started = False
        speech_frames = 0
        speech_total = 0  # every frame the VAD called speech — the min_speech_ms gate
        silence_frames = 0
        total_frames = 0
        silence_threshold = int(self.silence_timeout_ms / self.chunk_ms)
        min_speech_threshold = int(self.min_speech_ms / self.chunk_ms)
        wake_timeout_frames = int(wake_timeout_ms / self.chunk_ms)
        max_utterance_frames = int(self.max_utterance_ms / self.chunk_ms)

        # Ring buffer: keep the last 300ms of pre-speech audio
        # so we don't clip the beginning of the utterance
        pre_speech_buffer_size = int(300 / self.chunk_ms)  # 10 frames at 30ms
        pre_speech_buffer: collections.deque[bytes] = collections.deque(maxlen=pre_speech_buffer_size)

        try:
            while True:
                assert self._stream is not None
                chunk = self._stream.read(self.chunk_samples, exception_on_overflow=False)
                total_frames += 1

                is_speech = vad.is_speech(chunk, self.sample_rate)

                if not speech_started:
                    # Safety: abort if no speech starts within the wake timeout.
                    # This happens on false-positive wake triggers.
                    if total_frames >= wake_timeout_frames:
                        print("  ⏱️  Wake timeout — no speech detected.")
                        return b""

                    pre_speech_buffer.append(chunk)
                    if is_speech:
                        speech_frames += 1
                        # Require a few consecutive speech frames to avoid false starts
                        if speech_frames >= 3:
                            speech_started = True
                            speech_total = speech_frames   # the onset frames were speech
                            # Flush pre-speech buffer so we don't clip the start
                            frames.extend(pre_speech_buffer)
                            print("  🎤 Listening...")
                    else:
                        speech_frames = 0
                else:
                    frames.append(chunk)
                    if is_speech:
                        speech_total += 1
                        silence_frames = 0
                    else:
                        silence_frames += 1

                    # Silence ends the utterance, full stop. Whether it held
                    # enough speech to be worth transcribing is decided after
                    # the loop: folding that test in here means a genuinely
                    # short answer never satisfies it and the loop never exits.
                    if silence_frames >= silence_threshold:
                        break

                    # Safety net. A mic emitting constant noise scores every
                    # frame as speech, so silence_frames never climbs and the
                    # break above never fires — an unbounded loop holding the
                    # wake listener paused. Cap any single utterance.
                    if len(frames) >= max_utterance_frames:
                        print("  ⏱️  Maximum utterance length reached.")
                        break

        finally:
            self._close_stream()

        if not frames:
            return b""

        # The min_speech gate, measured in frames the VAD actually called
        # speech. It used to be measured against len(frames), which counts the
        # flushed pre-roll and every silent frame as well — so it was really a
        # "how long did we record" test and passed on silence alone. A cough or
        # a door slam trips the 3-frame onset but never accumulates real
        # speech, and handing that to Whisper produces a confident hallucinated
        # transcript rather than nothing.
        if speech_total < min_speech_threshold:
            print("  🔇 Too little speech — ignoring.")
            return b""

        # Package as WAV in memory
        return self._frames_to_wav(frames)

    def _frames_to_wav(self, frames: list[bytes]) -> bytes:
        """Convert raw PCM frames to WAV bytes in memory."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()

    def cleanup(self):
        """Release PyAudio resources."""
        self._close_stream()
        if self._pa:
            self._pa.terminate()
            self._pa = None
