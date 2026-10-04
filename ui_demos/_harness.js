/* ==========================================================================
   Shared demo harness — IDENTICAL across all three UI demos.

   Owns the state table (spec §6), the verified palette (§6), the sample
   content (§5), the simulated amplitude signal, reduced-motion handling and
   the state controls. Each demo supplies only its own render — so all three
   animate from the very same signal and show the very same words, and the
   only variable between the three files is the design itself.

   Deliberately a classic script, not an ES module: these files are opened
   straight off disk, and file:// blocks module imports.
   ========================================================================== */

(function (global) {
  "use strict";

  /* Contrast-verified against the #14161A pill surface, per spec §6. Not a
     palette choice — a constraint all three demos inherit. */
  const PALETTE = {
    gray:   "#8A93A3",  // 5.85:1
    blue:   "#5B9DFF",  // 6.65:1
    violet: "#B197FC",  // 7.51:1
    teal:   "#5EEAD4",  // 12.24:1
    green:  "#4ADE80",  // 10.39:1
    amber:  "#FFC24D",  // 11.28:1
    red:    "#FF6B6B",  // 6.53:1
    text:   "#F2F4F8",  // 16.45:1
    surface: "#14161A",
  };

  /* Spec §6. `shape` exists because §6 forbids meaning carried by colour
     alone: every state is also a distinct silhouette and a word. */
  const STATES = {
    idle:       { color: "gray",   shape: "dash",   motion: "none",  word: "Idle",       label: "" },
    listening:  { color: "blue",   shape: "ring",   motion: "mic",   word: "Listening",  label: "open spotify" },
    thinking:   { color: "violet", shape: "hollow", motion: "pulse", word: "Thinking",   label: "draft a note to Rahul" },
    acting:     { color: "teal",   shape: "play",   motion: "step",  word: "Acting",     label: "Opening Spotify" },
    confirming: { color: "amber",  shape: "square", motion: "none",  word: "Confirming", label: "Send this message?" },
    speaking:   { color: "green",  shape: "bars",   motion: "tts",   word: "Speaking",   label: "Here's the draft. I'll read it out." },
    error:      { color: "red",    shape: "cross",  motion: "none",  word: "Failed",     label: "Groq isn't answering." },
    muted:      { color: "gray",   shape: "muted",  motion: "none",  word: "Mic off",    label: "" },
  };

  /* Spec G1 — every turn says whether it left the machine. */
  const ROUTE = {
    idle: null, muted: null, listening: null,
    thinking: "cloud", speaking: "cloud", error: "cloud",
    acting: "local", confirming: "local",
  };

  /* Spec G2 — tier drives the confirm gate, so it is never hidden. */
  const TIER = {
    idle: null, muted: null, listening: null, thinking: null,
    acting: 1, confirming: 2, speaking: 0, error: 0,
  };

  const ORDER = ["idle", "listening", "thinking", "acting", "confirming", "speaking", "error", "muted"];

  /* Spec §5.2 — the card states exactly what will happen, to whom, in the
     words that will actually be sent. */
  const CONFIRM = {
    action: "Send message",
    app: "WhatsApp",
    to: "Rahul",
    body: "Running 10 minutes late, start without me",
    tier: 2,
    reversible: "No — can't be unsent",
    timeout: 10,
  };

  /* Spec §5.3 Timeline. Mixed routes, all four tiers, one cancel — so the
     route badge, the tier chip and the failure copy all have real work. */
  const TIMELINE = [
    { time: "14:22:07", text: "what time is it",             route: "local", tier: 0, result: "It's 2:22 PM.",              ms: 310,  ok: true },
    { time: "14:23:41", text: "open spotify",                route: "local", tier: 1, result: "Opened Spotify.",            ms: 486,  ok: true },
    { time: "14:24:58", text: "set a timer for 10 minutes",   route: "local", tier: 1, result: "Timer set for 10 minutes.",  ms: 402,  ok: true },
    { time: "14:26:12", text: "text Rahul I'm running late",  route: "local", tier: 2, result: "Sent to Rahul.",             ms: 5240, ok: true },
    { time: "14:28:03", text: "why is my laptop fan loud",    route: "cloud", tier: 0, result: "Spoke 3 sentences.",         ms: 2870, ok: true },
    { time: "14:31:20", text: "delete the build folder",      route: "local", tier: 3, result: "Cancelled at the confirm.",  ms: 9100, ok: false },
  ];

  /* Spec G4 — the per-stage breakdown. The sentence-gap bug in the TTS
     pipeline was invisible without exactly these four numbers, so all three
     demos surface them somewhere. */
  const TIMING = [
    { stage: "wake",    ms: 120 },
    { stage: "record",  ms: 1740 },
    { stage: "stt",     ms: 240 },
    { stage: "route",   ms: 2 },
    { stage: "llm",     ms: 610 },
    { stage: "speak",   ms: 840 },
  ];

  const reduceMotion = global.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ------------------------------------------------------------------------
     Amplitude engine

     Stands in for real mic level and real TTS output level — the two signals
     spec §6 requires the bars to be driven by. Shape matters: flat random
     jitter reads instantly as a fake, so `mic` is speech bursts under a slow
     envelope and `tts` is syllabic pulses under a phrase-length arc. Shared,
     so no demo gets a flattering signal the others didn't.
     ------------------------------------------------------------------------ */
  function createSignal(historyLength) {
    const history = new Float32Array(historyLength || 180);
    let value = 0, envelope = 0, phase = 0, burst = 0, elapsed = 0;

    function step(motion, dt) {
      elapsed += dt;
      phase += dt;

      if (motion === "mic") {
        burst -= dt;
        if (burst <= 0) {                       // a new syllable cluster
          burst = 0.22 + Math.random() * 0.42;
          envelope = 0.28 + Math.random() * 0.72;
        }
        const jitter = 0.55 + Math.random() * 0.45;
        value += (envelope * jitter - value) * Math.min(1, dt * 17);
      } else if (motion === "tts") {
        const syllable = Math.abs(Math.sin(phase * Math.PI * 5.1));
        const arc = 0.55 + 0.45 * Math.sin(phase * 0.72);
        value += (syllable * arc * 0.92 - value) * Math.min(1, dt * 21);
      } else if (motion === "pulse") {
        value = 0.12 + 0.09 * (0.5 + 0.5 * Math.sin(phase * 1.9));
      } else if (motion === "step") {
        value = 0.22;
      } else {
        value += (0 - value) * Math.min(1, dt * 9);
      }

      value = value < 0 ? 0 : value > 1 ? 1 : value;
      history.copyWithin(0, 1);
      history[history.length - 1] = value;
      return value;
    }

    /* Reduced motion still needs a characteristic shape, just a still one. */
    function freeze(motion) {
      for (let i = 0; i < history.length; i++) {
        const x = i / history.length;
        if (motion === "mic") {
          history[i] = 0.22 + 0.52 * Math.abs(Math.sin(x * 16)) * (0.5 + 0.5 * Math.sin(x * 3.4));
        } else if (motion === "tts") {
          history[i] = 0.30 + 0.44 * Math.abs(Math.sin(x * 27));
        } else if (motion === "pulse") {
          history[i] = 0.16;
        } else {
          history[i] = 0;
        }
      }
      value = (motion === "mic" || motion === "tts") ? 0.56 : motion === "pulse" ? 0.16 : 0;
      phase = 0.42;
    }

    return {
      history, step, freeze,
      get value() { return value; },
      get elapsed() { return elapsed; },
      resetClock() { elapsed = 0; },
    };
  }

  /* ------------------------------------------------------------------------
     Controls, keyboard and the frame loop
     ------------------------------------------------------------------------ */
  function mount(opts) {
    const bar = document.querySelector(".controls");
    const live = document.querySelector("[aria-live]");
    const signal = createSignal();
    let current = opts.initial || "listening";
    let motionOn = !reduceMotion;

    const buttons = ORDER.map(function (name) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.state = name;
      b.textContent = STATES[name].word;
      b.setAttribute("aria-pressed", String(name === current));
      b.addEventListener("click", function () { select(name); });
      bar.appendChild(b);
      return b;
    });

    const spacer = document.createElement("span");
    spacer.className = "spacer";
    bar.appendChild(spacer);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "toggle";
    toggle.setAttribute("aria-pressed", "false");
    toggle.textContent = "Motion on";
    toggle.addEventListener("click", function () { setMotion(!motionOn); });
    bar.appendChild(toggle);

    function setMotion(on) {
      motionOn = on;
      toggle.textContent = on ? "Motion on" : "Motion off";
      toggle.setAttribute("aria-pressed", String(!on));
      document.body.dataset.motion = on ? "on" : "off";
      if (!on) signal.freeze(STATES[current].motion);
    }

    function select(name) {
      if (!STATES[name]) return;
      current = name;
      signal.resetClock();
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed", String(b.dataset.state === name));
      });
      if (!motionOn) signal.freeze(STATES[name].motion);
      if (live) live.textContent = STATES[name].word + (STATES[name].label ? ". " + STATES[name].label : "");
      opts.onState(name, STATES[name]);
    }

    setMotion(motionOn);
    select(current);

    let last = performance.now();
    requestAnimationFrame(function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (motionOn) signal.step(STATES[current].motion, dt);
      opts.onFrame(signal, current, STATES[current], motionOn);
      requestAnimationFrame(frame);
    });

    /* Spec §5.1: Esc stops, Enter confirms. Both are global, because the
       overlay never takes keyboard focus from the window behind it. */
    global.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && current !== "idle") {
        e.preventDefault();
        if (opts.onStop) opts.onStop(current);
        select("idle");
      } else if (e.key === "Enter" && current === "confirming") {
        e.preventDefault();
        if (opts.onConfirm) opts.onConfirm();
      }
    });

    return {
      select: select,
      setMotion: setMotion,
      get state() { return current; },
      get motionOn() { return motionOn; },
    };
  }

  /* Real countdown, so the 10-second auto-cancel in §5.2 is demonstrated
     rather than described. */
  function countdown(seconds, onTick, onExpire) {
    const t0 = performance.now();
    let raf = requestAnimationFrame(function loop(now) {
      const left = seconds - (now - t0) / 1000;
      if (left <= 0) { onTick(0); onExpire(); return; }
      onTick(left);
      raf = requestAnimationFrame(loop);
    });
    return function cancel() { cancelAnimationFrame(raf); };
  }

  /* Canvas sizing that survives DPI changes — spec §11 asks for 100/150/200%. */
  function fitCanvas(canvas) {
    const dpr = global.devicePixelRatio || 1;
    const r = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width * dpr));
    const h = Math.max(1, Math.round(r.height * dpr));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    return { ctx: canvas.getContext("2d"), w: w, h: h, dpr: dpr };
  }

  /* The window behind the overlay. Same content in all three, so legibility
     over a real working window is judged on equal terms. */
  function fauxCode() {
    const pre = document.querySelector(".behind pre");
    if (!pre) return;
    pre.innerHTML =
      '<b>def</b> speak_pipelined(self, sentence_iter, stop_event=<b>None</b>):\n' +
      '    audio_q = queue.Queue(maxsize=3)\n' +
      '\n' +
      '    <b>def</b> _synth_worker():\n' +
      '        <b>for</b> sentence <b>in</b> sentence_iter:\n' +
      '            <b>if</b> stop_event.is_set():\n' +
      '                <b>break</b>\n' +
      '            audio_q.put((sentence, self._synth(sentence)))\n';
  }

  global.Loki = {
    PALETTE: PALETTE, STATES: STATES, ROUTE: ROUTE, TIER: TIER, ORDER: ORDER,
    CONFIRM: CONFIRM, TIMELINE: TIMELINE, TIMING: TIMING,
    reduceMotion: reduceMotion,
    createSignal: createSignal, mount: mount, countdown: countdown,
    fitCanvas: fitCanvas, fauxCode: fauxCode,
  };
})(window);
