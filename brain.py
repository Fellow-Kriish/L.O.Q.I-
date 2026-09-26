"""
LOQI — Groq LLM Fallback (Cloud Brain)

Chat completion via Groq's LPU inference. Used ONLY when the local intent router
has no match — open-ended questions, drafting text, explaining concepts, etc.

Two public entry points:
    * ``ask(text) -> str``            — full response, for text mode / logging.
    * ``ask_stream(text, stop_event) -> Iterator[str]`` — sentences as they stream in, so TTS
      can start speaking sentence 1 while sentence 2 is still generating.
      Pass a threading.Event as stop_event to cancel mid-stream (barge-in).

Reliability: the Groq SDK retries transient errors (429 / 5xx / connection) with
backoff internally (honoring Retry-After); on top of that we fail over from the
primary model to the backup model and never raise into the caller — a spoken
assistant must always say *something*.

Critical rule: LLM output is DATA, not AUTHORITY. Any action-shaped output routes
through confirm.py's gate, never executes directly.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Iterator

from groq import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    Groq,
    RateLimitError,
)

import config
from logging_setup import get_logger

log = get_logger(__name__)

# Sentence boundary: punctuation followed by whitespace. Compiled once.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_NO_KEY_MSG = "I can't reach the cloud right now. My API key isn't set up yet."
_ERROR_MSG = "Sorry, I couldn't reach the cloud right now. Try again in a moment."


class Brain:
    """Groq-backed cloud reasoning with streaming + sentence chunking."""

    def __init__(
        self,
        api_key: str = config.GROQ_API_KEY,
        model: str = config.GROQ_MODEL,
        backup_model: str = config.GROQ_BACKUP_MODEL,
        max_retries: int = config.GROQ_MAX_RETRIES,
    ):
        if not api_key:
            log.warning(
                "No GROQ_API_KEY set — cloud fallback will fail. Get a free key at "
                "https://console.groq.com/keys and add GROQ_API_KEY=... to your .env file."
            )

        # max_retries drives the SDK's own transient-error backoff.
        self.client = Groq(api_key=api_key, max_retries=max_retries) if api_key else None
        self.model = model
        self.backup_model = backup_model

        # Conversation history (last N turns).
        self.history: list[dict[str, str]] = []
        self.max_history = config.GROQ_HISTORY_LENGTH

    # ------------------------------------------------------------------ public
    def ask(self, text: str) -> str:
        """Return the full response as a string. Never raises."""
        if not self.client:
            return _NO_KEY_MSG

        messages = self._prepare(text)
        completion = self._create(messages, stream=False)
        if completion is None:
            return _ERROR_MSG

        content = (completion.choices[0].message.content or "").strip()
        self.history.append({"role": "assistant", "content": content})
        return content

    def ask_stream(self, text: str, stop_event: threading.Event | None = None) -> Iterator[str]:
        """Yield complete sentences as they stream in. Never raises.

        Args:
            text: The user's utterance.
            stop_event: Optional threading.Event. When set (e.g. by barge-in),
                        the stream is cancelled immediately and no further
                        sentences are yielded.
        """
        if not self.client:
            yield _NO_KEY_MSG
            return

        messages = self._prepare(text)
        stream = self._create(messages, stream=True)
        if stream is None:
            yield _ERROR_MSG
            return

        buffer = ""
        full_response = ""
        try:
            for chunk in stream:
                # Barge-in: caller signalled stop — close the stream and bail.
                if stop_event is not None and stop_event.is_set():
                    with contextlib.suppress(Exception):
                        stream.close()
                    break

                delta = chunk.choices[0].delta
                token = getattr(delta, "content", None)
                if not token:
                    continue
                buffer += token
                full_response += token

                # Emit every complete sentence, keep the trailing fragment.
                sentences = _SENTENCE_SPLIT.split(buffer)
                if len(sentences) > 1:
                    for sentence in sentences[:-1]:
                        sentence = sentence.strip()
                        if sentence:
                            yield sentence
                    buffer = sentences[-1]
        except (APIStatusError, APIConnectionError) as e:
            log.error("Groq stream interrupted mid-response: %s", e)
            if not full_response:
                yield _ERROR_MSG
                return

        # Only yield the trailing buffer if we weren't barged in on.
        if buffer.strip() and (stop_event is None or not stop_event.is_set()):
            yield buffer.strip()

        if full_response:
            self.history.append({"role": "assistant", "content": full_response})

    def clear_history(self) -> None:
        """Clear conversation history."""
        self.history.clear()

    # --------------------------------------------------------------- internals
    def _prepare(self, text: str) -> list[dict[str, str]]:
        """Append the user turn, trim history, and build the messages list."""
        self.history.append({"role": "user", "content": text})
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history :]
        return [{"role": "system", "content": config.GROQ_SYSTEM_PROMPT}, *self.history]

    def _create(self, messages: list[dict[str, str]], stream: bool):
        """
        Call Groq, trying the primary model then the backup. Returns the SDK
        response (a completion or a stream), or None if both models fail.

        The SDK already retried transient errors before raising; our job here is
        to decide when failing over to the backup model is worthwhile.
        """
        assert self.client is not None  # guarded by callers
        last_error: Exception | None = None

        for model in (self.model, self.backup_model):
            try:
                # The SDK's type stub wants a union of TypedDicts; plain role/content
                # dicts are accepted at runtime. Cast is not worth the import weight.
                return self.client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type, unused-ignore]
                    stream=stream,
                    max_tokens=config.GROQ_MAX_TOKENS,
                    temperature=config.GROQ_TEMPERATURE,
                )
            except AuthenticationError as e:
                # Both models use the same key — failover cannot help. Stop.
                log.error("Groq authentication failed: %s", e)
                return None
            except RateLimitError as e:
                last_error = e
                log.warning("Groq rate-limited on %s (after retries); trying backup.", model)
            except APIStatusError as e:
                last_error = e
                log.warning(
                    "Groq API error %s on %s: %s; trying backup.",
                    getattr(e, "status_code", "?"), model, e,
                )
            except APIConnectionError as e:
                last_error = e
                log.warning("Groq connection error on %s: %s; trying backup.", model, e)
            except Exception as e:  # noqa: BLE001 — must never crash the assistant
                last_error = e
                log.warning("Unexpected Groq error on %s: %s; trying backup.", model, e)

        log.error("Groq request failed on both primary and backup models: %s", last_error)
        return None
