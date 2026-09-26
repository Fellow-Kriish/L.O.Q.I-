"""
LOQI — Audio device resolution.

Both the recorder and the wake-word listener need to pick the same microphone.
A hardcoded device index breaks the moment a USB mic is plugged in, a headset
is removed, or the project is run on another machine — Windows renumbers
devices freely. This module resolves the mic once, in priority order:

1. ``LOQI_MIC_DEVICE_INDEX`` / ``config.MIC_DEVICE_INDEX`` — explicit pin, if it
   is still a valid input device (an invalid pin is reported and ignored).
2. ``LOQI_MIC_DEVICE_NAME`` / ``config.MIC_DEVICE_NAME`` — case-insensitive
   substring match against device names, so "Blue Yeti" survives renumbering.
3. ``None`` — let PortAudio use the system default input.

The result is cached: the wake-word listener reopens its stream on every
pause/resume cycle and must not re-enumerate devices each time.
"""

from __future__ import annotations

import config
from logging_setup import get_logger

log = get_logger(__name__)

_cached: int | None = None
_resolved = False


def _input_devices(pa) -> list[tuple[int, str]]:
    """Return [(index, name)] for every device that can capture audio."""
    devices = []
    for i in range(pa.get_device_count()):
        try:
            info = pa.get_device_info_by_index(i)
        except OSError:
            continue
        if int(info.get("maxInputChannels", 0)) > 0:
            devices.append((i, str(info.get("name", f"device {i}"))))
    return devices


def resolve_mic_index(pa, refresh: bool = False) -> int | None:
    """
    Resolve the microphone index to open, or None for the system default.

    Args:
        pa: an open ``pyaudio.PyAudio`` instance (device list is host-API state).
        refresh: re-resolve even if a previous call cached a result.
    """
    global _cached, _resolved
    if _resolved and not refresh:
        return _cached

    _cached = _resolve(pa)
    _resolved = True
    return _cached


def _resolve(pa) -> int | None:
    devices = _input_devices(pa)
    if not devices:
        log.warning("  ⚠️  No input devices found; using the system default.")
        return None

    valid = {idx for idx, _ in devices}

    # 1. Explicit index pin.
    pinned = config.MIC_DEVICE_INDEX
    if pinned is not None:
        if pinned in valid:
            log.info("  🎙️  Mic: index %d (pinned).", pinned)
            return pinned
        log.warning(
            "  ⚠️  Pinned mic index %d is not a valid input device "
            "(valid: %s). Ignoring the pin.",
            pinned, sorted(valid),
        )

    # 2. Name substring match.
    wanted = (config.MIC_DEVICE_NAME or "").strip().lower()
    if wanted:
        for idx, name in devices:
            if wanted in name.lower():
                log.info("  🎙️  Mic: index %d (%s) matched name %r.", idx, name, wanted)
                return idx
        log.warning(
            "  ⚠️  No input device name contains %r; using the system default.", wanted
        )

    # 3. System default.
    try:
        default = pa.get_default_input_device_info()
        log.info("  🎙️  Mic: system default (%s).", default.get("name", "?"))
    except OSError:
        log.warning("  ⚠️  No default input device reported by the host API.")
    return None


def describe_devices(pa) -> str:
    """Human-readable list of input devices — for troubleshooting/setup."""
    lines = ["Input devices:"]
    for idx, name in _input_devices(pa):
        lines.append(f"  [{idx:>2}] {name}")
    return "\n".join(lines)
