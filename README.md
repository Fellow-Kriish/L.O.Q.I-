# L.O.Q.I.

**Local Operations & Query Interface** — a local-first voice assistant for Windows.
(Written "L.O.Q.I.", pronounced *"Loki"*.)

LOQI wakes on a wake word, listens, and either **handles the command locally and
deterministically** through a regex intent router, or — for anything open-ended —
falls back to a cloud LLM. Speech recognition and speech synthesis run on your
machine; only the LLM fallback leaves it.

---

## How it works

```
 wake word          record            transcribe        route
 (openWakeWord) ──▶ (VAD, webrtcvad) ─▶ (faster-whisper) ─▶ intent router
 hey_loki.onnx                                              (regex, anchored)
                                                                │
                                          ┌─────────────────────┴─────────────────────┐
                                          ▼                                             ▼
                                   matched intent                                no match
                                          │                                             │
                              tier 0/1: run now                              Groq LLM fallback
                              tier 2/3: confirm first  ◀── confirm.py            (brain.py)
                                          │                                             │
                                          └──────────────▶  speak  ◀────────────────────┘
                                                          (Kokoro TTS)
```

- **Wake word** — [openWakeWord](https://github.com/dscripka/openWakeWord) with a
  custom `hey_loki.onnx` model.
- **Recording** — `webrtcvad` voice-activity detection trims silence and stops on
  end-of-speech.
- **STT** — [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  (CTranslate2). Runs on CPU by default; CUDA-capable (see [GPU](#gpu-optional)).
- **Intent router** (`intents.py`) — regex patterns matched against the **whole**
  utterance. This is where most daily commands are resolved, instantly and offline.
- **Confirm gate** (`confirm.py`) — higher-permission actions (closing apps, etc.)
  are read back and confirmed before running.
- **LLM fallback** (`brain.py`) — only reached when nothing matches; answers via the
  [Groq](https://groq.com) API.
- **TTS** — [Kokoro](https://github.com/hexgrad/kokoro) neural voice.

### The router's one rule: precision

Patterns are anchored to the **entire** normalized utterance, never a substring.
Before this, a bare keyword anywhere in a sentence hijacked the router — *"what's
the time complexity of quicksort"* answered with the wall clock. The regression
suite encodes this as two lists in `tests/corpus.py`:

- `MUST_MATCH` — utterances that must route to a specific intent.
- `MUST_FALL_THROUGH` — ordinary questions that must reach the LLM, matching nothing.

`tests/test_router.py` is the CI gate: **any new pattern must keep `MUST_FALL_THROUGH`
green.** That guarantee is what lets the router grow aggressively without regressions.

---

## Setup

Requires **Python 3.11+** on Windows.

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**TTS system dependency:** Kokoro needs **espeak-ng** installed on the system for
phonemization. Install it from the [espeak-ng releases](https://github.com/espeak-ng/espeak-ng/releases)
and ensure it's on your `PATH`.

**API key:** the LLM fallback needs a Groq API key. Create a `.env` file in the
project root:

```
GROQ_API_KEY=your_key_here
```

`.env` is gitignored — never commit it.

### GPU (optional)

STT and TTS both benefit from an NVIDIA GPU. The default install uses CPU wheels.
For CUDA, install the matching CUDA build of PyTorch from
[pytorch.org](https://pytorch.org) and make sure cuDNN 9 is on `PATH`, then point
STT at the GPU (see [Configuration](#configuration)). Capture your exact working
set with `pip freeze > requirements.lock` for reproducibility.

---

## Running

```bash
python main.py            # full mode: wake word → listen → respond
python main.py --no-wake  # skip the wake word; press Enter to speak
python main.py --text     # type instead of speak (no mic/TTS; great for testing the router)
```

`--text` mode loads no audio or ML models, so it starts instantly and is the fastest
way to exercise the router and actions.

---

## Configuration

All settings live in `config.py` as a typed, validated `pydantic-settings` model.
Every value can be overridden by an environment variable prefixed `LOQI_`, or via
`.env`. For example:

```
LOQI_STT_DEVICE=cuda
LOQI_STT_MODEL_SIZE=small.en
LOQI_STT_COMPUTE_TYPE=float16
LOQI_WAKE_WORD_THRESHOLD=0.6
LOQI_MIC_DEVICE_INDEX=15
```

Existing code reads the familiar uppercase constants (`config.GROQ_MODEL`, …); those
are kept as backward-compatible aliases over the settings model, so both styles work.

---

## Development

```bash
pip install -r requirements-dev.txt
pre-commit install          # optional: run lint on every commit
```

| Task            | Command                                   |
| --------------- | ----------------------------------------- |
| Lint            | `ruff check .`                            |
| Type-check      | `mypy .`                                  |
| Router tests    | `pytest tests/`                           |

CI (`.github/workflows/ci.yml`) runs **ruff + the router regression as the blocking
gate**, and **mypy as an advisory signal** (the v0.1 modules are being typed
incrementally). The router tests import only the standard library, so CI needs none
of the heavy ML dependencies.

Interactive hardware smoke scripts (`test_stt.py`, `test_tts.py`, `test_wakeword.py`,
`test_e2e.py`) live at the repo root; they need a mic/speaker and are run by hand,
not in CI.

---

## Project layout

Flat modules, run directly (not packaged):

| Module              | Responsibility                                        |
| ------------------- | ----------------------------------------------------- |
| `main.py`           | Entry point, mode dispatch, the main loop             |
| `config.py`         | Typed settings + backward-compatible constants        |
| `wakeword.py`       | Wake-word detection (openWakeWord)                    |
| `recorder.py`       | VAD-gated microphone capture                          |
| `stt.py`            | Speech-to-text (faster-whisper)                       |
| `intents.py`        | Regex intent router (full-utterance anchored)         |
| `actions.py`        | Handlers for matched intents                          |
| `app_registry.py`   | Known-application lookup for "open …"                 |
| `confirm.py`        | Read-back confirmation gate for higher-tier actions   |
| `brain.py`          | Groq LLM fallback                                     |
| `tts.py`            | Text-to-speech (Kokoro)                               |
| `fallback_log.py`   | Logs unmatched utterances for router improvement      |
| `logging_setup.py`  | Console + rotating-file logging                       |
