"""
LOQI — App Registry

Mapping of friendly app names → executable paths / commands.
Extend this dict with your own apps. Names are matched case-insensitively.

If a value is just a bare name (e.g. "notepad.exe"), it's assumed to be
on the system PATH. Otherwise, provide the full absolute path.
"""

# fmt: off
APPS: dict[str, str] = {
    # --- Windows built-ins ---
    "notepad":          "notepad.exe",
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
# fmt: on


def lookup(name: str) -> str | None:
    """
    Look up an app by friendly name. Case-insensitive.
    Returns the command/path string, or None if not found.
    """
    return APPS.get(name.lower().strip())


def list_apps() -> list[str]:
    """Return sorted list of known app names."""
    return sorted(set(APPS.keys()))
