"""
LOQI — Actions Layer

One function per action category. Each returns (success, response_text), where
response_text is what TTS will speak.

All actions are deterministic — no vision model, no LLM, just direct OS calls.

An action that cannot be fulfilled at all raises ActionUnavailable instead of
returning a failure sentence. The two are genuinely different outcomes:
"couldn't close Chrome, it isn't running" is an answer the user asked for, but
"I don't know how to open Photoshop" is a dead end where the LLM would have
done better. See ActionUnavailable.
"""

import os
import re
import subprocess
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from datetime import date, datetime

import timers
from app_registry import is_process_running, lookup, resolve, resolve_process
from logging_setup import get_logger
from timers import extract_timer_args
from weather import current_report

log = get_logger(__name__)

# Grace period between asking an app to close and forcing it: long enough for
# the app to flush state, short enough that "close chrome" doesn't feel slow.
_GRACEFUL_CLOSE_WAIT_S = 2.0
_GRACEFUL_CLOSE_POLL_S = 0.25
_TASKKILL_TIMEOUT_S = 5.0

# A URI/protocol launcher (ms-settings:, whatsapp:, mswindowsmusic:) vs a file
# path. Requires 2+ scheme chars before ':', so a Windows drive like "C:\..."
# never matches (single letter + ':').
_URI_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]+:")


class ActionUnavailable(Exception):
    """
    The router matched, but this action cannot do the job.

    Raised when the utterance names something the action layer has no handle on
    — an app that isn't installed, a target that didn't resolve. The
    orchestrator catches this and falls through to the LLM, which can give a
    useful answer ("I don't see Photoshop installed") where the action layer
    could only apologise.

    Not for failures: an action that tried and failed returns (False, reason),
    because the reason is worth speaking.
    """


def _is_protocol(cmd: str) -> bool:
    """True if ``cmd`` is a URI/protocol handler rather than an executable path."""
    return _URI_SCHEME.match(cmd) is not None


def tell_name(**kwargs) -> tuple[bool, str]:
    """Introduce the assistant."""
    return True, "I'm L.O.Q.I., which stands for Local Operations and Query Interface. I'm your personal voice assistant."


def tell_time(**kwargs) -> tuple[bool, str]:
    """Return current time as a spoken string."""
    now = datetime.now()
    time_str = now.strftime("%I:%M %p").lstrip("0")
    return True, f"It's {time_str}."


def tell_date(**kwargs) -> tuple[bool, str]:
    """Return current date as a spoken string."""
    today = date.today()
    date_str = today.strftime("%A, %B %d, %Y")
    return True, f"Today is {date_str}."


def open_youtube(**kwargs) -> tuple[bool, str]:
    """Open YouTube in the default browser."""
    webbrowser.open("https://www.youtube.com")
    return True, "Opening YouTube."


def open_google(**kwargs) -> tuple[bool, str]:
    """Open Google in the default browser."""
    webbrowser.open("https://www.google.com")
    return True, "Opening Google."


def search_youtube(query: str = "", **kwargs) -> tuple[bool, str]:
    """Search YouTube for the given query."""
    if not query:
        return False, "I didn't catch what to search for."
    encoded = urllib.parse.quote_plus(query)
    webbrowser.open(f"https://www.youtube.com/results?search_query={encoded}")
    return True, f"Searching YouTube for {query}."


def search_google(query: str = "", **kwargs) -> tuple[bool, str]:
    """Search Google for the given query."""
    if not query:
        return False, "I didn't catch what to search for."
    encoded = urllib.parse.quote_plus(query)
    webbrowser.open(f"https://www.google.com/search?q={encoded}")
    return True, f"Searching Google for {query}."


def open_website(url: str = "", **kwargs) -> tuple[bool, str]:
    """Open a website by URL."""
    if not url:
        return False, "I didn't catch the website."
    webbrowser.open(url)
    # Extract domain for the spoken response
    domain = url.replace("https://", "").replace("http://", "").split("/")[0]
    return True, f"Opening {domain}."


def open_app(app_name: str = "", **kwargs) -> tuple[bool, str]:
    """
    Open an application by spoken name.

    Raises:
        ActionUnavailable: the name did not resolve to an app we know how to
            launch. Falling through to the LLM beats a dead-end apology —
            "open spotify and play music" and "open photoshop" both land here,
            and both have better answers than this layer can give.
    """
    if not app_name:
        raise ActionUnavailable("no app name was captured")

    match = resolve(app_name)
    if match is None:
        raise ActionUnavailable(f"no installed app matches {app_name!r}")

    # A fuzzy match resolved to a name the user did not say. Speak the resolved
    # name, not theirs: hearing "Opening Antigravity" after saying "anti
    # gravity" is confirmation, and hearing the wrong name is the fastest
    # correction loop there is.
    spoken = app_name if match.exact else match.name

    try:
        if _is_protocol(match.command):
            # Protocol handler like "ms-settings:" or "whatsapp:"
            os.startfile(match.command)  # type: ignore[attr-defined, unused-ignore]
        else:
            subprocess.Popen(match.command, shell=False)
        return True, f"Opening {spoken}."
    except FileNotFoundError:
        return False, f"Couldn't find {spoken}. The path in the app registry might be wrong."
    except Exception as e:
        return False, f"Failed to open {spoken}: {e}"


def close_app(app_name: str = "", **kwargs) -> tuple[bool, str]:
    """
    Close an application by spoken name.

    Resolves against running processes, so "it isn't running" is a real answer
    rather than a guess. Tier 2: main.py has already spoken the action back and
    heard a yes before this runs.

    Graceful first: taskkill without /F posts WM_CLOSE, letting the app save
    its state and run its own prompts. Force (/F) only if it is still alive
    after the grace period — the confirm gate approved closing the app, not
    discarding whatever it was holding.
    """
    if not app_name:
        return False, "I didn't catch which app to close."

    match = resolve_process(app_name)
    if match is None:
        return False, f"{app_name} doesn't look like it's running."

    stem = match.image[:-4] if match.image.lower().endswith(".exe") else match.image
    spoken = app_name if match.exact else stem

    try:
        # No /F: a close request, not a termination. Windowless processes
        # refuse it outright (rc 1); the liveness check below decides what
        # happens next, not this return code.
        subprocess.run(
            ["taskkill", "/IM", match.image],
            capture_output=True,
            text=True,
            timeout=_TASKKILL_TIMEOUT_S,
        )
    except Exception as e:
        return False, f"Failed to close {spoken}: {e}"

    deadline = time.monotonic() + _GRACEFUL_CLOSE_WAIT_S
    while True:
        if not is_process_running(match.image):
            return True, f"Closed {spoken}."
        if time.monotonic() >= deadline:
            break
        time.sleep(_GRACEFUL_CLOSE_POLL_S)

    try:
        result = subprocess.run(
            ["taskkill", "/IM", match.image, "/F"],
            capture_output=True,
            text=True,
            timeout=_TASKKILL_TIMEOUT_S,
        )
    except Exception as e:
        return False, f"Failed to close {spoken}: {e}"

    if result.returncode == 0:
        return True, f"Closed {spoken}."
    # It was running a moment ago when resolve_process() saw it, so this is a
    # race (it exited on its own) or a permissions refusal.
    return False, f"Couldn't close {spoken}. It may have already exited."


def get_weather(place: str = "", **kwargs) -> tuple[bool, str]:
    """
    Current conditions plus today's outlook, via Open-Meteo (keyless).

    Tier 0: read-only, nothing executes. A network failure returns (False,
    reason) rather than raising ActionUnavailable — the LLM fallback has no
    live weather data, so falling through to it would trade an honest
    "I couldn't reach the weather service" for a confident guess.
    """
    return current_report(place)


def set_timer(query: str = "", **kwargs) -> tuple[bool, str]:
    """
    Set a countdown timer from the raw timer-shaped utterance.

    ``query`` arrives as the whole matched utterance; timers.extract_timer_args
    pulls the duration and optional label out of it.

    Raises:
        ActionUnavailable: no duration was spoken — "set a timer" with no
            length is a request this layer can't fill, and the LLM fallback
            will at least ask how long.
    """
    seconds, label = extract_timer_args(query)
    if seconds is None:
        raise ActionUnavailable("no duration in the request")
    return timers.start(seconds, label)


def cancel_timer(query: str = "", **kwargs) -> tuple[bool, str]:
    """Cancel timers by scope: all of them, by name, or by duration."""
    words = set(query.lower().split())
    return timers.cancel(query, all_timers="all" in words or "everything" in words)


def timer_status(**kwargs) -> tuple[bool, str]:
    """How much time is left on the running timers."""
    return timers.status()


def play_music(**kwargs) -> tuple[bool, str]:
    """Open Spotify or the default music player."""
    cmd = lookup("spotify")
    if cmd:
        try:
            subprocess.Popen(cmd, shell=False)
            return True, "Opening Spotify."
        except Exception:
            pass

    # Fallback: try opening the Windows default music app
    try:
        os.startfile("mswindowsmusic:")  # type: ignore[attr-defined, unused-ignore]
        return True, "Opening your music app."
    except Exception:
        return False, "I couldn't find a music app to open."


# ---------------------------------------------------------------------------
# Action dispatcher — maps handler names to functions
# ---------------------------------------------------------------------------
ACTION_MAP: dict[str, Callable[..., tuple[bool, str]]] = {
    "tell_name": tell_name,
    "tell_time": tell_time,
    "tell_date": tell_date,
    "open_youtube": open_youtube,
    "open_google": open_google,
    "search_youtube": search_youtube,
    "search_google": search_google,
    "open_website": open_website,
    "weather": get_weather,
    "set_timer": set_timer,
    "cancel_timer": cancel_timer,
    "timer_status": timer_status,
    "open_app": open_app,
    "close_app": close_app,
    "play_music": play_music,
}


def execute(handler_name: str, **kwargs) -> tuple[bool, str]:
    """
    Execute an action by handler name with the given args.

    Returns:
        (success, response_text) — response_text is spoken either way.

    Raises:
        ActionUnavailable: the action cannot do the job and the caller should
            fall through to the LLM. Also raised for an unknown handler, which
            is a wiring bug: it is logged at error level, but the user still
            gets a useful answer instead of "Unknown action: foo".
    """
    action_fn = ACTION_MAP.get(handler_name)
    if not action_fn:
        log.error("Intent is wired to handler %r, which is not in ACTION_MAP.", handler_name)
        raise ActionUnavailable(f"unknown handler {handler_name!r}")
    return action_fn(**kwargs)
