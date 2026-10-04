import asyncio
import functools
import http.server
import os
import secrets
import sys
import threading
from pathlib import Path
from urllib.parse import urlencode

from bridge import runtime, serve, start_voice, stop_voice
from desktop_state import GlobalShortcuts, Preferences, clamp_position, display_scale, work_areas


def main():
    if sys.platform != 'win32':
        raise SystemExit('The native host requires Windows. Use bridge.py for browser development.')
    import pystray
    import webview
    from PIL import Image, ImageDraw

    root = Path(__file__).resolve().parents[1]
    if not (root / 'dist' / 'index.html').is_file():
        raise SystemExit('Build the UI first: npm run build')

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(root / 'dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    token = secrets.token_urlsafe(32)
    port = 8765
    runtime_url = f'ws://127.0.0.1:{port}/?token={token}'
    bridge_ready = threading.Event()
    bridge_failure = []

    def start_bridge():
        try:
            asyncio.run(serve(port, token, [origin], bridge_ready))
        except Exception as error:
            bridge_failure.append(error)
            bridge_ready.set()

    threading.Thread(target=start_bridge, daemon=True).start()
    bridge_ready.wait(5)
    if bridge_failure or not bridge_ready.is_set():
        server.shutdown()
        raise SystemExit('Cannot start the local bridge. Close other L.O.Q.I. instances and try again.')

    quitting = threading.Event()
    widget_hidden = threading.Event()
    windows = {}
    position_lock = threading.Lock()
    position_timer = None
    preferences = Preferences()
    runtime.save_preferences = preferences.save
    runtime.update(**{key: preferences.values[key] for key in ('soundCues', 'showRequest') if isinstance(preferences.values.get(key), bool)})

    class NativeApi:
        def __init__(self):
            self.widget_height = 150

        def open_fullscreen(self):
            windows['full'].show()
            windows['full'].restore()

        def show_widget(self):
            widget_hidden.clear()
            self.resize_widget(self.widget_height)
            keep_on_screen()
            windows['widget'].show()

        def hide_widget(self):
            widget_hidden.set()
            windows['widget'].hide()

        def resize_widget(self, height):
            if height in (84, 150, 300):
                self.widget_height = height
                if widget_hidden.is_set():
                    return
                scale = display_scale(windows['widget'])
                windows['widget'].resize(round(360 * scale), round(height * scale))
                keep_on_screen()

        def open_sound_settings(self):
            os.startfile('ms-settings:sound')

    api = NativeApi()
    full_url = origin + '/#' + urlencode({'runtime': runtime_url, 'surface': 'full'})
    widget_url = origin + '/#' + urlencode({'runtime': runtime_url, 'surface': 'widget'})
    windows['full'] = webview.create_window('L.O.Q.I.', full_url, js_api=api, width=1100, height=820, min_size=(420, 600), background_color='#F6F7F2')

    scale = display_scale()
    position = clamp_position(preferences.values.get('position'), work_areas(), round(360 * scale), round(120 * scale))
    windows['widget'] = webview.create_window('L.O.Q.I. voice widget', widget_url, js_api=api, width=360, height=120, min_size=(280, 64), x=round(position[0] / scale), y=round(position[1] / scale), frameless=True, easy_drag=False, on_top=True, focus=False, resizable=False, transparent=True, background_color='#F6F7F2')

    def keep_on_screen():
        widget = windows['widget']
        current = (widget.x, widget.y)
        restored = clamp_position(current, work_areas(), widget.width, widget.height)
        if restored != current:
            position_scale = getattr(widget.native, 'scale_factor', display_scale())
            widget.move(*(round(value / position_scale) for value in restored))

    def remember_position(horizontal, vertical):
        nonlocal position_timer
        with position_lock:
            if position_timer:
                position_timer.cancel()
            position_timer = threading.Timer(.25, preferences.save, args=({'position': [int(horizontal), int(vertical)]},))
            position_timer.daemon = True
            position_timer.start()

    def flush_position():
        with position_lock:
            if position_timer:
                position_timer.cancel()
            preferences.save({'position': [windows['widget'].x, windows['widget'].y]})

    windows['widget'].events.moved += remember_position
    shortcuts = GlobalShortcuts(runtime, api.open_fullscreen)

    def watch_displays():
        previous = work_areas()
        while not quitting.wait(1):
            areas = work_areas()
            if areas != previous and not widget_hidden.is_set():
                keep_on_screen()
                previous = areas

    def close_full():
        if not quitting.is_set():
            windows['full'].hide()
            return False

    def close_widget():
        if not quitting.is_set():
            widget_hidden.set()
            windows['widget'].hide()
            return False

    windows['full'].events.closing += close_full
    windows['widget'].events.closing += close_widget
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.arc((8, 8, 56, 56), 35, 315, fill='#19B7A5', width=7)
    draw.arc((20, 20, 44, 44), 215, 495, fill='#2879D8', width=6)

    def exit_app(icon, item):
        flush_position()
        quitting.set()
        shortcuts.stop()
        stop_voice()
        icon.stop()
        for window in windows.values():
            window.destroy()
        server.shutdown()

    tray = pystray.Icon('loqi', image, 'L.O.Q.I.', menu=pystray.Menu(
        pystray.MenuItem('Open L.O.Q.I.', lambda icon, item: api.open_fullscreen(), default=True),
        pystray.MenuItem('Restore voice widget', lambda icon, item: api.show_widget()),
        pystray.MenuItem('Hide voice widget', lambda icon, item: api.hide_widget()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit L.O.Q.I.', exit_app),
    ))

    def started():
        shortcuts.start()
        threading.Thread(target=watch_displays, daemon=True).start()
        start_voice()
        threading.Thread(target=tray.run, daemon=True).start()

    try:
        webview.start(started, gui='edgechromium')
    finally:
        if not quitting.is_set():
            flush_position()
        quitting.set()
        shortcuts.stop()
        stop_voice()
        tray.stop()
        server.shutdown()


if __name__ == '__main__':
    main()
