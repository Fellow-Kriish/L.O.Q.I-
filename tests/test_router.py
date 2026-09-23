"""
Intent-router regression tests (the CI gate).

Run with::

    pytest tests/test_router.py -v

Two properties are enforced:

1. Every ``MUST_MATCH`` utterance routes to its expected intent.
2. Every ``MUST_FALL_THROUGH`` utterance routes to *nothing* (None), so it
   reaches the LLM. This is the precision guarantee the whole router design
   rests on — see ``tests/corpus.py``.

No microphone, model, or network is touched: this exercises pure regex routing.
"""

from __future__ import annotations

import pytest
from corpus import MUST_FALL_THROUGH, MUST_MATCH

from intents import route


@pytest.mark.parametrize("text, expected", MUST_MATCH, ids=[t for t, _ in MUST_MATCH])
def test_must_match(text: str, expected: str) -> None:
    result = route(text)
    assert result is not None, f"{text!r} should route to {expected!r} but fell through to the LLM"
    assert result.intent.name == expected, (
        f"{text!r} routed to {result.intent.name!r}, expected {expected!r}"
    )


@pytest.mark.parametrize("text", MUST_FALL_THROUGH, ids=MUST_FALL_THROUGH)
def test_must_fall_through(text: str) -> None:
    result = route(text)
    assert result is None, (
        f"{text!r} was hijacked by intent {result.intent.name!r} (args={result.args}); "
        "it must fall through to the LLM"
    )
