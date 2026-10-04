import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_state import Preferences, clamp_position, parse_shortcut


class DesktopStateTests(unittest.TestCase):
    def test_preferences_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'preferences.json'
            preferences = Preferences(path)
            preferences.save({'position': [400, 500], 'showRequest': False})
            preferences.save({'soundCues': True})
            self.assertEqual(Preferences(path).values, {'position': [400, 500], 'showRequest': False, 'soundCues': True})

    def test_corrupt_preferences_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'preferences.json'
            path.write_text('invalid json')
            self.assertEqual(Preferences(path).values, {})

    def test_visible_position_is_preserved(self):
        self.assertEqual(clamp_position([300, 400], [(0, 0, 1920, 1040)]), (300, 400))

    def test_removed_display_recovers(self):
        self.assertEqual(clamp_position([2400, 900], [(0, 0, 1920, 1040)]), (1560, 900))

    def test_secondary_display_and_taskbar(self):
        areas = [(0, 0, 1920, 1040), (-1280, 0, 0, 984)]
        self.assertEqual(clamp_position([-1200, 950], areas, height=150), (-1200, 834))

    def test_invalid_position_and_small_display(self):
        self.assertEqual(clamp_position('invalid', [(0, 0, 1920, 1040)]), (1536, 896))
        self.assertEqual(clamp_position([0, 0], [(0, 0, 320, 200)], height=300), (0, 0))

    def test_shortcut_parsing(self):
        self.assertEqual(parse_shortcut('Ctrl+Alt+Space'), (0x4003, 32))
        self.assertEqual(parse_shortcut('Alt+Shift+F8'), (0x4005, 119))
        self.assertEqual(parse_shortcut('Win+L'), (0x4008, 76))
        for invalid in ('L', 'Shift+L', 'Ctrl+Unknown', 'Ctrl+F25', 'Bogus+L'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_shortcut(invalid)
