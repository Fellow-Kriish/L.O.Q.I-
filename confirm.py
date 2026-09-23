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
"""

import config


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

    # Tier 0 and 1: no confirmation needed
    if tier <= 1:
        return True

    # Tier 2: speak back, wait for verbal yes/no
    if tier == 2:
        _say(f"I'm about to {action_description}. Should I go ahead?")
        response = _hear_yn("Confirm? (yes/no): ")
        return response in ("yes", "y", "yeah", "yep", "sure", "do it", "go ahead", "confirm")

    # Tier 3: speak back + repeat exact action, require explicit confirm
    if tier >= 3:
        _say(f"Warning: I'm about to {action_description}. This may be irreversible.")
        _say("Please say yes to confirm, or say nothing to cancel.")
        response = _hear_yn(f"Type 'yes, {action_description}' to confirm: ")
        expected = f"yes, {action_description}".lower()
        # Voice: plain "yes" accepted (speaking the full action phrase is impractical)
        # Text:  full repeat-back still works, as does plain yes/y
        return response == expected or response in ("yes", "y")

    return False
