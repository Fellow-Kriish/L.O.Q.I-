"""
LOQI — Microphone Recorder with VAD-based Endpoint Detection

Records 16kHz mono 16-bit PCM from the default mic.
Uses webrtcvad to detect speech start/end — stops recording after
sustained silence, not a fixed timer.

Returns raw audio bytes ready for faster-whisper.
"""

import io
import wave
import struct
import collections

import pyaudio
import webrtcvad

import config


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
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.chunk_samples = int(sample_rate * chunk_ms / 1000)
        self.chunk_bytes = self.chunk_samples * 2  # 16-bit = 2 bytes/sample

        self.silence_timeout_ms = silence_timeout_ms
        self.min_speech_ms = min_speech_ms

        self.vad = webrtcvad.Vad(vad_aggressiveness)
        self._pa = None
        self._stream = None

    def _open_stream(self):
        """Open the PyAudio mic stream."""
        if self._pa is None:
            self._pa = pyaudio.PyAudio()
        mic_idx = getattr(config, 'MIC_DEVICE_INDEX', None)
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=mic_idx,
            frames_per_buffer=self.chunk_samples,
        )

    def _close_stream(self):
        """Close the mic stream (not PyAudio itself — keep it alive)."""
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

    def record(self) -> bytes:
        """
        Record audio from mic until speech ends (VAD-based).

        Flow:
        1. Wait for speech to start (VAD detects voice).
        2. Keep recording while speech continues.
        3. Stop after `silence_timeout_ms` of continuous silence.
        4. Return WAV bytes (in-memory, 16kHz mono 16-bit).

        Returns:
            WAV file content as bytes.
        """
        self._open_stream()

        frames: list[bytes] = []
        speech_started = False
        speech_frames = 0
        silence_frames = 0
        silence_threshold = int(self.silence_timeout_ms / self.chunk_ms)
        min_speech_threshold = int(self.min_speech_ms / self.chunk_ms)

        # Ring buffer: keep the last 300ms of pre-speech audio
        # so we don't clip the beginning of the utterance
        pre_speech_buffer_size = int(300 / self.chunk_ms)  # 10 frames at 30ms
        pre_speech_buffer = collections.deque(maxlen=pre_speech_buffer_size)

        try:
            while True:
                chunk = self._stream.read(self.chunk_samples, exception_on_overflow=False)

                is_speech = self.vad.is_speech(chunk, self.sample_rate)

                if not speech_started:
                    pre_speech_buffer.append(chunk)
                    if is_speech:
                        speech_frames += 1
                        # Require a few consecutive speech frames to avoid false starts
                        if speech_frames >= 3:
                            speech_started = True
                            # Flush pre-speech buffer so we don't clip the start
                            frames.extend(pre_speech_buffer)
                            print("  🎤 Listening...")
                    else:
                        speech_frames = 0
                else:
                    frames.append(chunk)
                    if is_speech:
                        silence_frames = 0
                    else:
                        silence_frames += 1

                    # Stop if enough silence after sufficient speech
                    total_speech_frames = len(frames)
                    if silence_frames >= silence_threshold and total_speech_frames >= min_speech_threshold:
                        break

        finally:
            self._close_stream()

        if not frames:
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
