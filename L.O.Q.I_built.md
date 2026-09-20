# Jarvis Build Doc — Local Voice Assistant

## 0. Reality Check (read this first)

This is not "any task, like the movie." That doesn't exist anywhere yet — not from Anthropic, OpenAI, or Microsoft, with teams of hundreds. What this doc specs is a **voice-controlled command router with a cloud LLM fallback**, running on real hardware you own. That's a genuinely useful, buildable thing. Don't let the name "Jarvis" pull the scope back toward the fictional version — that's how this stalls.

**Your hardware:** Intel i5 HX-series, RTX 2050 (4GB VRAM), 12GB RAM.
**Hard constraint this sets:** no local model heavier than ~3B–8B quantized fits comfortably. No local vision/screen-perception model. No local 70B+ model, with or without exotic streaming tricks (MoE-streaming engines like Colibrì don't apply — that trick only works for sparse Mixture-of-Experts models with huge idle parameter counts; dense 70B models activate every parameter every token, so there's nothing to leave on disk). Reasoning power comes from a **free cloud API**, not local weights.

---

## 1. What This Builds

A voice assistant that:
- Listens for a wake word, transcribes speech locally.
- Matches common commands (open a site, launch an app, play music, tell time, send a message) instantly via a local rule-based router — **no network call, no LLM, for known commands.**
- Falls back to a cloud LLM (Groq, free tier) for anything open-ended (draft an email, answer a question, write code).
- Speaks the response back, locally synthesized, streamed sentence-by-sentence so it starts talking before the full answer is ready.

## 2. Why This Architecture (not the "vision agent does everything" version)

The alternative — a general computer-use agent that takes a screenshot, feeds it to a vision model, and clicks coordinates for every task — is the unsolved industry problem (per OSWorld benchmark: best agents ~20–40% success rate, human baseline ~72%, and that's on datacenter GPUs). It's also slow (2–5 sec per action) and carries a documented security hole: indirect prompt injection, where malicious text rendered on screen gets read and obeyed by the agent (documented Attack Success Rates around 48% in red-team testing, jumping higher with multi-step attacks).

The router-first approach sidesteps both problems for the 80% of tasks you'll actually ask daily, and only pays the slow/risky LLM cost when genuinely necessary.

## 3. Tech Stack

| Layer | Tool | Why | Runs where |
|---|---|---|---|
| Wake word | openWakeWord | Free, MIT license, runs on near-nothing CPU, always-on listening | Local |
| Speech-to-text | faster-whisper (tiny or base model, int8) | Sub-second on CPU, no cloud round-trip, no privacy leak | Local |
| Intent routing | Python regex / keyword match | Near-zero latency, deterministic, no hallucination risk | Local |
| Reasoning ("brain") | Groq API — Llama 3.3 70B (free tier) | Free, ~300–500 tok/sec (LPU hardware), far more reasoning than local 4GB VRAM can host | Cloud (only when router misses) |
| Text-to-speech | Kokoro-82M | 82M params, ~300MB, near-instant even on CPU, natural voice | Local |
| Actions (open app/site/message) | `webbrowser`, `os`/`subprocess`, `pywinauto` (Windows UIA) | Direct, deterministic, no vision model needed, faster and more reliable than screen-reading | Local |
| Voice activity detection | webrtcvad (or similar) | Ends recording on actual silence, not a fixed timer — cuts dead air | Local |

**Not used, and why:** local 70B+ models (don't fit 4GB VRAM), OmniParser/vision-based screen agents (too slow, too risky, unnecessary for wired commands), MoE-streaming engines like Colibrì (wrong tool — built for sparse 744B MoE models, not applicable here), OpenAI API as primary brain (paid, no meaningful free tier — Groq/Gemini free tiers cover this use case at $0).

## 4. Architecture / Process Flow

```
[Always-on] Wake word listener (openWakeWord, own thread, near-zero CPU)
        ↓ (wake word detected)
Record audio until silence (VAD-based, not fixed timer)
        ↓
faster-whisper transcribes → text
        ↓
Local intent router (regex/keyword match) checks text
        │
        ├── MATCH (known command: "open youtube", "message X", "play music", "what time is it")
        │       → execute directly (webbrowser.open / subprocess / pywinauto)
        │       → canned or templated response text
        │       → Kokoro TTS → play audio
        │       (no network call — this path is milliseconds)
        │
        └── NO MATCH (open-ended: "draft an email about...", "explain...", "write code for...")
                → Groq API call, STREAMING enabled
                → as tokens arrive, split on sentence boundaries
                → send each finished sentence to Kokoro TTS immediately
                → play sentence 1 while sentence 2 is still generating/synthesizing
                → (this overlap is what makes cloud responses feel fast, not the raw API speed alone)
```

**Engineering rules that matter for latency (in order of actual impact):**
1. Router before LLM, always. Every command that can be handled locally must never touch the network.
2. Stream the LLM response — don't wait for the full answer before speaking any of it.
3. VAD-based recording end, not a fixed-duration timer.
4. Load faster-whisper and Kokoro models once at startup, keep resident in memory — never reload per request.
5. Run wake-word listening on its own thread so it's never blocked by TTS playback or an in-flight LLM call.
6. Regex pattern count is a non-issue for latency — matching cost is microseconds regardless of how many patterns exist. Don't under-build the router out of a mistaken latency-economy instinct; more coverage here means fewer expensive round-trips to the cloud, not more.

## 5. Self-Improving Router (future feature, not v1)

**Manual version — do this from day one, it's free:** log every utterance that falls through to the LLM fallback (i.e., every router miss). Review the log periodically. When a phrase repeats (e.g., "open spotify" shows up 5 times), add it as a new regex pattern by hand. This is cheap, safe, and the router naturally improves with real usage.

**Automated version — explicitly deferred, not part of v1:** auto-generating and auto-inserting new regex patterns from LLM output, without a human reviewing them first, is a bad idea at this stage for two reasons:
- It's optimization work on a system that doesn't have a working v0 yet — premature.
- Letting an external model's output silently rewrite your own control/routing logic is the same category of risk as the indirect-prompt-injection problem flagged in section 2 — a foothold for the router to be steered by content it was never meant to trust, especially once (if ever) the assistant reads external content like emails or webpages. Keep a human in the loop reviewing the miss-log before any new pattern is added, indefinitely.

## 6. Security Notes (don't skip these when scope grows)

- Any future feature that lets the assistant act on content it reads (a webpage, an email, a file) inherits the indirect prompt injection risk documented in the original research doc — treat any such input as untrusted, never as an instruction source.
- If the project ever grows beyond your own pre-wired command list into "agent reads screen and decides what to click," that is the point where a permission-tiering system becomes mandatory, not optional: read-only/local actions can run silently, anything involving shell execution, sending messages, or filesystem changes beyond a known safe list should require explicit confirmation before executing.
- Local models and local STT/TTS mean nothing leaves your machine for those steps — but the Groq API call does leave your machine. Don't send anything to that call you wouldn't want logged by a third party.

## 7. Build Order (v0 scope, in sequence)

1. **Intent router** — plain Python, regex/keyword table for your top 5–10 real daily commands (open youtube+search, open an app, tell time, play music, send a message via one specific app). No dependencies beyond Python stdlib. Build and test this in isolation first, with typed-in text, before wiring any audio.
2. **Actions layer** — wire each matched intent to a real action (`webbrowser.open`, `subprocess.Popen`, `pywinauto` for app control). Test each one manually.
3. **STT** — add faster-whisper, feed its output into the router you already tested.
4. **TTS** — add Kokoro, speak the router's canned responses.
5. **Wake word** — add openWakeWord so you're not manually triggering recording.
6. **Groq fallback** — wire the "no match" branch to a streaming Groq API call, chunk-and-speak as described in section 4.
7. **Logging** — log every fallback-triggering utterance to a file for the manual router-improvement loop in section 5.

Each step should produce something runnable before moving to the next. If a step isn't working, that's the actual signal to stop and fix — not a reason to research a different architecture.

## 8. Known Trade-offs, Stated Plainly

- Free-tier rate limits (Groq: 30 RPM / 14,400 RPD) are fine for one personal user, not for anything with multiple users or heavy automated polling.
- Anything not in your router's pattern list and not answerable by a text-only LLM (e.g., "click the third icon on my screen") is out of scope for this build — that requires the vision-agent approach this doc deliberately avoids, for the reasons in section 2.
- This is not private end-to-end: STT/TTS/routing/actions are local, but the Groq fallback call sends that utterance's text to Groq's servers. If that's disqualifying, the fallback branch would need to route to a small local model instead, at a real cost in capability — not addressed further here since it wasn't the stated priority.
