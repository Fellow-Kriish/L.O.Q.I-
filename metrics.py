"""
LOQI — Latency Metrics

Per-stage timing for one wake-to-silence interaction, appended to
``logs/latency.jsonl`` as a single record per turn.

One record per *turn*, not per stage, on purpose. The question worth answering
is "what was slow in the turns that felt slow", and that needs the stages of a
turn kept together. Records carry ``path`` ("action" / "llm" / "cancelled")
because those profiles are wildly different — a router hit is milliseconds, a
cloud fallback is a second or more — and averaging them together is how you end
up optimizing the wrong stage.

Collection is cheap enough to always be on: two perf_counter reads and a dict
write per stage, ~200ns against a turn measured in seconds. Only the file write
is gated, by ``config.METRICS_ENABLED``.

Nothing here may break a voice turn. close() swallows its own errors — a
metrics bug that costs the user their answer would be worse than no metrics.

Read the log back with::

    python metrics.py
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import config
from logging_setup import get_logger

log = get_logger(__name__)

# Canonical stage names. Constants rather than bare strings at each call site,
# so a typo cannot quietly create a second near-identical stage in the log.
ACK = "ack"                          # wake detected → acknowledgement audible
RECORD = "record"                    # VAD capture (user-paced, not our cost)
STT = "stt"                          # wav → text
ROUTE = "route"                      # regex router
CONFIRM = "confirm"                  # tier 2/3 gate, incl. waiting on the user
ACTION = "action"                    # deterministic handler
LLM_FIRST = "llm_first_sentence"     # turn start → first sentence spoken
LLM_TOTAL = "llm_total"              # whole stream consumed
TTS = "tts"                          # synthesis + playback, summed over sentences

# Chronological order for the console summary. Stages not listed here still
# appear in the log; they just sort last.
_STAGE_ORDER = (ACK, RECORD, STT, ROUTE, CONFIRM, ACTION, LLM_FIRST, LLM_TOTAL, TTS)

_write_lock = threading.Lock()


class Turn:
    """
    Timings for one interaction.

    Set ``path``, ``intent`` and ``tier`` directly as they become known; wrap
    each stage in :meth:`stage`. The record is written by :meth:`close`, which
    ``turn()`` calls for you.
    """

    __slots__ = ("path", "intent", "tier", "stages_ms", "failed", "total_ms", "_t0", "_closed", "_lock")

    def __init__(self) -> None:
        self.path: str = "unknown"
        self.intent: str | None = None
        self.tier: int | None = None
        self.stages_ms: dict[str, float] = {}
        self.failed: list[str] = []
        self.total_ms: float = 0.0
        self._t0 = time.perf_counter()
        self._closed = False
        # TTS may run on a worker thread (streaming playback), so stage
        # bookkeeping has to be safe from more than one thread.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ timing
    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """
        Time a block and record it under ``name``.

        Repeat calls with the same name accumulate, which is what you want for
        per-sentence TTS: one "tts" figure for the whole turn.

        The elapsed time is recorded even when the block raises, and the stage
        is flagged as failed. A stage that blew up after four seconds is
        precisely the data point you want, and that is the worst moment to drop
        it.
        """
        t0 = time.perf_counter()
        try:
            yield
        except BaseException:
            self._add(name, (time.perf_counter() - t0) * 1000.0, failed=True)
            raise
        else:
            self._add(name, (time.perf_counter() - t0) * 1000.0)

    def first(self, name: str) -> None:
        """
        Record time-since-turn-start the first time this is called; ignore the
        rest.

        For "time to first spoken sentence" — the latency the user actually
        feels on a streamed answer, since everything after it arrives while
        they are already listening.
        """
        with self._lock:
            self.stages_ms.setdefault(name, (time.perf_counter() - self._t0) * 1000.0)

    def mark(self, name: str, ms: float) -> None:
        """Record a span measured elsewhere (e.g. inside a worker thread)."""
        self._add(name, ms)

    def _add(self, name: str, ms: float, failed: bool = False) -> None:
        with self._lock:
            self.stages_ms[name] = self.stages_ms.get(name, 0.0) + ms
            if failed and name not in self.failed:
                self.failed.append(name)

    # ------------------------------------------------------------------ output
    def summary(self) -> str:
        """A one-line console summary, stages in chronological order."""
        def order(item: tuple[str, float]) -> tuple[int, str]:
            name = item[0]
            return (_STAGE_ORDER.index(name) if name in _STAGE_ORDER else len(_STAGE_ORDER), name)

        parts = [f"{name} {_human_ms(ms)}" for name, ms in sorted(self.stages_ms.items(), key=order)]
        total = _human_ms(self.total_ms or (time.perf_counter() - self._t0) * 1000.0)
        return f"{total} total  ·  " + " · ".join(parts) if parts else f"{total} total"

    def as_record(self) -> dict:
        """The JSONL record for this turn."""
        record: dict = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "path": self.path,
            "total_ms": round(self.total_ms, 2),
            "stages_ms": {name: round(ms, 2) for name, ms in self.stages_ms.items()},
        }
        if self.intent is not None:
            record["intent"] = self.intent
        if self.tier is not None:
            record["tier"] = self.tier
        if self.failed:
            record["failed"] = self.failed
        return record

    def close(self) -> None:
        """
        Finalize the turn and append its record. Idempotent, and never raises.
        """
        if self._closed:
            return
        self._closed = True
        self.total_ms = (time.perf_counter() - self._t0) * 1000.0

        if not config.METRICS_ENABLED:
            return

        try:
            line = json.dumps(self.as_record(), ensure_ascii=False)
            with _write_lock, open(config.LATENCY_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            # A metrics failure must never cost the user their answer.
            log.debug("Could not write the latency record.", exc_info=True)


@contextmanager
def turn() -> Iterator[Turn]:
    """
    Time one interaction.

    Always closes, so a turn that raises is still logged — a crash has a
    latency worth knowing too.
    """
    t = Turn()
    try:
        yield t
    finally:
        t.close()


def _human_ms(ms: float) -> str:
    """Format a duration for the console: sub-ms, ms, or seconds."""
    if ms < 1.0:
        return f"{ms:.2f}ms"
    if ms < 1000.0:
        return f"{ms:.0f}ms"
    return f"{ms / 1000.0:.2f}s"


# ---------------------------------------------------------------------------
# Reading the log back
# ---------------------------------------------------------------------------

def load(limit: int | None = None) -> list[dict]:
    """
    Read latency records, newest last. Malformed lines are skipped.

    ``limit`` keeps only the most recent N records, which is usually what you
    want — you are asking about the build you are running now.
    """
    try:
        with open(config.LATENCY_LOG_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return []

    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records[-limit:] if limit else records


def report(limit: int | None = None) -> str:
    """
    Summarize the log as p50/p95 per stage, grouped by path.

    p95 rather than a mean because latency is what the user notices, and they
    notice the bad turns. A mean hides them.
    """
    records = load(limit)
    if not records:
        return f"No latency records yet ({config.LATENCY_LOG_PATH})."

    by_path: dict[str, list[dict]] = {}
    for record in records:
        by_path.setdefault(str(record.get("path", "unknown")), []).append(record)

    lines = [f"{len(records)} turns  ·  {config.LATENCY_LOG_PATH}"]
    for path in sorted(by_path):
        group = by_path[path]
        lines.append("")
        lines.append(f"── {path}  ({len(group)} turns)")

        totals = [float(r["total_ms"]) for r in group if "total_ms" in r]
        if totals:
            p50, p95 = _percentiles(totals)
            lines.append(f"   {'total':<20} p50 {_human_ms(p50):>8}   p95 {_human_ms(p95):>8}")

        stage_samples: dict[str, list[float]] = {}
        for record in group:
            for name, ms in (record.get("stages_ms") or {}).items():
                stage_samples.setdefault(name, []).append(float(ms))

        def order(name: str) -> tuple[int, str]:
            return (_STAGE_ORDER.index(name) if name in _STAGE_ORDER else len(_STAGE_ORDER), name)

        for name in sorted(stage_samples, key=order):
            samples = stage_samples[name]
            p50, p95 = _percentiles(samples)
            lines.append(
                f"   {name:<20} p50 {_human_ms(p50):>8}   p95 {_human_ms(p95):>8}   (n={len(samples)})"
            )

    return "\n".join(lines)


def _percentiles(samples: list[float]) -> tuple[float, float]:
    """(p50, p95). Degrades sensibly on tiny samples rather than raising."""
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0], ordered[0]
    p50 = statistics.median(ordered)
    # Nearest-rank p95: with a handful of turns this is the honest answer, and
    # interpolating between two samples would invent precision we don't have.
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return p50, ordered[index]


if __name__ == "__main__":
    print(report())
