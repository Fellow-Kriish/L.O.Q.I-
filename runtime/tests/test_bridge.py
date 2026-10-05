import asyncio
import json
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
from ui_runtime import RuntimeState
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError, InvalidStatus


class StateTests(unittest.TestCase):
    def test_pause_during_spoken_confirmation_fails_closed(self):
        state = RuntimeState()
        state.clients = 1
        approved = state.confirm_close('Chrome', lambda text: state.command({'action': 'pause'}))
        self.assertFalse(approved)
        self.assertIsNone(state.snapshot()['confirmation'])

    def test_feedback_expires_without_history(self):
        state = RuntimeState()
        state.feedback('Opened YouTube')
        self.assertEqual(state.snapshot()['feedback']['text'], 'Opened YouTube')
        deadline = state.snapshot()['feedback']['expiresAt'] / 1000
        with patch('ui_runtime.time.time', return_value=deadline + 1):
            self.assertIsNone(state.snapshot()['feedback'])
        self.assertNotIn('history', state.snapshot())

    def test_preferences_validate_and_save(self):
        state = RuntimeState()
        saved: list[dict[str, bool]] = []
        state.save_preferences = saved.append
        state.update(soundCuesAvailable=True)
        state.command({'action': 'preferences', 'soundCues': True, 'showRequest': False, 'unknown': True})
        self.assertTrue(state.snapshot()['soundCues'])
        self.assertFalse(state.snapshot()['showRequest'])
        self.assertEqual(saved, [{'soundCues': True, 'showRequest': False}])
        state.command({'action': 'preferences', 'showRequest': 'false'})
        self.assertFalse(state.snapshot()['showRequest'])

    def test_cues_are_opt_in_and_distinct(self):
        state = RuntimeState()
        state.update(soundCuesAvailable=True)
        from types import SimpleNamespace
        calls = []
        with patch.dict(sys.modules, {'winsound': SimpleNamespace(Beep=lambda frequency, duration: calls.append((frequency, duration)))}):
            state.cue('start')
            self.assertEqual(calls, [])
            state.update(soundCues=True)
            state.cue('start')
            state.cue('end')
        self.assertEqual(calls, [(880, 70), (540, 70)])

    def test_microphone_errors_are_actionable(self):
        state = RuntimeState()
        state.microphone_error(OSError(-9996, 'Invalid device'))
        self.assertEqual(state.snapshot()['errorCode'], 'mic_disconnected')
        state.microphone_error(OSError(-9985, 'Device unavailable'))
        self.assertEqual(state.snapshot()['errorCode'], 'mic_busy')
        state.microphone_error(OSError('Unknown audio failure'))
        self.assertEqual(state.snapshot()['errorCode'], 'mic_unavailable')
        self.assertFalse(state.snapshot()['microphoneReady'])

    def test_cloud_error_does_not_disable_local_runtime(self):
        state = RuntimeState()
        state.update(online=True, state='idle', microphoneReady=True)
        state.update(cloudError='Cloud unavailable')
        self.assertTrue(state.snapshot()['online'])
        self.assertTrue(state.snapshot()['microphoneReady'])

    def test_idle_and_paused_are_still_and_clear_request(self):
        state = RuntimeState()
        state.update(state='listening')
        state.audio(bytes([0, 32]) * 128, pcm=True)
        self.assertGreater(state.snapshot()['audioLevel'], 0)
        state.update(state='idle', route='cloud', request='private text')
        self.assertEqual(state.snapshot()['audioLevel'], 0)
        self.assertIsNone(state.snapshot()['route'])
        self.assertEqual(state.snapshot()['request'], '')
        state.command({'action': 'pause'})
        self.assertTrue(state.paused.is_set())
        self.assertEqual(state.snapshot()['state'], 'paused')

    def test_confirmation_rejects_wrong_id_and_replay(self):
        state = RuntimeState()
        state.clients = 1
        result = []
        worker = threading.Thread(target=lambda: result.append(state.confirm_close('Chrome', lambda text: None)))
        worker.start()
        for attempt in range(100):
            pending = state.snapshot()['confirmation']
            if pending:
                break
            time.sleep(.01)
        self.assertIsNotNone(pending)
        self.assertEqual(pending['title'], 'Close all Chrome windows?')
        state.command({'action': 'confirm', 'id': 'wrong', 'approved': True})
        self.assertFalse(state.approval.is_set())
        state.command({'action': 'confirm', 'id': pending['id'], 'approved': False})
        worker.join(1)
        self.assertEqual(result, [False])
        state.command({'action': 'confirm', 'id': pending['id'], 'approved': True})
        self.assertFalse(state.answer)

    def test_approval_and_expiry(self):
        state = RuntimeState()
        state.clients = 1
        result = []
        worker = threading.Thread(target=lambda: result.append(state.confirm_close('Chrome', lambda text: None)))
        worker.start()
        for attempt in range(100):
            pending = state.snapshot()['confirmation']
            if pending:
                break
            time.sleep(.01)
        state.command({'action': 'confirm', 'id': pending['id'], 'approved': True})
        worker.join(1)
        self.assertEqual(result, [True])
        state.update(confirmation={'id': 'expired', 'expiresAt': 0})
        state.answer = False
        state.approval.clear()
        state.command({'action': 'confirm', 'id': 'expired', 'approved': True})
        self.assertFalse(state.approval.is_set())

    def test_no_client_fails_closed(self):
        state = RuntimeState()
        self.assertFalse(state.confirm_close('Chrome', lambda text: None))

    def test_resume_does_not_open_mic_mid_turn(self):
        state = RuntimeState()
        state.in_turn.set()
        state.command({'action': 'pause'})
        state.command({'action': 'resume'})
        self.assertTrue(state.interrupted.is_set())
        self.assertFalse(state.paused.is_set())


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bridge.runtime = RuntimeState()
        self.state = bridge.runtime
        with socket.socket() as temporary:
            temporary.bind(('127.0.0.1', 0))
            self.port = temporary.getsockname()[1]
        self.ready = threading.Event()
        self.server = asyncio.create_task(bridge.serve(self.port, 'test-secret', ['http://localhost:8443'], self.ready))
        for attempt in range(100):
            if self.ready.is_set():
                break
            await asyncio.sleep(.01)
        self.assertTrue(self.ready.is_set())
        self.url = f'ws://127.0.0.1:{self.port}/?token=test-secret'

    async def asyncTearDown(self):
        self.state.shutdown.set()
        await asyncio.wait_for(self.server, 2)

    async def test_authenticated_status_and_control(self):
        async with connect(self.url, origin='http://localhost:8443') as client:
            initial = json.loads(await client.recv())
            self.assertFalse(initial['online'])
            self.assertFalse(initial['microphoneReady'])
            self.state.update(online=True, microphoneReady=True, state='listening')
            update = json.loads(await asyncio.wait_for(client.recv(), 1))
            self.assertEqual(update['state'], 'listening')
            await client.send(json.dumps({'type': 'command', 'action': 'pause'}))
            paused = json.loads(await asyncio.wait_for(client.recv(), 1))
            self.assertEqual(paused['state'], 'paused')
            self.assertEqual(paused['audioLevel'], 0)

    async def test_wrong_token_rejected(self):
        async with connect(self.url.replace('test-secret', 'wrong'), origin='http://localhost:8443') as client:
            with self.assertRaises(ConnectionClosedError):
                await client.recv()
        self.assertEqual(self.state.clients, 0)

    async def test_untrusted_origin_rejected(self):
        with self.assertRaises(InvalidStatus):
            async with connect(self.url, origin='https://untrusted.example'):
                pass

    async def test_disconnect_cancels_pending_approval(self):
        async with connect(self.url, origin='http://localhost:8443') as client:
            await client.recv()
            self.state.update(confirmation={'id': 'pending', 'expiresAt': time.time() * 1000 + 20000})
        for attempt in range(100):
            if self.state.clients == 0:
                break
            await asyncio.sleep(.01)
        self.assertIsNone(self.state.snapshot()['confirmation'])
        self.assertFalse(self.state.answer)


if __name__ == '__main__':
    unittest.main()
