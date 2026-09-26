"""
Intent-level unit tests.

test_router.py covers *which* intent an utterance reaches. This file covers the
metadata the orchestrator and the confirm gate read off the intent once it has
been reached — in particular the phrase the gate speaks back before a Tier 2 or
3 action runs.
"""

from corpus import MUST_MATCH

from intents import INTENTS, Intent, route


def test_every_gated_intent_has_a_spoken_description():
    """
    Tier 2 and 3 read the action back and wait for a yes. A user who cannot
    parse what they heard cannot meaningfully consent, so "close_app:
    {'app_name': 'chrome'}" is a broken gate even though it technically asks.

    Any new gated intent must ship a describe template. This is the check that
    stops one landing without.
    """
    missing = sorted(i.name for i in INTENTS if i.tier >= 2 and not i.describe)
    assert not missing, f"gated intents with no describe template: {missing}"


def test_gated_descriptions_render_from_real_utterances():
    """
    Catch template/extractor drift using the corpus rather than hand-written
    args: rename the regex group and the template silently stops filling, which
    would put the intent name back in the user's ear.
    """
    checked = 0
    for text, expected in MUST_MATCH:
        result = route(text)
        if result is None or result.intent.tier < 2:
            continue
        checked += 1
        description = result.intent.description(result.args)
        assert description != result.intent.name, (
            f"{text!r} → {expected}: description() fell back to the intent name, "
            f"so template {result.intent.describe!r} and the extractor disagree"
        )
        assert "{" not in description, (
            f"{text!r} → {expected}: unfilled placeholder in {description!r}"
        )
    assert checked, "corpus covers no gated intents — this test is vacuous"


def test_description_renders_the_template():
    intent = Intent(
        name="close_app", patterns=[], tier=2, handler="close_app",
        describe="close {app_name}",
    )
    assert intent.description({"app_name": "chrome"}) == "close chrome"


def test_description_falls_back_to_the_intent_name_without_a_template():
    """Untiered intents are never spoken back, so no template is required."""
    intent = Intent(name="tell_time", patterns=[], tier=0, handler="tell_time")
    assert intent.description({}) == "tell_time"


def test_youtube_query_strips_filler_words():
    """
    "search for cats on youtube" must extract "cats", not "for cats" — the
    filler leaks straight into the YouTube search URL and the spoken response
    ("Searching YouTube for for cats"), which reads as a hearing problem.
    """
    result = route("search for cats on youtube")
    assert result is not None and result.intent.name == "search_youtube"
    assert result.args["query"] == "cats"


def test_music_verbs_route_to_play_music_not_open_app():
    """
    open_app's "start ..." pattern sits above play_music's "start ... music"
    shape. If ordering ever regresses, "start music" resolves to an app named
    "music" and falls through to the LLM — an instant local command paying a
    cloud round-trip.
    """
    for text in ("start music", "start my music", "open music"):
        result = route(text)
        assert result is not None and result.intent.name == "play_music", (
            f"{text!r} must route to play_music, got {result.intent.name if result else None!r}"
        )


def test_description_survives_a_missing_arg():
    """
    A KeyError here would crash the gate that exists to keep the user in
    control. Degrade to something safe instead.
    """
    intent = Intent(
        name="close_app", patterns=[], tier=2, handler="close_app",
        describe="close {app_name}",
    )
    assert intent.description({}) == "close_app"
