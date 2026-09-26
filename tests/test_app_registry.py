"""
Name-resolution regression tests.

app_registry decides what gets launched and what gets killed on the strength of
a fuzzy string match, which makes its acceptance policy a safety property
rather than a convenience. open_app in particular is Tier 1 — no confirmation —
so a match that is merely plausible is a wrong program starting silently.

These tests pin that policy to the cases that actually went wrong on a real
182-app Start menu. No Windows needed: the matcher is pure, and both OS queries
are monkeypatched.
"""

import subprocess

import pytest

import app_registry
from app_registry import (
    _best_match,
    _Installed,
    _running_processes,
    is_process_running,
    resolve,
    resolve_process,
)

# A slice of a real Start menu, picked for the near-misses it contains:
# Photos is what "photoshop" used to launch, and the two Antigravity and two
# Visual Studio entries are the ambiguity the policy has to refuse.
_START_MENU = (
    "Photos",
    "Antigravity",
    "Antigravity IDE",
    "Visual Studio Code",
    "Visual Studio Installer",
    "Notepad",
    "XBOX",
    "Wispr Flow",
)


@pytest.fixture
def installed(monkeypatch):
    """Swap the discovered Start-menu map for a fixed slice of a real one."""
    apps = {
        name.lower(): _Installed(name=name, app_id=f"{name.replace(' ', '')}.AppID")
        for name in _START_MENU
    }
    monkeypatch.setattr(app_registry, "_INSTALLED", apps)
    app_registry._LOADED.set()  # don't block on the real background loader
    return apps


def _fake_run(stdout: str, returncode: int = 0):
    """A subprocess.run stand-in returning canned output."""
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=""
        )
    return run


# ---------------------------------------------------------------------------
# The acceptance policy, in isolation
# ---------------------------------------------------------------------------

def test_photoshop_does_not_match_photos():
    """
    The original bug, as a unit test.

    "open photoshop" launched Microsoft Photos: difflib scored them 0.8, the
    cutoff was 0.7, and Tier 1 meant nobody was asked. Photos is a prefix of
    photoshop, so any partial or token scorer rates this a perfect hit — which
    is why _best_match uses whole-string similarity.
    """
    assert _best_match("photoshop", ["photos", "notepad", "xbox"]) is None


def test_accepts_a_clear_winner():
    """Spacing drift is what the fuzzy tier exists for: 95.7 on the real menu."""
    match = _best_match("anti gravity", ["antigravity", "notepad"])
    assert match is not None
    name, score = match
    assert name == "antigravity"
    assert score >= 95.0


def test_refuses_a_near_tie():
    """
    Two candidates we cannot choose between is a refusal, not a coin flip.

    Both score identically here, so the winner carries no information — and
    picking one anyway is how you kill the wrong process.
    """
    assert _best_match("spotify musi", ["spotify music", "spotify musik"]) is None


def test_refuses_an_ambiguous_product_family():
    """'visual studio' names two different products; neither is the answer."""
    assert _best_match(
        "visual studio", ["visual studio code", "visual studio installer"]
    ) is None


def test_refuses_an_app_that_is_not_there():
    assert _best_match("zomato", list(_START_MENU)) is None


def test_no_candidates_is_a_refusal_not_a_crash():
    assert _best_match("anything", []) is None


# ---------------------------------------------------------------------------
# resolve() — launch resolution and tier precedence
# ---------------------------------------------------------------------------

def test_aliases_win_over_installed_apps(installed):
    """
    The alias table is the only place a spoken shorthand can be pinned, so it
    has to outrank discovery. "vs code" must reach the `code` CLI, not the
    Start-menu entry that happens to share the name.
    """
    match = resolve("vs code")
    assert match is not None
    assert match.source == "alias"
    assert match.command == "code"
    assert match.exact


def test_exact_installed_name_resolves(installed):
    match = resolve("antigravity")
    assert match is not None
    assert match.source == "exact"
    assert match.name == "Antigravity"
    assert match.score == 100.0
    assert match.exact


def test_fuzzy_hit_reports_the_canonical_name(installed):
    """
    open_app speaks match.name when the hit is fuzzy, so the user hears which
    app actually started. That only works if the canonical casing survives.
    """
    match = resolve("anti gravity")
    assert match is not None
    assert match.source == "fuzzy"
    assert match.name == "Antigravity"   # not the user's "anti gravity"
    assert not match.exact


@pytest.mark.parametrize("query", ["photoshop", "visual studio", "zomato", "illustrator"])
def test_unknown_apps_resolve_to_none(installed, query):
    """
    None is the useful answer: actions.open_app turns it into
    ActionUnavailable, and the LLM says "I don't see Photoshop installed"
    instead of Photos opening and the assistant reporting success.
    """
    assert resolve(query) is None


@pytest.mark.parametrize("query", ["", "   ", "\t"])
def test_blank_queries_resolve_to_none(installed, query):
    assert resolve(query) is None


def test_whitespace_is_normalized(installed):
    match = resolve("  ANTI   gravity  ")
    assert match is not None
    assert match.name == "Antigravity"


# ---------------------------------------------------------------------------
# resolve_process() — close resolution
# ---------------------------------------------------------------------------

_TASKLIST_CSV = (
    '"chrome.exe","1234","Console","1","250,000 K"\n'
    '"svchost.exe","900","Services","0","12,000 K"\n'
    '"System","4","Services","0","100 K"\n'
    '"lsass.exe","800","Services","0","8,000 K"\n'
    '"Code.exe","5678","Console","1","400,000 K"\n'
    '"Antigravity.exe","4321","Console","1","90,000 K"\n'
)


def test_tasklist_csv_is_parsed_to_stems(monkeypatch):
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run(_TASKLIST_CSV))
    running = _running_processes()
    assert running["chrome"] == "chrome.exe"
    assert running["code"] == "Code.exe"
    assert running["antigravity"] == "Antigravity.exe"


def test_protected_processes_never_enter_the_candidate_pool(monkeypatch):
    """
    Dropped at the source, not at the threshold.

    taskkill /F on lsass or csrss takes the machine down. Filtering them out of
    the pool means no score, alias, or confirmation slip can aim at one — there
    is nothing to aim at.
    """
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run(_TASKLIST_CSV))
    running = _running_processes()
    assert "svchost" not in running
    assert "system" not in running
    assert "lsass" not in running


@pytest.mark.parametrize("query", ["svchost", "system", "lsass"])
def test_protected_processes_cannot_be_resolved(monkeypatch, query):
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run(_TASKLIST_CSV))
    assert resolve_process(query) is None


def test_running_process_resolves_exactly(monkeypatch):
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run(_TASKLIST_CSV))
    match = resolve_process("chrome")
    assert match is not None
    assert match.image == "chrome.exe"
    assert match.source == "exact"


def test_process_alias_resolves_to_its_image(monkeypatch):
    """"vs code" is nothing like "Code.exe", which is what the alias table is for."""
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run(_TASKLIST_CSV))
    match = resolve_process("vs code")
    assert match is not None
    assert match.image == "Code.exe"
    assert match.source == "alias"


def test_aliased_app_that_is_not_running_resolves_to_none(monkeypatch):
    """
    An alias miss means "not running" — deliberately not licence to go
    fuzzy-match something else and kill that instead.
    """
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run('"chrome.exe","1","C","1","1 K"\n'))
    assert resolve_process("vs code") is None


def test_app_that_is_not_running_resolves_to_none(monkeypatch):
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run(_TASKLIST_CSV))
    assert resolve_process("notepad") is None


def test_empty_process_list_resolves_to_none(monkeypatch):
    """No tasklist (or not Windows) must not become a wrong kill."""
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run(""))
    assert resolve_process("chrome") is None


def test_tasklist_failure_resolves_to_none(monkeypatch):
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run("", returncode=1))
    assert resolve_process("chrome") is None


# ---------------------------------------------------------------------------
# is_process_running() — the liveness re-check behind graceful close
# ---------------------------------------------------------------------------

def test_liveness_matches_by_exact_image(monkeypatch):
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run(_TASKLIST_CSV))
    assert is_process_running("chrome.exe")
    assert not is_process_running("notepad.exe")


def test_liveness_no_match_info_line_is_not_a_hit(monkeypatch):
    """The 'No tasks are running' line tasklist prints must not read as alive."""
    monkeypatch.setattr(
        app_registry.subprocess, "run",
        _fake_run("INFO: No tasks are running which match the specified criteria.\n"),
    )
    assert not is_process_running("chrome.exe")


def test_liveness_failure_reads_as_alive(monkeypatch):
    """
    A failed query must not let close_app report a close that never happened.

    Uncertainty escalates to a force kill the user already confirmed; a false
    "not running" would report success while the app is still up.
    """
    monkeypatch.setattr(app_registry.subprocess, "run", _fake_run("", returncode=1))
    assert is_process_running("chrome.exe")
