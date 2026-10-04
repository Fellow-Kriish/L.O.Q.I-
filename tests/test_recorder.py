"""
VAD endpointing tests.

record() decides two things nothing downstream can undo: when the user stopped
talking, and whether what was captured is worth transcribing at all. The second
is the expensive one — Whisper handed a wav of near-silence does not return
nothing, it returns a confident invented sentence, which then routes as though
the user had actually said it.

No microphone is involved: the capture loop is driven by a scripted VAD and a
fake stream. recorder.py imports pyaudio and webrtcvad lazily (first mic open /
first record()), and both are replaced here before either happens, so these run
in CI with no audio wheels installed — they used to importorskip and therefore
never ran where it mattered.
"""

from recorder import Recorder

_CHUNK = b"\x00" * 960   # one 30ms frame, 16kHz 16-bit mono


class _Mic:
    """
    Replays a speech script: one character per frame, "S" speech, "." silence.

    Standing in for both the stream and the VAD keeps the two in lockstep —
    frame N's audio and frame N's verdict cannot drift apart. Reading past the
    end of the script is a hang, not a pass, so it fails loudly.
    """

    def __init__(self, pattern: str):
        self.pattern = pattern
        self.i = -1

    def read(self, n, exception_on_overflow=True) -> bytes:
        self.i += 1
        if self.i >= len(self.pattern):
            raise AssertionError(
                "read past the end of the script: the capture loop never "
                "reached a break, which on a real mic is a hang with the wake "
                "listener still paused"
            )
        return _CHUNK

    def is_speech(self, chunk: bytes, rate: int) -> bool:
        return self.pattern[self.i] == "S"


def _recorder(monkeypatch, pattern: str, **kwargs) -> Recorder:
    """A Recorder wired to a scripted mic, with short thresholds for legibility."""
    settings = {
        "silence_timeout_ms": 90,     # 3 frames of silence ends the utterance
        "min_speech_ms": 150,         # 5 speech frames to be worth transcribing
        "max_utterance_ms": 600,      # 20 frames, the anti-hang ceiling
    }
    settings.update(kwargs)

    rec = Recorder(**settings)
    mic = _Mic(pattern)
    # setattr rather than plain assignment for the stream and the VAD too: it
    # restores them after the test, and it doesn't ask the annotations to admit
    # a fake where a pyaudio.Stream belongs.
    monkeypatch.setattr(rec, "_open_stream", lambda: None)
    monkeypatch.setattr(rec, "_close_stream", lambda: None)
    monkeypatch.setattr(rec, "_stream", mic)
    monkeypatch.setattr(rec, "vad", mic)
    return rec


def test_a_normal_utterance_is_captured(monkeypatch):
    """Three frames to trigger onset, speech, then silence closes it."""
    rec = _recorder(monkeypatch, "SSSSSSSS...")
    audio = rec.record()
    assert audio.startswith(b"RIFF")


def test_a_cough_is_discarded(monkeypatch):
    """
    Onset needs only 3 consecutive speech frames, which a cough or a door
    slam clears easily. The min_speech gate is what stops that reaching the
    STT, and it is the only thing that does.
    """
    rec = _recorder(monkeypatch, "SSS...")
    assert rec.record() == b""


def test_silence_ends_the_recording_even_when_speech_was_too_short(monkeypatch):
    """
    The termination guarantee, stated as a test.

    Gating the loop's break on min_speech — rather than filtering afterwards —
    reads as the obvious implementation and hangs: a short utterance never
    satisfies the minimum, so the break never fires and the loop runs forever
    with the wake listener paused. _Mic fails instead of hanging.
    """
    rec = _recorder(monkeypatch, "SSS" + "." * 10)
    assert rec.record() == b""


def test_min_speech_counts_speech_not_elapsed_time(monkeypatch):
    """
    The original defect: the gate compared len(frames), which counts the
    flushed pre-roll and every silent frame, so it really asked "did we record
    for long enough" and a long silence passed it on its own.

    Here 3 speech frames are followed by 20 silent ones. By elapsed frames that
    is 23, comfortably over the 5-frame minimum; by actual speech it is 3, and
    it must be rejected.
    """
    rec = _recorder(monkeypatch, "SSS" + "." * 20, silence_timeout_ms=600)
    assert rec.record() == b""


def test_isolated_speech_frames_never_start_a_recording(monkeypatch):
    """
    Onset requires 3 *consecutive* speech frames. Alternating single frames are
    line noise, and the wake timeout is what ends the wait — otherwise a
    false-positive wake word leaves the assistant listening indefinitely.
    """
    rec = _recorder(monkeypatch, "S.S.S.S.S.")
    assert rec.record(wake_timeout_ms=300) == b""


def test_constant_noise_hits_the_ceiling_instead_of_looping_forever(monkeypatch):
    """
    A mic emitting constant noise scores every frame as speech, so
    silence_frames never climbs and the silence break never fires. Without the
    ceiling this is an unbounded loop — the script is 40 frames against a
    20-frame cap, so overrunning it fails rather than spins.
    """
    rec = _recorder(monkeypatch, "S" * 40)
    audio = rec.record()
    assert audio.startswith(b"RIFF")
    # 20 frames × 960 bytes, plus the 44-byte WAV header.
    assert len(audio) <= 20 * 960 + 44


def test_the_start_of_the_utterance_is_not_clipped(monkeypatch):
    """
    Onset is only detected 3 frames in, so those frames plus the preceding
    pre-roll are flushed into the recording. Without that the first syllable is
    missing and "open notepad" reaches the router as "pen notepad".
    """
    rec = _recorder(monkeypatch, "SSSSS...")
    audio = rec.record()
    # 5 speech frames + the 3 trailing silent ones that closed the utterance.
    # Without the pre-roll flush the first 3 speech frames would be missing and
    # this would be 5.
    assert len(audio) - 44 == 8 * 960


def test_recorder_imports_without_the_audio_stack(monkeypatch):
    """
    Guards the lazy imports. A None entry in sys.modules makes `import pyaudio`
    raise ImportError even where the wheel is installed, so this simulates CI
    on a dev machine. If a top-level audio import comes back, this fails here
    instead of the whole file silently disappearing from CI.
    """
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "pyaudio", None)
    monkeypatch.setitem(sys.modules, "webrtcvad", None)
    monkeypatch.delitem(sys.modules, "recorder", raising=False)

    module = importlib.import_module("recorder")
    module.Recorder()   # construction must not touch the audio stack either
