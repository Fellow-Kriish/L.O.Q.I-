"""
LOQI — Actions Layer

One function per action category. Each returns (success, response_text).
The response_text is what TTS will speak.

All actions are deterministic — no vision model, no LLM, just direct OS calls.
"""

import os
import re
import subprocess
import urllib.parse
import webbrowser
from collections.abc import Callable
from datetime import date, datetime

from app_registry import lookup

# A URI/protocol launcher (ms-settings:, whatsapp:, mswindowsmusic:) vs a file
# path. Requires 2+ scheme chars before ':', so a Windows drive like "C:\..."
# never matches (single letter + ':').
_URI_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]+:")


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
    """Open an application by friendly name."""
    if not app_name:
        return False, "I didn't catch which app to open."

    cmd = lookup(app_name)
    if not cmd:
        return False, f"I don't know how to open {app_name}. You can add it to the app registry."

    try:
        if cmd.startswith("explorer.exe "):
            subprocess.Popen(cmd, shell=False)
        elif _is_protocol(cmd):
            # Protocol handler like "ms-settings:" or "whatsapp:"
            os.startfile(cmd)
        else:
            subprocess.Popen(cmd, shell=False)
        return True, f"Opening {app_name}."
    except FileNotFoundError:
        return False, f"Couldn't find {app_name}. The path in the app registry might be wrong."
    except Exception as e:
        return False, f"Failed to open {app_name}: {e}"


def close_app(app_name: str = "", **kwargs) -> tuple[bool, str]:
    """Close an application by name using taskkill."""
    if not app_name:
        return False, "I didn't catch which app to close."

    # Map friendly names to process names
    process_map = {
        "chrome": "chrome.exe",
        "firefox": "firefox.exe",
        "edge": "msedge.exe",
        "notepad": "notepad.exe",
        "calculator": "Calculator.exe",
        "spotify": "Spotify.exe",
        "discord": "Discord.exe",
        "vlc": "vlc.exe",
        "vs code": "Code.exe",
        "vscode": "Code.exe",
        "visual studio code": "Code.exe",
        "steam": "steam.exe",
        "word": "WINWORD.EXE",
        "excel": "EXCEL.EXE",
        "powerpoint": "POWERPNT.EXE",
        "file explorer": "explorer.exe",
        "task manager": "Taskmgr.exe",
    }

    process_name = process_map.get(app_name.lower(), f"{app_name}.exe")

    try:
        result = subprocess.run(
            ["taskkill", "/IM", process_name, "/F"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return True, f"Closed {app_name}."
        else:
            return False, f"Couldn't close {app_name}. It might not be running."
    except Exception as e:
        return False, f"Failed to close {app_name}: {e}"


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
        os.startfile("mswindowsmusic:")
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
    "open_app": open_app,
    "close_app": close_app,
    "play_music": play_music,
}


def execute(handler_name: str, **kwargs) -> tuple[bool, str]:
    """
    Execute an action by handler name with the given args.
    Returns (success, response_text).
    """
    action_fn = ACTION_MAP.get(handler_name)
    if not action_fn:
        return False, f"Unknown action: {handler_name}"
    return action_fn(**kwargs)
