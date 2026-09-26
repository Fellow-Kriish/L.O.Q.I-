"""
LOQI — Confirm Gate

Permission-tier gate from v2 doc section 4.

Tier 0/1: execute immediately, no confirmation.
Tier 2:   speak back the proposed action, wait for "yes"/"no" by voice (or
          typed input when audio hooks are not available, e.g. --text mode).
Tier 3:   speak back + repeat exact action, require explicit confirm.
Never:    surface as suggestion, never auto-execute.

Voice path: caller supplies recorder_fn (→ wav bytes) and stt_fn (→ str).
Text path:  both default to None → falls back to input().
Silence within VAD_CONFIRM_TIMEOUT_MS → treated as "no" (safe default).
Unclear speech → re-asked once ("Sorry, was that a yes or a no?") before
defaulting to "no", so a mumble doesn't force the user to repeat the whole
command — but two mumbles in a row means we genuinely can't hear them.
"""

import config

_YES_WORDS = (
    "yes", "y", "yeah", "yep", "sure", "do it", "go ahead", "confirm", "ok", "okay",
)
_NO_WORDS = (
    "no", "n", "nope", "nah", "don't", "dont", "do not", "stop", "cancel",
    "never mind", "nevermind",
)


def confirm_action(
    tier: int,
    action_description: str,
    tts_fn=None,
    recorder_fn=None,
    stt_fn=None,
) -> bool:
    """
    Check whether an action should proceed based on its permission tier.

    Args:
        tier:               Permission level (0, 1, 2, 3).
        action_description: Human-readable description of the action.
        tts_fn:             Optional TTS function — speak(text). Falls back to print().
        recorder_fn:        Optional callable() → wav bytes. When provided with
                            stt_fn, enables hands-free voice confirmation.
        stt_fn:             Optional callable(wav_bytes) → str. Transcribes the
                            yes/no utterance returned by recorder_fn.

    Returns:
        True if the action should proceed, False if denied or timed out.
    """
    def _say(text: str):
        if tts_fn:
            tts_fn(text)
        else:
            print(f"[LOQI] {text}")

    def _hear_yn(prompt: str) -> str:
        """
        Capture one yes/no utterance.

        Voice path: recorder_fn records a short clip (VAD_CONFIRM_TIMEOUT_MS),
        stt_fn transcribes it. Silence → empty string → caller treats as "no".
        Text fallback: input() when audio hooks are absent (--text mode, tests).
        """
        if recorder_fn and stt_fn:
            wav = recorder_fn(wake_timeout_ms=config.VAD_CONFIRM_TIMEOUT_MS)
            if wav:
                return stt_fn(wav).lower().strip()
            # Silence within timeout — return empty so caller treats as "no"
            return ""
        return input(prompt).strip().lower()

    def _ask_yes_no(prompt: str, strict: bool = False, expected: str | None = None) -> bool:
        """
        Hear a yes/no verdict, re-asking once on unclear speech.

        Silence → "no" immediately, without the re-ask: the user walked away,
        and doubling their wait serves nobody. Unclear speech gets one re-ask;
        a second unclear answer means we can't hear them, and the safe default
        stands. ``strict`` (Tier 3) accepts only an explicit "yes"/"y" or the
        full repeat-back — a casual "yeah" is not consent to the irreversible.
        """
        yes_words = ("yes", "y") if strict else _YES_WORDS
        for attempt in (1, 2):
            response = _hear_yn(prompt)
            if not response:
                return False
            if response == expected or response in yes_words:
                return True
            if response in _NO_WORDS:
                return False
            if attempt == 1:
                _say("Sorry, was that a yes or a no?")
        return False

    # Tier 0 and 1: no confirmation needed
    if tier <= 1:
        return True

    # Tier 2: speak back, wait for verbal yes/no
    if tier == 2:
        _say(f"I'm about to {action_description}. Should I go ahead?")
        return _ask_yes_no("Confirm? (yes/no): ")

    # Tier 3: speak back + repeat exact action, require explicit confirm
    if tier >= 3:
        _say(f"Warning: I'm about to {action_description}. This may be irreversible.")
        _say("Please say yes to confirm, or say nothing to cancel.")
        # Voice: plain "yes" accepted (speaking the full action phrase is impractical)
        # Text:  full repeat-back still works, as does plain yes/y
        return _ask_yes_no(f"Type 'yes, {action_description}' to confirm: ", strict=True,
                           expected=f"yes, {action_description}".lower())

    return False
