"""
LOQI — App Registry

Maps a spoken app name onto a real OS object, in both directions:

  * resolve()          — name → a command that launches it (for open_app)
  * resolve_process()  — name → a running process image (for close_app)

Both share one acceptance policy, deliberately: they are the two places where a
fuzzy string match decides what the machine does, and the policy is the safety
property. Keeping them in one module keeps it to one policy.

resolve() consults three tiers, in descending order of trust:

  1. alias      — the hand-written APPS table. Always wins: it is the only place
                  a spoken shorthand ("cmd", "vs code") can be pinned to an
                  exact target, and it covers things Get-StartApps handles
                  badly (protocol URIs, portable installs).
  2. installed  — Windows Start-menu entries, discovered once in the background
                  via Get-StartApps. Exact name match.
  3. fuzzy      — scored match against the installed names, to absorb STT noise
                  and spacing drift ("anti gravity" → "Antigravity").

The fuzzy tier is deliberately strict. It is the only tier that can resolve to
an app the user did not name, and open_app is Tier 1 — it runs with no
confirmation — so a loose match silently launches the wrong program. Scores
measured against this machine's real 182-entry Start menu:

    "anti gravity"  → Antigravity          95.7    want: accept
    "antigravity"   → Antigravity         100.0    want: accept
    "visual studio" → Visual Studio Code   83.9    want: reject — which one?
    "photoshop"     → Photos               80.0    want: reject — not installed
    "spotify"       → Photos               46.2    want: reject

_FUZZY_MIN_SCORE sits above the highest wrong answer and below the lowest right
one. Near-ties between two candidates are refused as well (see _best_match).

Refusing returns None, which is a real answer, not a failure: the caller falls
through to the LLM, which can say "I don't see Photoshop installed" — strictly
better than opening Photos and calling it success.

resolve_process() mirrors those tiers against *running* processes rather than
installed ones, because whether an app can be closed depends on live state.
"""

import csv
import io
import json
import subprocess
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process

from logging_setup import get_logger

log = get_logger(__name__)

# Hand-written overrides: spoken shorthands, protocol launchers, and portable
# installs that the Start menu either misses or names unhelpfully.
APPS: dict[str, str] = {
    # --- Windows built-ins ---
    "calculator":       "calc.exe",
    "calc":             "calc.exe",
    "file explorer":    "explorer.exe",
    "explorer":         "explorer.exe",
    "task manager":     "taskmgr.exe",
    "command prompt":   "cmd.exe",
    "cmd":              "cmd.exe",
    "powershell":       "powershell.exe",
    "settings":         "ms-settings:",
    "paint":            "mspaint.exe",
    "snipping tool":    "snippingtool.exe",

    # --- Browsers ---
    "chrome":           r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "firefox":          r"C:\Program Files\Mozilla Firefox\firefox.exe",
    "edge":             "msedge.exe",

    # --- Dev tools ---
    "vs code":          "code",
    "vscode":           "code",
    "visual studio code": "code",
    "terminal":         "wt.exe",
    "windows terminal": "wt.exe",

    # --- Communication ---
    "discord":          r"C:\Users\Lenovo\AppData\Local\Discord\Update.exe --processStart Discord.exe",
    "whatsapp":         "whatsapp:",

    # --- Media ---
    "spotify":          r"C:\Users\Lenovo\AppData\Roaming\Spotify\Spotify.exe",
    "vlc":              r"C:\Program Files\VideoLAN\VLC\vlc.exe",

    # --- Productivity ---
    "word":             "winword.exe",
    "excel":            "excel.exe",
    "powerpoint":       "powerpnt.exe",

    # --- Gaming ---
    "steam":            r"C:\Program Files (x86)\Steam\steam.exe",
}

MatchSource = Literal["alias", "exact", "fuzzy"]
"""How a name was resolved: pinned by hand, matched exactly, or scored."""


@dataclass(frozen=True, slots=True)
class AppMatch:
    """A resolved app: what to launch, and how sure we are about it."""

    query: str          # what the user asked for, normalized
    name: str           # canonical name, original casing — safe to speak back
    command: str        # command line or protocol URI to launch
    score: float        # 0-100 similarity; 100.0 for alias and exact hits
    source: MatchSource

    @property
    def exact(self) -> bool:
        """True when the user named this app precisely, so no need to echo it back."""
        return self.source != "fuzzy"


@dataclass(frozen=True, slots=True)
class ProcessMatch:
    """A running process resolved from a spoken app name."""

    query: str          # what the user asked for, normalized
    image: str          # process image name, original casing — what taskkill needs
    score: float        # 0-100 similarity; 100.0 for alias and exact hits
    source: MatchSource

    @property
    def exact(self) -> bool:
        """True when the user named this process precisely."""
        return self.source != "fuzzy"


@dataclass(frozen=True, slots=True)
class _Installed:
    """One Start-menu entry."""

    name: str           # display name, original casing
    app_id: str         # Start-menu AppID, for shell:AppsFolder


# ---------------------------------------------------------------------------
# Start-menu discovery
# ---------------------------------------------------------------------------
# Rebound exactly once, by the loader thread, immediately before _LOADED is set.
_INSTALLED: dict[str, _Installed] = {}
_LOADED = threading.Event()

_LOAD_TIMEOUT_S = 15.0      # PowerShell cold start on a loaded machine
_LOOKUP_WAIT_S = 2.0        # how long one lookup will block on a cold registry

# Windows-only flag; absent (and ignored) elsewhere, which keeps this importable
# on the Linux CI runner.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _launch_command(app_id: str) -> str:
    """Build the shell:AppsFolder launcher for a Start-menu AppID."""
    return f"explorer.exe shell:AppsFolder\\{app_id}"


def _query_start_menu() -> dict[str, _Installed]:
    """Ask Windows for its Start-menu app list, keyed by lowercased name."""
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-NonInteractive",
            "-Command", "Get-StartApps | ConvertTo-Json",
        ],
        capture_output=True,
        text=True,
        timeout=_LOAD_TIMEOUT_S,
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        log.debug(
            "Get-StartApps exited %d: %s",
            result.returncode, result.stderr.strip()[:200],
        )
        return {}

    payload = json.loads(result.stdout)
    # ConvertTo-Json emits a bare object, not an array, when there is one app.
    if isinstance(payload, dict):
        payload = [payload]

    installed: dict[str, _Installed] = {}
    for entry in payload:
        name = str(entry.get("Name") or "").strip()
        app_id = str(entry.get("AppID") or "").strip()
        if name and app_id:
            # First writer wins, so duplicate display names resolve predictably.
            installed.setdefault(name.lower(), _Installed(name=name, app_id=app_id))
    return installed


def _load_installed() -> None:
    """
    Populate _INSTALLED once, off the startup path.

    Never raises and always sets _LOADED: a thread that died without signalling
    would make every later lookup pay the full _LOOKUP_WAIT_S for nothing.
    """
    global _INSTALLED

    installed: dict[str, _Installed] = {}
    try:
        installed = _query_start_menu()
        log.debug("App registry: %d installed apps discovered.", len(installed))
    except FileNotFoundError:
        log.debug("Get-StartApps unavailable (no powershell on this platform).")
    except subprocess.TimeoutExpired:
        log.warning(
            "Get-StartApps timed out after %.0fs — static aliases only.",
            _LOAD_TIMEOUT_S,
        )
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        log.warning("Could not parse the Start-menu app list (%s) — static aliases only.", e)
    except Exception:
        log.exception("Unexpected failure loading the Start-menu app list.")
    finally:
        # Rebind in one shot, then signal. A reader that beats the event sees
        # either the finished map or the empty one, never a half-filled dict.
        _INSTALLED = installed
        _LOADED.set()


# Discovery costs ~1s of PowerShell, so start it at import and let it run while
# the (far slower) STT and TTS models load.
threading.Thread(target=_load_installed, name="app-registry-load", daemon=True).start()


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
# Above the highest observed wrong match (83.9) and below the lowest right one
# (95.7) — see the module docstring for the measurements.
_FUZZY_MIN_SCORE = 88.0

# Two candidates this close are the same answer as far as we can tell, so we
# cannot pick one on the user's behalf.
_FUZZY_TIE_MARGIN = 4.0


def _best_match(query: str, candidates: Iterable[str]) -> tuple[str, float] | None:
    """
    Pick the single best candidate for ``query``, or None when unsure.

    Pure and side-effect free, so the acceptance policy is testable without a
    Windows Start menu.

    ``fuzz.ratio`` — whole-string indel similarity — is the scorer on purpose.
    The partial and token scorers score a candidate that is a prefix of the
    query as a perfect hit, which is precisely the "photoshop" → "Photos"
    failure this function exists to prevent.
    """
    ranked = process.extract(query, list(candidates), scorer=fuzz.ratio, limit=2)
    if not ranked:
        return None

    name, score, _ = ranked[0]
    if score < _FUZZY_MIN_SCORE:
        log.debug(
            "Fuzzy miss: %r best candidate %r scored %.1f < %.1f",
            query, name, score, _FUZZY_MIN_SCORE,
        )
        return None

    if len(ranked) > 1:
        runner_up, runner_score, _ = ranked[1]
        if score - runner_score < _FUZZY_TIE_MARGIN:
            log.debug(
                "Fuzzy ambiguous: %r matches %r (%.1f) and %r (%.1f)",
                query, name, score, runner_up, runner_score,
            )
            return None

    return name, float(score)


def resolve(name: str) -> AppMatch | None:
    """
    Resolve a spoken app name to a launchable command.

    Returns None when nothing is confidently known. Callers must treat that as
    "I don't have this app" and fall through to the LLM — never as licence to
    launch a best guess.
    """
    query = " ".join(name.split()).lower()
    if not query:
        return None

    # 1. Hand-written aliases win outright.
    command = APPS.get(query)
    if command is not None:
        return AppMatch(query=query, name=query, command=command, score=100.0, source="alias")

    # A cold registry is worth a short wait; a hung one is not.
    _LOADED.wait(timeout=_LOOKUP_WAIT_S)
    installed = _INSTALLED

    # 2. Exact installed name.
    hit = installed.get(query)
    if hit is not None:
        return AppMatch(
            query=query,
            name=hit.name,
            command=_launch_command(hit.app_id),
            score=100.0,
            source="exact",
        )

    # 3. Scored match, strictly gated.
    if installed:
        match = _best_match(query, installed.keys())
        if match is not None:
            key, score = match
            hit = installed[key]
            log.info("App %r resolved to %r by fuzzy match (%.1f).", query, hit.name, score)
            return AppMatch(
                query=query,
                name=hit.name,
                command=_launch_command(hit.app_id),
                score=score,
                source="fuzzy",
            )

    return None


def lookup(name: str) -> str | None:
    """
    Resolve ``name`` to a launch command, discarding the match metadata.

    For callers that already know exactly what they are asking for and only
    need "how do I start this" (see actions.play_music). On the voice path
    prefer resolve(), where the caller should know whether the match was exact.
    """
    match = resolve(name)
    return match.command if match else None


# ---------------------------------------------------------------------------
# Running-process resolution — the close_app side
# ---------------------------------------------------------------------------
# Spoken name → process image, for the apps whose executable is not named
# anything like the app. Everything else resolves from the live process list.
_PROCESS_ALIASES: dict[str, str] = {
    "vs code":              "Code.exe",
    "vscode":               "Code.exe",
    "visual studio code":   "Code.exe",
    "edge":                 "msedge.exe",
    "microsoft edge":       "msedge.exe",
    "word":                 "WINWORD.EXE",
    "powerpoint":           "POWERPNT.EXE",
    "file explorer":        "explorer.exe",
    "task manager":         "Taskmgr.exe",
    "terminal":             "WindowsTerminal.exe",
    "windows terminal":     "WindowsTerminal.exe",
}

# Killing any of these takes the desktop or the OS down with it. They are
# removed from the candidate pool entirely, so no threshold or confirmation
# slip can ever aim taskkill at one.
_PROTECTED_PROCESSES = frozenset({
    "system", "system idle process", "registry", "memory compression",
    "csrss", "wininit", "winlogon", "services", "lsass", "smss",
    "svchost", "dwm", "fontdrvhost", "sihost", "ctfmon",
    "taskhostw", "runtimebroker", "shellexperiencehost",
    "searchhost", "startmenuexperiencehost", "textinputhost",
    "audiodg", "conhost", "wudfhost", "spoolsv",
})

_PROCESS_QUERY_TIMEOUT_S = 5.0


def _running_processes() -> dict[str, str]:
    """
    Snapshot the running processes as {lowercased stem: image name}.

    Keyed on the stem ("chrome") rather than the image ("chrome.exe") because
    that is what people say. Protected system processes are dropped here, at
    the source, so they never reach the matcher.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=_PROCESS_QUERY_TIMEOUT_S,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        log.debug("tasklist unavailable (not Windows).")
        return {}
    except subprocess.TimeoutExpired:
        log.warning("tasklist timed out after %.0fs.", _PROCESS_QUERY_TIMEOUT_S)
        return {}

    if result.returncode != 0:
        log.debug("tasklist exited %d: %s", result.returncode, result.stderr.strip()[:200])
        return {}

    running: dict[str, str] = {}
    for row in csv.reader(io.StringIO(result.stdout)):
        if not row or not row[0].strip():
            continue
        image = row[0].strip()
        stem = image[:-4] if image.lower().endswith(".exe") else image
        stem = stem.lower()
        if stem in _PROTECTED_PROCESSES:
            continue
        running.setdefault(stem, image)
    return running


def resolve_process(name: str) -> ProcessMatch | None:
    """
    Resolve a spoken app name to a running process image, for taskkill.

    Matched against what is actually *running*, not what is installed. That is
    the whole point: "close X" depends on live state, so matching live state is
    what lets close_app say "it isn't running" truthfully instead of firing
    taskkill at a name it guessed.

    Never cached — processes come and go between one command and the next.
    """
    query = " ".join(name.split()).lower()
    if not query:
        return None

    running = _running_processes()
    if not running:
        return None

    # 1. Hand-pinned image name. If that exact process is not up, the answer is
    #    "not running" — deliberately not a licence to go fuzzy-match something
    #    else and kill it instead.
    alias = _PROCESS_ALIASES.get(query)
    if alias is not None:
        stem = (alias[:-4] if alias.lower().endswith(".exe") else alias).lower()
        hit = running.get(stem)
        return (
            ProcessMatch(query=query, image=hit, score=100.0, source="alias")
            if hit is not None
            else None
        )

    # 2. Exact stem match.
    hit = running.get(query)
    if hit is not None:
        return ProcessMatch(query=query, image=hit, score=100.0, source="exact")

    # 3. Scored match, same strict gate as resolve().
    match = _best_match(query, running.keys())
    if match is not None:
        stem, score = match
        log.info("Process %r resolved to %r by fuzzy match (%.1f).", query, running[stem], score)
        return ProcessMatch(query=query, image=running[stem], score=score, source="fuzzy")

    return None


def is_process_running(image: str) -> bool:
    """
    True when ``image`` (e.g. "chrome.exe") is in the current process list.

    A targeted liveness re-check for close_app's graceful path: after asking an
    app to close, this is how we learn whether it actually did. Query failure
    reads as True on purpose — uncertainty must escalate the caller, never let
    it report a close that never happened.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=_PROCESS_QUERY_TIMEOUT_S,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        log.debug("tasklist unavailable (not Windows).")
        return True
    except subprocess.TimeoutExpired:
        log.warning("tasklist timed out after %.0fs.", _PROCESS_QUERY_TIMEOUT_S)
        return True

    if result.returncode != 0:
        return True
    # No-match output is the "INFO: No tasks are running..." line, whose single
    # CSV field is not the image — an exact first-field compare can't hit it.
    for row in csv.reader(io.StringIO(result.stdout)):
        if row and row[0].strip().lower() == image.lower():
            return True
    return False


def list_apps() -> list[str]:
    """Every name the registry resolves exactly: aliases plus installed apps."""
    _LOADED.wait(timeout=_LOOKUP_WAIT_S)
    return sorted(set(APPS) | {app.name for app in _INSTALLED.values()})
