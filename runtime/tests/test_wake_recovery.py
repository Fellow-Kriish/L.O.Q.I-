import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ui_runtime import RuntimeState


class WakeRecoveryTests(unittest.TestCase):
    def test_failed_device_open_recovers_without_restarting_assistant(self):
        attempts = []
        terminated = []
        listener: Any = None

        class Stream:
            def read(self, *args, **kwargs):
                listener._stop_event.set()
                return b'\0\0'

            def stop_stream(self):
                pass

            def close(self):
                pass

        class Audio:
            def open(self, **kwargs):
                attempts.append(kwargs)
                if len(attempts) == 1:
                    raise OSError(-9996, 'Device disconnected')
                return Stream()

            def terminate(self):
                terminated.append(True)

        class Model:
            def __init__(self, **kwargs):
                pass

            def predict(self, chunk):
                return {}

        modules = {
            'numpy': SimpleNamespace(int16=object(), frombuffer=lambda *args, **kwargs: []),
            'pyaudio': SimpleNamespace(PyAudio=Audio, paInt16=8),
            'openwakeword': SimpleNamespace(),
            'openwakeword.model': SimpleNamespace(Model=Model),
            'config': SimpleNamespace(WAKE_WORD_MODEL='hey_loki.onnx', WAKE_WORD_THRESHOLD=.5, AUDIO_SAMPLE_RATE=16000, WAKE_WORD_CHUNK_SAMPLES=1280),
            'audio_devices': SimpleNamespace(resolve_mic_index=lambda *args, **kwargs: None, publish_mic_name=lambda *args: None),
        }
        source = Path(__file__).resolve().parents[2] / 'wakeword.py'
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('wakeword_audit', source)
            self.assertIsNotNone(spec)
            assert spec is not None
            module = importlib.util.module_from_spec(spec)
            self.assertIsNotNone(spec.loader)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            setattr(module, 'runtime', RuntimeState())
            listener = module.WakeWordListener()
            with patch.object(listener._stop_event, 'wait', return_value=False):
                listener._listen_loop()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(terminated), 2)
        runtime_state = getattr(module, 'runtime')
        self.assertEqual(runtime_state.snapshot()['state'], 'idle')
        self.assertTrue(runtime_state.snapshot()['microphoneReady'])
        self.assertEqual(runtime_state.snapshot()['errorCode'], '')
