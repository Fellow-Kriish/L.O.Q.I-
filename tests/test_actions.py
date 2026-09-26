"""
close_app regression tests: graceful close before force kill.

The confirm gate approves the intent (close this app), not the damage: an
instant ``taskkill /F`` throws away unsaved work without ever offering the app
its own chance to save. These tests pin the escalation order — WM_CLOSE first,
``/F`` only once the grace period has expired on a process that ignored it.

No Windows needed: taskkill and the liveness probe are monkeypatched, and the
grace period is zeroed so the escalation path runs instantly.
"""

import subprocess

import pytest

import actions
from app_registry import ProcessMatch


class _Recorder:
    """subprocess.run stand-in that records every taskkill invocation."""

    def __init__(self, returncode: int = 0):
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(
            args=argv, returncode=self.returncode, stdout="", stderr=""
        )


@pytest.fixture
def running_chrome(monkeypatch):
    """Pretend chrome.exe resolved as an exact, running match."""
    monkeypatch.setattr(
        actions, "resolve_process",
        lambda name: ProcessMatch(
            query="chrome", image="chrome.exe", score=100.0, source="exact"
        ),
    )


def test_graceful_close_wins_when_the_app_exits(monkeypatch, running_chrome):
    """The app honours WM_CLOSE → /F never fires."""
    recorder = _Recorder()
    monkeypatch.setattr(actions.subprocess, "run", recorder)
    monkeypatch.setattr(actions, "is_process_running", lambda image: False)

    ok, _text = actions.close_app("chrome")

    assert ok
    assert recorder.calls == [["taskkill", "/IM", "chrome.exe"]]


def test_force_kill_only_after_the_grace_period(monkeypatch, running_chrome):
    """The app ignores WM_CLOSE → exactly one /F, and only after the grace wait."""
    recorder = _Recorder()
    monkeypatch.setattr(actions.subprocess, "run", recorder)
    monkeypatch.setattr(actions, "is_process_running", lambda image: True)
    monkeypatch.setattr(actions, "_GRACEFUL_CLOSE_WAIT_S", 0.0)

    ok, _text = actions.close_app("chrome")

    assert ok
    assert recorder.calls == [
        ["taskkill", "/IM", "chrome.exe"],
        ["taskkill", "/IM", "chrome.exe", "/F"],
    ]


def test_force_kill_failure_is_reported(monkeypatch, running_chrome):
    """Both attempts refuse → the user hears a failure, not false success."""
    recorder = _Recorder(returncode=1)
    monkeypatch.setattr(actions.subprocess, "run", recorder)
    monkeypatch.setattr(actions, "is_process_running", lambda image: True)
    monkeypatch.setattr(actions, "_GRACEFUL_CLOSE_WAIT_S", 0.0)

    ok, text = actions.close_app("chrome")

    assert not ok
    assert "Couldn't close" in text


def test_unresolvable_app_is_not_running(monkeypatch):
    """resolve_process → None must reach the user as a spoken answer, not a kill."""
    kills = _Recorder()
    monkeypatch.setattr(actions.subprocess, "run", kills)
    monkeypatch.setattr(actions, "resolve_process", lambda name: None)

    ok, text = actions.close_app("photoshop")

    assert not ok
    assert "running" in text
    assert kills.calls == []
