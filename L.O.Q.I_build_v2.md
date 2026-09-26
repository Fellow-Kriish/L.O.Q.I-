# L.O.Q.I. Build Doc v2 — Local Voice Assistant + Supervised PC Control
_Updated with Sept 2026 research. Supersedes the v1 build doc's section 0/2/3 numbers._

## 0. Reality Check — Updated

Asked directly: can this become a full-autonomy "do anything on my PC" assistant like the movie?

**No. Not a hardware problem. An unsolved-industry-problem problem.**

Hard data, Sept 2026:
- OSWorld 2.0 (long-horizon, realistic multi-app tasks): best model on earth, Claude Opus 4.8, completes only **20.5%** of tasks end-to-end. Median human: 72%+, doing it in under 2 hours.
- Same paper: across tested agents, completion rate range **4.6%–14.0%**, most runs stop at "partial progress," not done.
- Small/local-sized open models (7B–8B, the size that'd fit your 4GB VRAM) collapse hardest on real tasks: GUI-OWL-8B goes from 52.3% (easy bench) to **5.7%** (realistic bench).
- Indirect prompt injection (malicious text on a webpage/doc hijacking the agent) — still an open, documented risk in 2026, no fix shipped anywhere.

Conclusion: a full-autonomy screen-agent, even using the best cloud model money can buy, fails most real tasks and can be hijacked by content it reads. On your 4GB card, a local vision-agent isn't "slow," it's not viable at all — drop the idea entirely, don't half-build it.

**What IS viable and worth building:** router (yours, deterministic) → cloud LLM brain (open-ended answers/tasks) → a **supervised action layer** — pre-wired actions you approved (open app, send message via one app, file operations in one folder) plus a confirm-before-execute gate for anything new/destructive. This gives you 80% of "personal assistant" feel with near-zero of the failure/security surface above.

---

## 1. Hardware (unchanged, confirmed)

Intel i5 HX, RTX 2050 4GB VRAM, 12GB RAM.

Confirmed against Sept 2026 model landscape:
- 4GB VRAM realistic ceiling: **3B–4B model at Q4**, ~2–4GB. Comfortable headroom for OS + other processes.
- Best fits for this tier right now: **Llama 3.2 3B**, **Qwen3 4B**, **Phi-4-mini 3.8B**, **Gemma 4 E2B**. Any of these run fine locally if you ever want an offline-only fallback brain (slower, dumber than cloud, but zero network dependency).
- Not changed: no local 70B+, no local vision/screen model, no MoE-streaming trick applies here (wrong tool, sparse-MoE only).

---

## 2. Tech Stack (v2)

| Layer | Tool | Notes (2026) |
|---|---|---|
| Wake word | openWakeWord | unchanged, still best free option |
| STT | faster-whisper (`small.en`, float16 on GPU / int8 CPU fallback) | unchanged |
| Intent routing | Python regex/keyword table | unchanged, this is the reliability backbone |
| Reasoning (cloud) | Groq API — `qwen/qwen3.8-27b` primary, `openai/gpt-oss-120b` as backup | Both are Groq *Production* models (Preview models must not be used — enforced by the comment on `config.py`'s `groq_model`). Free tier confirmed live: 30 RPM, per-model daily caps in the low thousands of requests — fine for solo use. Groq killed Mixtral/Mistral models in 2025 — don't spec those. |
| Reasoning (local fallback, optional) | Llama 3.2 3B or Qwen3 4B via Ollama, Q4 | only if you want offline mode; noticeably weaker than the Groq cloud models |
| TTS | Kokoro-82M | unchanged, still best small local TTS |
| VAD | webrtcvad | unchanged |
| Known-app actions | `webbrowser`, `subprocess`, `pywinauto` | unchanged — deterministic, no vision model |
| Supervised action layer (new) | confirm-gate wrapper around any non-whitelisted action | new in v2, see section 5 |

**Still explicitly not used:** OSWorld-style vision/screen agents, OmniParser, click-coordinate control, any "autonomous full desktop" framework (Agent S2, UI-TARS, CoAct-1, etc.) — these are the current state of the art and still fail 40–95% of real tasks depending on difficulty. Not worth the risk or the latency (multi-second per action) for a personal daily-driver tool.

---

## 3. Architecture / Process Flow

```
[Always-on] Wake word listener (own thread)
        ↓
Record until silence (VAD)
        ↓
faster-whisper → text
        ↓
Local intent router (regex/keyword)
        │
        ├── MATCH known command
        │       → whitelisted action executes directly, no confirm needed
        │       → canned response → Kokoro TTS
        │
        ├── MATCH but action is destructive/new (e.g. "delete X", "send email to Y")
        │       → speak back what it's about to do
        │       → wait for verbal "yes"/"confirm"
        │       → then execute
        │
        └── NO MATCH → open-ended
                → Groq API, streaming
                → sentence-boundary chunking → Kokoro TTS, overlapped playback
                → if the answer implies an action (e.g. "draft and send this email"),
                  route the proposed action back through the confirm-gate above —
                  LLM output is NEVER auto-executed as a command, always re-checked
```

Rule that matters most in v2: **LLM output is data, not authority.** Whether it comes from the cloud model or (someday) from a webpage/email the assistant reads, nothing it produces should directly trigger a shell command, file deletion, or message-send without passing through the same confirm-gate a typed command would. This is the direct fix for the indirect-prompt-injection risk documented above.

---

## 4. Permission Tiers

| Tier | Examples | Confirmation |
|---|---|---|
| Tier 0 — read-only/local | tell time, open a known site, play music | none, instant |
| Tier 1 — reversible action | open an app, search the web, answer a question | none, instant |
| Tier 2 — sends/writes | send a message, save a file, post something | spoken confirm required |
| Tier 3 — destructive/irreversible | delete file, uninstall, send money, modify system settings | spoken confirm + repeat-back of exact action |
| Never | anything from unreviewed screen/webpage/email content treated as a command | not automatable — always surfaced to you as "X suggested this, want me to do it?" |

This tiering is what "supervised PC access" means in practice — not full autonomy, not zero access either.

---

## 5. Build Order (v0 → v1)

1. Intent router — top 5–10 commands, typed-text tested, no audio yet.
2. Actions layer — wire each intent to real action, add Tier labels (0/1/2/3) per action now, not later.
3. Confirm-gate — build this before adding cloud fallback, not after. Simple: Tier 2/3 actions speak back the action, wait for yes/no via a second STT pass.
4. STT — faster-whisper feeding the already-tested router.
5. TTS — Kokoro speaking canned + confirm-gate responses.
6. Wake word — openWakeWord.
7. Groq fallback — streaming, chunk-and-speak. Any action-shaped output from Groq routes through step 3's gate, never executes directly.
8. Logging — every fallback utterance logged, reviewed manually, promoted to router pattern when repeated (same as v1 doc — still correct, still deferred-automation for the reasons already stated).

Each step: runnable before next. Stuck on a step = fix that step, not a new architecture doc.

---

## 6. Known Trade-offs

- Groq free tier: 30 RPM, ~1,000–14,400 req/day depending on model — fine solo, not for heavy polling.
- Not private end-to-end: Groq call leaves your machine. Local-only fallback (section 2) trades privacy for capability if ever needed.
- No screen-reading, no click-based control, no "do anything visually" mode — deliberate, see section 0. Revisit only if a future OSWorld-class benchmark shows small local/cheap models clearing ~60%+ on long-horizon tasks, not before.
- Confirm-gate adds a few seconds of friction to Tier 2/3 actions. That's the cost of not getting hijacked or nuking a file by accident — worth it.
