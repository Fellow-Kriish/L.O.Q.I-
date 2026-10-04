"""
Confirm gate tests — fed the strings Whisper actually emits.

The gate's original defect was a bare string compare: Whisper returns "Yes.",
"Yeah!", "Yes, please." and every one of them was rejected, so nearly every
real Tier 2 action ended in "was that a yes or a no?" and then a denial. Only
a hand-typed lowercase "yes" passed, which is why it went unnoticed.

Both input paths are covered: the voice path (recorder_fn + stt_fn) and the
text fallback (input()). No audio or model is involved.
"""

import pytest

import confirm


def _voice(*replies: str):
    """recorder_fn/stt_fn pair that replays STT transcripts in order."""
    queue = list(replies)

    def recorder_fn(wake_timeout_ms=None):
        return b"wav" if queue else b""

    def stt_fn(_wav):
        return queue.pop(0)

    return recorder_fn, stt_fn


def _ask(tier: int, *replies: str, said: list[str] | None = None) -> bool:
    recorder_fn, stt_fn = _voice(*replies)
    spoken = said if said is not None else []
    return confirm.confirm_action(
        tier, "close chrome", tts_fn=spoken.append,
        recorder_fn=recorder_fn, stt_fn=stt_fn,
    )


# --- Tier 2: Whisper-style consent must pass ---------------------------------

@pytest.mark.parametrize("reply", [
    "yes", "Yes.", "Yes!", "YES", " Yes. ", "Yeah!", "Yeah.", "Yep.",
    "Okay.", "OK.", "Ok!", "Sure.", "Sure thing.", "Go ahead.",
    "Yes, please.", "Yeah, go ahead.", "Okay, do it.", "Do it!",
])
def test_tier2_accepts_whisper_style_yes(reply):
    said: list[str] = []
    assert _ask(2, reply, said=said) is True
    assert "Sorry, was that a yes or a no?" not in said


# --- Tier 2: refusals, including ones that start like a yes ------------------

@pytest.mark.parametrize("reply", [
    "No.", "No!", "Nope.", "Nah.", "Don't.", "Do not.", "Stop!", "Cancel.",
    "Never mind.", "No, thanks.",
    # Mixed answers must fail closed — prefix matching alone reads these as yes.
    "Yeah, no.", "Okay, wait, stop.", "Yes, no, cancel that.", "Sure, don't.",
])
def test_tier2_rejects_refusals(reply):
    said: list[str] = []
    assert _ask(2, reply, said=said) is False
    # A clear refusal is answered at once, never re-asked.
    assert "Sorry, was that a yes or a no?" not in said


def test_silence_is_no_without_a_reask():
    said: list[str] = []
    assert _ask(2, said=said) is False   # recorder returns b"" immediately
    assert "Sorry, was that a yes or a no?" not in said


def test_unclear_then_yes_is_reasked_once_and_accepted():
    said: list[str] = []
    assert _ask(2, "Banana.", "Yes.", said=said) is True
    assert said.count("Sorry, was that a yes or a no?") == 1


def test_two_unclear_replies_deny():
    assert _ask(2, "Banana.", "Potato.") is False


def test_yes_word_must_be_a_whole_word():
    """'Yesterday' is not 'yes' — the starts-with arm requires a word break."""
    assert _ask(2, "Yesterday.", "Okay-ish maybe") is False


# --- Tier 3: strict, but still punctuation-tolerant --------------------------

@pytest.mark.parametrize("reply", ["Yes.", "Yes!", "yes", "Yes, close chrome."])
def test_tier3_accepts_explicit_yes(reply):
    assert _ask(3, reply) is True


@pytest.mark.parametrize("reply", ["Yeah.", "Okay.", "Sure.", "Go ahead."])
def test_tier3_rejects_casual_consent(reply):
    """A casual 'yeah' is not consent to the irreversible."""
    assert _ask(3, reply, reply) is False


# --- Tiers that never ask ----------------------------------------------------

@pytest.mark.parametrize("tier", [0, 1])
def test_low_tiers_never_listen(tier):
    def boom(**_):
        raise AssertionError("tier <= 1 must not record")
    assert confirm.confirm_action(tier, "x", recorder_fn=boom, stt_fn=boom) is True


# --- Text fallback goes through the same normalization -----------------------

@pytest.mark.parametrize("typed,expected", [
    ("Yes.", True), ("Yes, please.", True), ("No.", False), ("Yeah, no.", False),
])
def test_text_fallback_is_normalized_too(monkeypatch, typed, expected):
    monkeypatch.setattr("builtins.input", lambda _prompt: typed)
    assert confirm.confirm_action(2, "close chrome", tts_fn=lambda _t: None) is expected
