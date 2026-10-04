# L.O.Q.I. desktop runtime

The desktop host integrates directly with this repository's original root-level
Python modules; there is no second copy of the assistant. Its existing router,
actions, models, cloud fallback, interruption handling, and permission gates
remain in place. UI instrumentation was added to the recorder, wake listener,
playback, barge-in listener, and orchestrator. Close-app actions use explicit
UI confirmation instead of a second spoken yes/no capture while the bridge is
active. The original CLI retains its spoken/typed confirmation. Other approval tiers
retain the repository's original gate.

## Windows setup

Use Python 3.11 or 3.12, Node.js, and Microsoft Edge WebView2 Runtime.
Install espeak-ng on PATH as required by Kokoro. Model downloads require an
internet connection on first use. The original Python/audio dependencies must
be compatible with your machine; use the upstream GPU setup if applicable.

From the project root, in PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r runtime/requirements.txt
python -c "from openwakeword.utils import download_models; download_models(model_names=['hey_loki'])"
npm install
npm run build
python runtime/desktop.py
```

The download step installs openWakeWord's shared preprocessing/VAD model assets.
The custom `hey_loki.onnx` wake model itself is already included at the repo root.

The host starts the actual wake-word assistant, an authenticated loopback
WebSocket bridge, the main window, and a non-activating always-on-top widget.
Drag the widget's identity row to reposition it. Closing a window hides it;
use the notification-area menu to restore either surface or quit the assistant.
The widget does not appear above Windows secure desktops. Both windows consume
the same state, and only real captured/playback audio produces aperture movement.

Keep optional upstream settings in the root `.env` (ignored by Git).
`GROQ_API_KEY` is needed only for cloud fallback; never put it in a `VITE_`
variable. Raw microphone audio is not included in WebSocket messages or sent
to the cloud model. Transient request text clears on idle or pause; this UI does
not add a conversation archive. The original upstream logging behavior remains.

## Browser development

```powershell
python runtime/bridge.py
```

This prints a per-run authenticated runtime URL. Set `VITE_LOQI_RUNTIME_URL`
in the frontend's `.env.local` to that full URL, then restart Vite. Do not commit
the token. For a stable local development session, `LOQI_UI_TOKEN` can be set
in the Python process environment to a strong random secret.

`LOQI_UI_ORIGINS` is a comma-separated exact-origin allowlist; defaults are
`http://localhost:8443,http://127.0.0.1:8443`. The bridge binds only to
`127.0.0.1`. Hosted HTTPS previews cannot reliably connect to a Windows localhost
bridge because of browser mixed-content/local-network restrictions; use the
native host or a local browser build. A token is still required even with an
allowed origin. The native launcher creates and passes its token automatically.

The app defaults to real-runtime mode and never reports an online assistant
without runtime evidence. Add `?preview=1` to explicitly enter labeled design
preview mode; preview states are not microphone measurements.

## Everyday controls

- **Ctrl+Alt+Space:** pause/resume listening. The Windows host registers a global
  shortcut; the browser-only version works while the page is focused.
- **Ctrl+Alt+L:** open/restore the assistant. **Escape:** cancel an approval or
  stop speech while the full assistant page is focused.
- Set `LOQI_SHORTCUT_TOGGLE` and `LOQI_SHORTCUT_OPEN` before launching the native
  host to customize global shortcuts, for example `Ctrl+Shift+F8`. Supported
  modifiers are Ctrl, Alt, Shift, and Win; keys are letters, digits, Space, and
  F1–F24. A conflicting or invalid shortcut is reported, not silently claimed.
- **Listening sounds** is off by default. Enable it in the full-screen footer
  for distinct short capture-start and capture-end tones. These are generated
  by the Windows runtime once per capture, not by each connected UI.
- **Request text** toggles the current recognized request in both surfaces,
  including close-app approval. It never creates a transcript history.
- Native widget placement and the two preferences are saved in
  `%LOCALAPPDATA%\LOQI\desktop.json`. No request text or audio is saved there.
  Position is clamped to monitor work areas when restored, resized, or when
  display topology changes, keeping the widget above the taskbar.
- Actual microphone names appear when supplied by PortAudio. Device errors
  distinguish disconnection from a busy/blocked device when the audio error
  permits it. **Open sound settings** is available in the native full view.
- Action results/cancellations appear briefly, then clear. A cloud failure does
  not mark the local assistant offline or disable its local command router.

## Extended bridge contract

Snapshots are sent immediately on connection and whenever state changes:

```json
{"type":"status","online":true,"state":"listening","microphoneReady":true,"route":null,"audioLevel":0.24,"request":"","confirmation":null,"error":""}
```

States: `idle`, `listening`, `processing`, `speaking`, `paused`, `unavailable`.
Route is `local`, `cloud`, or null. Audio levels are normalized RMS measurements
from actual microphone chunks or the output callback, limited to ~16 Hz.
Idle, paused, processing, and unavailable states always have zero audio level.

Commands are `{"type":"command","action":"pause"}`, `resume`, or `stop`.
Close-app approvals include a unique `id`, title, consequence text, and absolute
`expiresAt` timestamp. Answer with
`{"type":"command","action":"confirm","id":"…","approved":false}`.
Approvals expire after 20 seconds, reject mismatched/replayed IDs, and cancel
if all UI clients disconnect, on pause/stop, or on shutdown. Escape cancels.
Actual actions run through the original local action layer after approval.

Optional snapshot fields include `microphoneName`, `errorCode`, `cloudError`,
`feedback` (unique ID, text, kind, absolute four-second expiry), `soundCues`,
`soundCuesAvailable`, `showRequest`, `shortcuts`, and `shortcutError`.
Change preferences with
`{"type":"command","action":"preferences","soundCues":true,"showRequest":false}`.
Only boolean preference values are accepted. Native preferences persist across
restarts; browser-only bridge preferences last for that bridge process.

## Verification

```powershell
python -m unittest discover -s runtime/tests -v
npx tsc --noEmit
npm run build
```

Bridge tests do not require Windows, microphone access, model downloads, or
cloud credentials. Native positioning, tray behavior, microphone capture,
playback, and Windows app closing must be exercised on Windows.

The original runtime regression suite remains in `tests/`. Install
`requirements-dev.txt` and run `python -m pytest tests` from the repo root.
