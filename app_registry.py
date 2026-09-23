"""
LOQI — App Registry

Mapping of friendly app names → executable paths / commands.
Extends the dict dynamically by querying Windows StartApps.
"""

import difflib
import json
import subprocess
import threading

# Hardcoded overrides (for things like "settings", "cmd", etc that UWP/StartMenu don't handle easily,
# or custom portable apps).
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

_DYNAMIC_APPS: dict[str, str] = {}
_APPS_LOADED = threading.Event()

def _load_apps_bg():
    try:
        # Run Get-StartApps and parse JSON
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json"],
            capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        if result.returncode == 0:
            apps = json.loads(result.stdout)
            for app in apps:
                name = app.get("Name", "")
                appid = app.get("AppID", "")
                if name and appid:
                    _DYNAMIC_APPS[name.lower()] = appid
    except Exception as e:
        print(f"Warning: Failed to load dynamic apps list: {e}")
    finally:
        _APPS_LOADED.set()

# Start background load immediately when module is imported
threading.Thread(target=_load_apps_bg, daemon=True).start()

def lookup(name: str) -> str | None:
    """
    Look up an app by friendly name. Case-insensitive.
    Returns the command/path string, or None if not found.
    """
    name_lower = name.lower().strip()

    # 1. Check hardcoded static overrides first
    if name_lower in APPS:
        return APPS[name_lower]

    # 2. Ensure dynamic apps are loaded
    # Use a small timeout so we don't freeze indefinitely if powershell hangs
    _APPS_LOADED.wait(timeout=2.0)

    # 3. Check exact match in dynamic apps
    if name_lower in _DYNAMIC_APPS:
        return f'explorer.exe shell:AppsFolder\\{_DYNAMIC_APPS[name_lower]}'

    # 4. Fuzzy match if exact match fails
    if _DYNAMIC_APPS:
        matches = difflib.get_close_matches(name_lower, _DYNAMIC_APPS.keys(), n=1, cutoff=0.7)
        if matches:
            best_match = matches[0]
            return f'explorer.exe shell:AppsFolder\\{_DYNAMIC_APPS[best_match]}'

    return None

def list_apps() -> list[str]:
    """Return sorted list of known app names."""
    _APPS_LOADED.wait(timeout=2.0)
    all_apps = set(APPS.keys()) | set(_DYNAMIC_APPS.keys())
    return sorted(all_apps)
