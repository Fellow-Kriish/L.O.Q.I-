import array
import json
import math
import secrets
import sys
import threading
import time


class RuntimeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.paused = threading.Event()
        self.in_turn = threading.Event()
        self.interrupted = threading.Event()
        self.shutdown = threading.Event()
        self.changed = threading.Event()
        self.approval = threading.Event()
        self.answer = False
        self.clients = 0
        self.ui_enabled = False
        self.wake_listener = None
        self.tts = None
        self.stop_event = None
        self.last_audio = 0.0
        self.save_preferences = None
        self.data = dict(type='status', state='unavailable', online=False,
                         microphoneReady=False, route=None, audioLevel=0,
                         request='', confirmation=None, error='Starting the voice runtime.',
                         microphoneName='', errorCode='', cloudError='', feedback=None,
                         soundCues=False, soundCuesAvailable=sys.platform == 'win32', showRequest=True,
                         shortcuts={'toggle': 'Ctrl+Alt+Space', 'open': 'Ctrl+Alt+L'}, shortcutError='')

    def update(self, **values):
        with self.lock:
            self.data.update(values)
            if self.data['state'] not in ('listening', 'speaking'):
                self.data['audioLevel'] = 0
            if self.data['state'] in ('idle', 'paused', 'unavailable'):
                self.data.update(route=None, request='')
            self.changed.set()

    def snapshot(self):
        with self.lock:
            feedback = self.data['feedback']
            if isinstance(feedback, dict) and feedback.get('expiresAt', 0) <= time.time() * 1000:
                self.data['feedback'] = None
            return json.loads(json.dumps(self.data))

    def feedback(self, text, kind='success'):
        self.update(feedback={'id': secrets.token_urlsafe(12), 'text': str(text)[:180],
                              'kind': kind, 'expiresAt': int((time.time() + 4) * 1000)})

    def microphone_error(self, error):
        code = next((value for value in getattr(error, 'args', ()) if isinstance(value, int)), None)
        description = str(error).lower()
        if code == -9996 or 'no default input' in description or 'disconnected' in description:
            title, detail, key = 'Microphone disconnected', 'Reconnect your microphone and check Windows sound settings.', 'mic_disconnected'
        elif code == -9985 or 'in use' in description:
            title, detail, key = 'Microphone busy or blocked', 'Close other microphone apps and check Windows microphone permissions.', 'mic_busy'
        else:
            title, detail, key = 'Microphone unavailable', 'Check your input device in Windows sound settings.', 'mic_unavailable'
        self.update(state='unavailable', microphoneReady=False, error=detail, errorCode=key)
        return title

    def cue(self, boundary):
        if not self.data['soundCues'] or not self.data['soundCuesAvailable']:
            return
        try:
            import winsound

            beep = getattr(winsound, 'Beep', None)
            if callable(beep):
                beep(880 if boundary == 'start' else 540, 70)
            else:
                raise RuntimeError('winsound.Beep unavailable')
        except (ImportError, RuntimeError):
            self.update(soundCuesAvailable=False, soundCues=False)

    def audio(self, samples, pcm=False):
        now = time.monotonic()
        if now - self.last_audio < 0.06:
            return
        self.last_audio = now
        values = array.array('h', samples) if pcm else samples
        count = len(values)
        rms = math.sqrt(sum(float(value) ** 2 for value in values) / count) if count else 0
        self.update(audioLevel=min(1, rms * (6 / 32768 if pcm else 6)))

    def cancel_confirmation(self):
        with self.lock:
            self.answer = False
            self.data['confirmation'] = None
            self.approval.set()
            self.changed.set()

    def command(self, message):
        action = message.get('action')
        if action == 'preferences':
            values = {key: message[key] for key in ('soundCues', 'showRequest') if isinstance(message.get(key), bool)}
            if not self.data['soundCuesAvailable']:
                values.pop('soundCues', None)
            self.update(**values)
            if self.save_preferences:
                self.save_preferences(values)
            return
        if action == 'confirm':
            with self.lock:
                pending = self.data['confirmation']
                if (
                    isinstance(pending, dict)
                    and pending.get('expiresAt', 0) > time.time() * 1000
                    and message.get('id') == pending.get('id')
                    and isinstance(message.get('approved'), bool)
                ):
                    self.answer = bool(message['approved'])
                    self.data['confirmation'] = None
                    self.approval.set()
                    self.changed.set()
            return
        if action not in ('pause', 'resume', 'stop'):
            return
        if action in ('pause', 'stop'):
            self.interrupted.set()
            self.cancel_confirmation()
            if self.stop_event:
                self.stop_event.set()
            if self.tts:
                self.tts.stop()
        if action == 'pause':
            self.paused.set()
            if self.wake_listener:
                self.wake_listener.pause()
            self.update(state='paused', microphoneReady=False)
        elif action == 'resume' and self.paused.is_set():
            self.paused.clear()
            if self.tts and not self.in_turn.is_set():
                self.tts.reset()
            if self.wake_listener and not self.in_turn.is_set():
                self.wake_listener.resume()
        elif action == 'stop':
            self.update(audioLevel=0)

    def confirm_close(self, app_name, speak):
        self.approval.clear()
        self.answer = False
        if not self.clients:
            return False
        app_name = str(app_name).strip()[:100]
        speak(f'Close all {app_name} windows? Please confirm in L.O.Q.I., or cancel.')
        with self.lock:
            if not self.clients or self.paused.is_set() or self.interrupted.is_set() or self.shutdown.is_set():
                return False
            self.update(state='processing', confirmation={
                'id': secrets.token_urlsafe(18), 'title': f'Close all {app_name} windows?',
                'detail': 'This closes every window belonging to the app. Windows asking you to save will be left open.',
                'expiresAt': int((time.time() + 20) * 1000),
            })
        self.approval.wait(20)
        with self.lock:
            answer = self.answer
            self.data['confirmation'] = None
            self.changed.set()
        return answer and not self.paused.is_set() and not self.shutdown.is_set() and not self.interrupted.is_set()


runtime = RuntimeState()
