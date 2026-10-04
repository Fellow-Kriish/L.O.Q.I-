import ctypes
import json
import os
import threading
from pathlib import Path


class Preferences:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'LOQI' / 'desktop.json'
        self.lock = threading.Lock()
        try:
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            self.values = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            self.values = {}

    def save(self, values):
        with self.lock:
            self.values.update(values)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.values), encoding='utf-8')
                temporary.replace(self.path)
            except OSError:
                pass


def clamp_position(position, areas, width=360, height=120):
    if not areas:
        return (24, 24)
    valid = isinstance(position, (list, tuple)) and len(position) == 2 and all(isinstance(value, int) and not isinstance(value, bool) for value in position)
    if not valid:
        left, top, right, bottom = areas[0]
        position = (right - width - 24, bottom - height - 24)
    horizontal, vertical = position
    area = min(areas, key=lambda bounds: max(bounds[0] - horizontal, 0, horizontal - bounds[2]) ** 2 + max(bounds[1] - vertical, 0, vertical - bounds[3]) ** 2)
    left, top, right, bottom = area
    return (max(left, min(horizontal, max(left, right - width))), max(top, min(vertical, max(top, bottom - height))))


def display_scale(window=None):
    try:
        if window is not None and window.native:
            from ctypes import wintypes
            get_dpi = ctypes.windll.user32.GetDpiForWindow
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
            return max(1, get_dpi(window.native.Handle.ToInt64()) / 96)
        return max(1, ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100)
    except (AttributeError, OSError):
        return 1


def work_areas():
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT), ('work', wintypes.RECT), ('flags', wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    areas = []

    def collect(handle, device, rectangle, context):
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            bounds = info.work
            item = (bounds.left, bounds.top, bounds.right, bounds.bottom)
            if info.flags & 1:
                areas.insert(0, item)
            else:
                areas.append(item)
        return True

    user32.EnumDisplayMonitors(None, None, callback_type(collect), 0)
    return areas


def parse_shortcut(value):
    parts = [part.strip().upper() for part in value.split('+')]
    modifiers = {'CTRL': 2, 'ALT': 1, 'SHIFT': 4, 'WIN': 8}
    if len(parts) < 2 or any(part not in modifiers for part in parts[:-1]):
        raise ValueError('Use Ctrl, Alt, Shift, or Win plus a supported key.')
    mask = 0
    for part in parts[:-1]:
        mask |= modifiers[part]
    if not mask & 11:
        raise ValueError('Global shortcuts need Ctrl, Alt, or Win.')
    key = parts[-1]
    if key == 'SPACE':
        code = 32
    elif len(key) == 1 and key.isascii() and key.isalnum():
        code = ord(key)
    elif key.startswith('F') and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        code = 111 + int(key[1:])
    else:
        raise ValueError('Use Space, a letter, a digit, or F1–F24.')
    return mask | 0x4000, code


class GlobalShortcuts:
    def __init__(self, runtime, open_window):
        self.runtime = runtime
        self.open_window = open_window
        self.thread_id = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='loqi-shortcuts', daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread:
            self.thread.join(timeout=2)

    def run(self):
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        registered = {}
        errors = []
        settings = {'toggle': os.environ.get('LOQI_SHORTCUT_TOGGLE', 'Ctrl+Alt+Space'), 'open': os.environ.get('LOQI_SHORTCUT_OPEN', 'Ctrl+Alt+L')}
        try:
            for identity, name in enumerate(settings, start=1):
                try:
                    modifiers, key = parse_shortcut(settings[name])
                    if not user32.RegisterHotKey(None, identity, modifiers, key):
                        raise ValueError('Shortcut is already in use.')
                    registered[identity] = name
                except ValueError as error:
                    errors.append(f'{name}: {error}')
                    settings[name] = ''
            self.runtime.update(shortcuts=settings, shortcutError=' '.join(errors))
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message != 0x0312:
                    continue
                action = registered.get(message.wParam)
                if action == 'open':
                    self.open_window()
                elif action == 'toggle' and self.runtime.snapshot()['online']:
                    self.runtime.command({'action': 'resume' if self.runtime.paused.is_set() else 'pause'})
        finally:
            for identity in registered:
                user32.UnregisterHotKey(None, identity)
