"""
LOQI — User Profile

The handful of facts about the user that both halves of the assistant need: the
cloud model, so it doesn't have to be told where you live on every turn, and the
local skills, which need a default city and a unit system to answer at all.

Those facts live in exactly one place. "Where the user is" used to be
``weather_default_city`` — a weather setting by name, a profile fact in truth —
and adding a second field beside it would have given the assistant two answers
to the same question, free to drift apart. So the weather settings were folded
into the profile instead, with their old environment variable names kept as
aliases. See the User profile section of config.py.

Deliberately not a memory store. There is no write path here: the profile is
read from the environment (or .env) at startup and is fixed for the life of the
process. A spoken "remember that I ..." implies a mutable store, and a mutable
store reachable from model output is a prompt-injection surface — anything the
assistant reads could append a line to its own system prompt. That feature is
worth building, but it belongs behind the confirm gate, not behind a setter.
See the project rule: LLM output is data, not authority.
"""

from __future__ import annotations

import config
from logging_setup import get_logger

log = get_logger(__name__)

# The free-text note is the one unbounded field, and it is sharing a context
# window with a long persona prompt. Truncated rather than rejected: refusing to
# boot over an overlong note would leave a voice assistant unable to explain why
# it is silent.
_ABOUT_MAX_CHARS = 400

# Spelled out rather than left as "metric"/"imperial": naming the actual units
# is what stops the model answering a distance in miles to a metric user, and
# the conversion clause is what stops it reciting whichever unit its source
# happened to use.
_UNIT_PHRASES = {
    "metric": (
        "metric — give temperatures in Celsius, distances in kilometres and "
        "weights in kilograms without being asked, converting if your source "
        "uses imperial"
    ),
    "imperial": (
        "imperial — give temperatures in Fahrenheit, distances in miles and "
        "weights in pounds without being asked, converting if your source "
        "uses metric"
    ),
}

# Facts alone make the model chatty about them: told a name, it opens every
# answer with it; told a city, it works the city into answers that never needed
# one. The header is what buys the facts without the recital.
_HEADER = """
# ABOUT THE USER
Context, not a script. Use these when they change the answer, and stay quiet
about them otherwise — don't recite this list back, don't open your answers with
the user's name, and don't mention where they live unless the question turns on
it.
"""


def _about_text() -> str:
    """The user's free-text note, truncated to fit the prompt budget."""
    about = " ".join(config.settings.user_about.split())
    if len(about) <= _ABOUT_MAX_CHARS:
        return about

    log.warning(
        "LOQI_USER_ABOUT is %d characters; using the first %d.",
        len(about), _ABOUT_MAX_CHARS,
    )
    # Cut at a word boundary so the note doesn't end mid-word.
    return about[:_ABOUT_MAX_CHARS].rsplit(" ", 1)[0] + "..."


def prompt_block() -> str:
    """
    The profile as a block to append to the system prompt.

    Empty when nothing is configured, so an unconfigured install sends exactly
    the persona prompt and nothing else — a heading with no facts under it is
    worse than no heading, since it invites the model to fill the gap.

    Each line is omitted individually for the same reason: "Name:" with nothing
    after it reads as a name the model failed to catch, and it will either ask
    for one or invent one.
    """
    settings = config.settings
    facts: list[str] = []

    if settings.user_name.strip():
        facts.append(f"Name: {settings.user_name.strip()}")

    if settings.user_city.strip():
        facts.append(
            f"Location: {settings.user_city.strip()} — treat this as \"here\" when a "
            "question implies a place without naming one"
        )

    # Always stated. Unlike the others this one has no unknown value: the
    # assistant is going to pick a unit for every temperature it speaks, and
    # leaving it unsaid means it picks from the model's own default rather than
    # from the same setting the weather skill uses.
    facts.append(f"Units: {_UNIT_PHRASES[settings.units]}")

    about = _about_text()
    if about:
        facts.append(f"Notes they've given you: {about}")

    # One fact and it is the units line — i.e. nothing the user actually
    # configured. Not worth a section.
    if len(facts) == 1:
        return ""

    return "\n" + _HEADER + "\n" + "\n".join(f"- {fact}" for fact in facts) + "\n"


def describe() -> str:
    """
    A one-line profile summary for the startup console.

    A profile that silently failed to load is the failure mode worth catching
    early, and it is invisible until the assistant answers oddly hours later.
    Printing what it read at boot makes it a two-second check instead.
    """
    settings = config.settings
    parts = [settings.user_name.strip() or "no name set", settings.user_city.strip() or "no city set"]
    parts.append(settings.units)
    if settings.user_about.strip():
        parts.append("with notes")
    return " · ".join(parts)
