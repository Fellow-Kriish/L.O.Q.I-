"""
LOQI — Confirm Gate

Permission-tier gate from v2 doc section 4.

Tier 0/1: execute immediately, no confirmation.
Tier 2:   speak back the proposed action, wait for "yes"/"no".
Tier 3:   speak back + repeat exact action, wait for explicit confirm.
Never:    surface as suggestion, never auto-execute.

In v0 this uses typed input(). Swapped to STT-based verbal confirm later.
"""


def confirm_action(tier: int, action_description: str, tts_fn=None) -> bool:
    """
    Check whether an action should proceed based on its permission tier.

    Args:
        tier: Permission level (0, 1, 2, 3).
        action_description: Human-readable description of the action.
        tts_fn: Optional TTS function — speak(text). If None, uses print().

    Returns:
        True if the action should proceed, False if denied.
    """
    def _say(text: str):
        if tts_fn:
            tts_fn(text)
        else:
            print(f"[LOQI] {text}")

    # Tier 0 and 1: no confirmation needed
    if tier <= 1:
        return True

    # Tier 2: speak back, ask for yes/no
    if tier == 2:
        _say(f"I'm about to {action_description}. Should I go ahead?")
        # v0: typed input. Later: second STT pass for verbal "yes"/"no"
        response = input("Confirm? (yes/no): ").strip().lower()
        return response in ("yes", "y", "yeah", "yep", "sure", "do it", "go ahead", "confirm")

    # Tier 3: speak back + repeat exact action, require explicit confirm
    if tier >= 3:
        _say(f"Warning: I'm about to {action_description}. This may be irreversible.")
        _say(f"To confirm, please say or type 'yes, {action_description}'.")
        response = input(f"Type 'yes, {action_description}' to confirm: ").strip().lower()
        expected = f"yes, {action_description}".lower()
        return response == expected or response in ("yes", "y")

    return False
