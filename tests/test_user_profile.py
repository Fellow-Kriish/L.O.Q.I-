"""
User profile tests.

Two things are worth pinning here. One is the prompt block's shape: an
unconfigured install must send the persona prompt untouched, because a heading
with no facts under it invites the model to fill the gap, and a half-filled line
("Name:" with nothing after it) reads as a name it failed to hear.

The other is the collapse. "Where the user is" and "which units" each used to
have a weather-flavoured setting of their own; they are now one field apiece,
referenced by the weather skill rather than copied into it. Nothing in the code
stops someone reintroducing the second copy, so the tests below assert the two
readings are the same value rather than merely equal today.
"""

from __future__ import annotations

import importlib.util
from typing import Literal

import pytest

import config
import user_profile
from config import Settings

# The Brain tests below need the Groq SDK importable — not reachable, just
# importable. CI installs pydantic and rapidfuzz only, so they skip there, the
# same way the recorder tests skip without the audio wheels. The rest of this
# file is pure Python and runs everywhere, which is why the guard is per-test
# rather than at module level.
_HAS_GROQ = importlib.util.find_spec("groq") is not None
_needs_groq = pytest.mark.skipif(not _HAS_GROQ, reason="groq SDK not installed (CI omits it)")


def _with_profile(
    monkeypatch,
    *,
    user_name: str = "",
    user_city: str = "",
    units: Literal["metric", "imperial"] = "metric",
    user_about: str = "",
) -> None:
    """
    Point config.settings at a profile built from these fields.

    Spelled out rather than splatted from a dict: BaseSettings.__init__ also
    takes a dozen underscore-prefixed control arguments, so a **dict lands on
    those signatures and the call stops being type-checkable.
    """
    monkeypatch.setattr(
        config,
        "settings",
        Settings(user_name=user_name, user_city=user_city, units=units, user_about=user_about),
    )


def _fresh_settings(monkeypatch, **env) -> Settings:
    """
    Settings built from ``env`` alone.

    ``_env_file=None`` skips .env, and every name involved is cleared first:
    config imports load_dotenv, so the developer's own .env has already reached
    os.environ by the time a test runs, and an alias test whose first choice is
    set there would pass or fail on that file's contents rather than on the code.
    """
    for name in ("LOQI_USER_CITY", "LOQI_WEATHER_DEFAULT_CITY", "LOQI_UNITS", "LOQI_WEATHER_UNITS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


# --------------------------------------------------------------------- shape

def test_an_unconfigured_profile_adds_nothing_to_the_prompt(monkeypatch):
    """
    No name, no city, no note — the units line alone is not a profile.

    The persona prompt has to come out byte-identical, not merely similar: a
    section header with one generic line under it is a worse prompt than no
    section at all.
    """
    _with_profile(monkeypatch)
    assert user_profile.prompt_block() == ""


def test_a_single_configured_fact_is_enough_for_a_block(monkeypatch):
    _with_profile(monkeypatch, user_name="Krish")
    block = user_profile.prompt_block()
    assert "ABOUT THE USER" in block
    assert "Name: Krish" in block


def test_unset_fields_leave_no_empty_lines(monkeypatch):
    """A city but no name must not emit a bare "Name:" for the model to fill."""
    _with_profile(monkeypatch, user_city="Indore")
    block = user_profile.prompt_block()
    assert "Indore" in block
    assert "Name:" not in block
    assert "Notes they've given you:" not in block


def test_the_block_tells_the_model_not_to_recite_it(monkeypatch):
    """
    The header is load-bearing, not decoration: given a name and no instruction
    the model opens every answer with it, which is grating out loud.
    """
    _with_profile(monkeypatch, user_name="Krish", user_city="Indore")
    block = user_profile.prompt_block()
    assert "don't recite" in block
    assert "don't open your answers with" in block


def test_units_name_the_actual_units_and_only_one_system(monkeypatch):
    """
    "metric" alone doesn't stop a model answering a distance in miles, so the
    line spells the units out — and must not spell out both systems at once.
    """
    _with_profile(monkeypatch, user_name="Krish", units="metric")
    metric = user_profile.prompt_block()
    assert "Celsius" in metric and "kilometres" in metric
    assert "Fahrenheit" not in metric and "miles" not in metric

    _with_profile(monkeypatch, user_name="Krish", units="imperial")
    imperial = user_profile.prompt_block()
    assert "Fahrenheit" in imperial and "miles" in imperial
    assert "Celsius" not in imperial and "kilometres" not in imperial


def test_the_free_text_note_is_passed_through(monkeypatch):
    _with_profile(monkeypatch, user_about="Computer science student. Works late.")
    block = user_profile.prompt_block()
    assert "Computer science student. Works late." in block


def test_an_overlong_note_is_truncated_rather_than_refused(monkeypatch):
    """
    Booting is not optional. A voice assistant that dies on a ValidationError
    has no way to tell the user their note was forty characters too long, so the
    note is cut to fit and the overrun goes to the log instead.
    """
    _with_profile(monkeypatch, user_about="word " * 400)
    block = user_profile.prompt_block()
    assert block                       # still produced a block
    assert len(block) < 1200           # and did not paste 2000 characters into it
    assert block.rstrip().endswith("...")


def test_the_note_is_collapsed_to_one_line(monkeypatch):
    """
    A note pasted from a text editor arrives with newlines, and a raw newline
    inside the block would break out of its bullet and read as a new prompt
    section.
    """
    _with_profile(monkeypatch, user_about="Line one.\n\nLine two.\n")
    block = user_profile.prompt_block()
    assert "Line one. Line two." in block


# ------------------------------------------------------------------ describe

def test_describe_names_what_is_missing(monkeypatch):
    """
    The startup line exists to catch a profile that didn't load, so absence has
    to be legible in it — a blank where the city goes reads as working.
    """
    _with_profile(monkeypatch)
    line = user_profile.describe()
    assert "no name set" in line
    assert "no city set" in line


def test_describe_summarises_a_configured_profile(monkeypatch):
    _with_profile(monkeypatch, user_name="Krish", user_city="Indore", user_about="Student.")
    line = user_profile.describe()
    assert "Krish" in line
    assert "Indore" in line
    assert "metric" in line
    assert "with notes" in line


# ------------------------------------------------------- the collapsed fields

def test_the_weather_place_is_the_profile_city(monkeypatch):
    """
    Regression guard on the collapse. weather.py reads WEATHER_DEFAULT_CITY to
    decide what "the weather" means with no place named; the profile tells the
    model where the user lives. Those were separate settings, and separate
    settings drift — someone moves city, updates the profile, and the forecast
    quietly keeps reporting the old one.
    """
    assert config.settings.user_city == config.WEATHER_DEFAULT_CITY
    assert not hasattr(config.settings, "weather_default_city")


def test_the_weather_units_are_the_profile_units(monkeypatch):
    assert config.settings.units == config.WEATHER_UNITS
    assert not hasattr(config.settings, "weather_units")


def test_the_old_weather_env_names_still_configure_the_profile(monkeypatch):
    """
    The settings were renamed, not replaced. An existing .env using the weather
    names has to keep working — and has to reach the *profile*, so the model is
    told the same city the forecast uses.
    """
    settings = _fresh_settings(monkeypatch, LOQI_WEATHER_DEFAULT_CITY="Bengaluru")
    assert settings.user_city == "Bengaluru"

    settings = _fresh_settings(monkeypatch, LOQI_WEATHER_UNITS="imperial")
    assert settings.units == "imperial"


def test_the_profile_env_names_win_over_the_old_weather_names(monkeypatch):
    """With both set, the name that describes the fact takes precedence."""
    settings = _fresh_settings(
        monkeypatch,
        LOQI_USER_CITY="Indore",
        LOQI_WEATHER_DEFAULT_CITY="Bengaluru",
    )
    assert settings.user_city == "Indore"


# -------------------------------------------------------- reaching the model

@_needs_groq
def test_the_profile_reaches_the_system_message(monkeypatch):
    """
    The payoff, end to end. Rendering a block is not the feature — the feature
    is the model being told, and the only place that happens is the system
    message Brain sends.
    """
    from brain import Brain

    _with_profile(monkeypatch, user_name="Krish", user_city="Indore")
    messages = Brain(api_key="unused-by-this-test")._prepare("what should I wear")

    assert messages[0]["role"] == "system"
    assert "Name: Krish" in messages[0]["content"]
    assert "Indore" in messages[0]["content"]


@_needs_groq
def test_an_unconfigured_profile_leaves_the_persona_prompt_byte_identical(monkeypatch):
    """
    An install that configured nothing must send the persona prompt and nothing
    else — no stray heading, no trailing whitespace, nothing for the model to
    read as a section it should say something about.
    """
    from brain import Brain

    _with_profile(monkeypatch)
    assert Brain(api_key="unused-by-this-test").system_prompt == config.GROQ_SYSTEM_PROMPT


@_needs_groq
def test_an_explicit_system_prompt_replaces_persona_and_profile_both(monkeypatch):
    """
    The seam is injectable so a test can pin what the model was sent without
    reaching into config. Injecting has to bypass the profile too, or the
    override is only half an override.
    """
    from brain import Brain

    _with_profile(monkeypatch, user_name="Krish")
    brain = Brain(api_key="unused-by-this-test", system_prompt="just this")

    assert brain.system_prompt == "just this"
    assert "Krish" not in brain._prepare("hello")[0]["content"]
