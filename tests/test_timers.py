"""
Timers skill tests: duration parsing, label extraction, store behavior, wiring.

Parsing is the risky part — Whisper produces digits, words, compounds and
half-phrases ("an hour and a half", "two and a half hours"), and a misparsed
duration silently sets the wrong countdown. Every spoken form gets pinned here.

The store tests swap in an empty dict per test (same isolation trick as the
weather cache fixture), so nothing leaks between files.
"""

from __future__ import annotations

import time

import pytest

import actions
import timers
from intents import route


@pytest.fixture
def fresh_store(monkeypatch):
    """An empty timer store — no state leaks between tests."""
    monkeypatch.setattr(timers, "_timers", {})


# ------------------------------------------------------------ duration parsing
@pytest.mark.parametrize(
    "text, seconds",
    [
        ("set a timer for 10 minutes", 600),
        ("10 minute timer", 600),
        ("10-minute timer", 600),
        ("five minutes", 300),
        ("forty five seconds", 45),
        ("twenty minutes", 1200),
        ("fifteen minutes", 900),
        ("10 mins", 600),
        ("2 hrs", 7200),
        ("30 secs", 30),
        ("an hour", 3600),
        ("half an hour", 1800),
        ("a half hour", 1800),
        ("an hour and a half", 5400),
        ("two hours and a half", 9000),
        ("two and a half hours", 9000),
        ("set a timer for 1 hour 30 minutes", 5400),
        ("1.5 hours", 5400),
    ],
)
def test_parse_duration_forms(text: str, seconds: float) -> None:
    assert timers.parse_duration(text) == seconds


@pytest.mark.parametrize(
    "text",
    ["set a timer", "what's the weather", "hello", "how do timers work"],
)
def test_parse_duration_finds_nothing(text: str) -> None:
    assert timers.parse_duration(text) is None


# ----------------------------------------------------------- spoken rendering
@pytest.mark.parametrize(
    "total_s, spoken",
    [
        (3725, "1 hour, 2 minutes and 5 seconds"),
        (5400, "1 hour and 30 minutes"),
        (3600, "1 hour"),
        (120, "2 minutes"),
        (60, "1 minute"),
        (45, "45 seconds"),
        (0, "0 seconds"),
    ],
)
def test_format_duration(total_s: float, spoken: str) -> None:
    assert timers.format_duration(total_s) == spoken


# ---------------------------------------------------------------- label logic
def test_label_extracted_before_timer_word() -> None:
    assert timers.extract_timer_args("set a tea timer for 3 minutes") == (180, "tea")


def test_label_extracted_after_duration() -> None:
    assert timers.extract_timer_args("set a timer for 3 minutes for the eggs") == (180, "eggs")


def test_prefix_filler_is_not_a_label() -> None:
    assert timers.extract_timer_args("hey loki set a timer for 5 minutes") == (300, "")
    assert timers.extract_timer_args("can you set a timer for 2 minutes") == (120, "")


def test_no_duration_no_label() -> None:
    assert timers.extract_timer_args("set a timer") == (None, "")


def test_long_remainder_is_not_a_label() -> None:
    # A whole trailing clause is not a name — the label cap drops it.
    seconds, label = timers.extract_timer_args(
        "set a timer for 2 minutes and then remind me to stretch my legs"
    )
    assert seconds == 120
    assert label == ""


# -------------------------------------------------------------------- store
def test_start_ack_names_duration_and_label(fresh_store) -> None:
    assert timers.start(600) == (True, "Timer set for 10 minutes.")
    assert timers.start(180, "tea") == (True, "Timer set for 3 minutes for tea.")


def test_start_rejects_below_minimum(fresh_store) -> None:
    assert timers.start(0.5) == (False, "A timer needs to be at least one second.")


def test_start_rejects_above_one_day(fresh_store) -> None:
    assert timers.start(24 * 3600 + 1) == (False, "I can only set timers up to 24 hours.")
    assert timers.start(24 * 3600)[0]  # the boundary itself is fine


def test_start_rejects_the_ninth_timer(fresh_store) -> None:
    for i in range(8):
        assert timers.start(60 + i)[0]
    assert timers.start(60) == (False, "You already have 8 timers running.")


def test_cancel_on_empty_store(fresh_store) -> None:
    assert timers.cancel() == (True, "There are no timers running.")


def test_cancel_all_counts(fresh_store) -> None:
    timers.start(60, "tea")
    assert timers.cancel(all_timers=True) == (True, "Cancelled your timer.")
    timers.start(60, "tea")
    timers.start(600, "eggs")
    assert timers.cancel(all_timers=True) == (True, "Cancelled 2 timers.")


def test_bare_cancel_takes_the_soonest(fresh_store) -> None:
    timers.start(600, "eggs")
    timers.start(60, "tea")
    assert timers.cancel() == (True, "Cancelled the tea timer.")
    assert [t.label for t in timers._timers.values()] == ["eggs"]


def test_cancel_by_label(fresh_store) -> None:
    timers.start(180, "tea")
    timers.start(300, "eggs")
    assert timers.cancel("tea") == (True, "Cancelled the tea timer.")
    assert [t.label for t in timers._timers.values()] == ["eggs"]


def test_cancel_by_duration(fresh_store) -> None:
    timers.start(600)
    assert timers.cancel("10 minutes") == (True, "Cancelled the 10 minute timer.")


def test_cancel_duration_miss_is_spoken(fresh_store) -> None:
    timers.start(300)
    assert timers.cancel("10 minutes") == (True, "I don't have a 10 minutes timer running.")


def test_cancel_label_miss_is_spoken(fresh_store) -> None:
    timers.start(600, "eggs")
    assert timers.cancel("pizza") == (True, "I don't have a timer for pizza.")


def test_status_empty(fresh_store) -> None:
    assert timers.status() == (True, "There are no timers running.")


def test_status_one_timer(fresh_store) -> None:
    timers.start(600, "tea")
    assert timers.status() == (True, "Your tea timer has 10 minutes left.")


def test_status_many_timers_soonest_first(fresh_store) -> None:
    timers.start(600, "eggs")
    timers.start(60, "tea")
    assert timers.status() == (
        True,
        "You have 2 timers: the tea timer has 1 minute left "
        "and the eggs timer has 10 minutes left.",
    )


def test_status_reports_an_already_due_timer(fresh_store) -> None:
    timers._timers[1] = timers._Timer(
        id=1, label="tea", duration_s=180.0, due=time.monotonic() - 1
    )
    assert timers.status() == (True, "Your tea timer is done.")


def test_pop_due_empty(fresh_store) -> None:
    assert timers.pop_due() == []


def test_pop_due_removes_and_announces_in_due_order(fresh_store) -> None:
    now = time.monotonic()
    timers._timers[1] = timers._Timer(id=1, label="tea", duration_s=180.0, due=now - 5)
    timers._timers[2] = timers._Timer(id=2, label="", duration_s=60.0, due=now - 1)
    assert timers.pop_due() == [
        "Your tea timer is done.",
        "Your 1 minute timer is done.",
    ]
    assert timers._timers == {}


def test_pop_due_leaves_running_timers_alone(fresh_store) -> None:
    timers.start(600, "eggs")
    assert timers.pop_due() == []
    assert len(timers._timers) == 1


def test_poll_interval_only_when_running(fresh_store) -> None:
    assert timers.poll_interval_s() is None
    timers.start(600)
    assert timers.poll_interval_s() == 0.5


# ------------------------------------------------------------ action wiring
def test_timer_handlers_are_registered() -> None:
    assert actions.ACTION_MAP["set_timer"] is actions.set_timer
    assert actions.ACTION_MAP["cancel_timer"] is actions.cancel_timer
    assert actions.ACTION_MAP["timer_status"] is actions.timer_status


def test_execute_set_timer_from_raw_utterance(fresh_store) -> None:
    ok, text = actions.execute("set_timer", query="hey loki set a tea timer for 3 minutes")
    assert (ok, text) == (True, "Timer set for 3 minutes for tea.")
    assert [t.label for t in timers._timers.values()] == ["tea"]


def test_execute_set_timer_without_duration_falls_to_llm(fresh_store) -> None:
    with pytest.raises(actions.ActionUnavailable):
        actions.execute("set_timer", query="set a timer")


def test_execute_cancel_all(fresh_store) -> None:
    actions.execute("set_timer", query="set a timer for 5 minutes")
    actions.execute("set_timer", query="set a timer for 10 minutes")
    assert actions.execute("cancel_timer", query="all") == (True, "Cancelled 2 timers.")


def test_execute_cancel_everything(fresh_store) -> None:
    actions.execute("set_timer", query="set a timer for 5 minutes")
    assert actions.execute("cancel_timer", query="everything") == (
        True,
        "Cancelled your timer.",
    )


def test_execute_timer_status(fresh_store) -> None:
    assert actions.execute("timer_status") == (True, "There are no timers running.")


# --------------------------------------------------------- router contracts
def test_router_hands_the_whole_utterance_to_set_timer() -> None:
    result = route("set a tea timer for 3 minutes")
    assert result is not None
    assert result.intent.name == "timer_set"
    assert result.args == {"query": "set a tea timer for 3 minutes"}


def test_router_hands_cancel_scope_to_cancel_timer() -> None:
    result = route("cancel my tea timer")
    assert result is not None
    assert result.intent.name == "timer_cancel"
    assert result.args == {"query": "tea"}


def test_router_cancel_all_captures_all() -> None:
    result = route("cancel all timers")
    assert result is not None
    assert result.intent.name == "timer_cancel"
    assert result.args == {"query": "all"}
