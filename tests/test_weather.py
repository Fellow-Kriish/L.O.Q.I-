"""
Weather skill tests: report rendering, caching, and failure lines.

Everything network-shaped is faked — ``_http_get_json`` is monkeypatched with
a canned Open-Meteo responder, so these run offline with no pytest marker.
"""

import pytest

import actions
import config
import weather

_GEOCODE_URL = weather._GEOCODE_URL

_GEOCODE_DELHI = {
    "results": [{"latitude": 28.65, "longitude": 77.22, "name": "Delhi"}]
}
_FORECAST_DELHI = {
    "current": {
        "temperature_2m": 23.9,
        "apparent_temperature": 27.9,
        "weather_code": 3,
    },
    "daily": {
        "temperature_2m_max": [29.6],
        "temperature_2m_min": [22.6],
        "precipitation_probability_max": [74],
    },
}
_REPORT_DELHI = (
    "In Delhi, it's 24 degrees and overcast. Feels like 28. "
    "Today, high of 30, low of 23, with a 74 percent chance of rain."
)


class _FakeHttp:
    """``_http_get_json`` stand-in: serves canned payloads, records every call."""

    def __init__(self, geocode: dict, forecast: dict):
        self.geocode = geocode
        self.forecast = forecast
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, params: dict[str, str]) -> dict:
        self.calls.append((url, dict(params)))
        return self.geocode if url == _GEOCODE_URL else self.forecast


@pytest.fixture
def fresh_caches(monkeypatch):
    """Isolated caches so tests cannot leak state into each other."""
    monkeypatch.setattr(weather, "_geocode_cache", {})
    monkeypatch.setattr(weather, "_report_cache", {})


@pytest.fixture
def delhi(monkeypatch, fresh_caches):
    """Open-Meteo faked for Delhi, both endpoints."""
    fake = _FakeHttp(_GEOCODE_DELHI, _FORECAST_DELHI)
    monkeypatch.setattr(weather, "_http_get_json", fake)
    return fake


# ------------------------------------------------------------ report rendering
def test_format_report_renders_the_full_spoken_report():
    assert weather._format_report(_FORECAST_DELHI, "Delhi") == _REPORT_DELHI


def test_format_report_minimal_payload():
    payload = {"current": {"temperature_2m": 21.2, "weather_code": 42}}
    assert weather._format_report(payload, "Leh") == "In Leh, it's 21 degrees."


def test_format_report_skips_feels_like_when_the_gap_is_small():
    payload = {
        "current": {"temperature_2m": 24.0, "apparent_temperature": 25.0, "weather_code": 0},
    }
    assert weather._format_report(payload, "Delhi") == (
        "In Delhi, it's 24 degrees and clear skies."
    )


def test_format_report_without_temperature_is_unavailable():
    with pytest.raises(weather.WeatherUnavailable):
        weather._format_report({"current": {}}, "Delhi")


# ------------------------------------------------------------- current_report
def test_current_report_fetches_and_speaks(delhi):
    ok, text = weather.current_report("Delhi")

    assert ok
    assert text == _REPORT_DELHI


def test_second_ask_hits_the_cache(delhi):
    weather.current_report("delhi")
    assert len(delhi.calls) == 2  # one geocode + one forecast

    ok, text = weather.current_report("delhi")

    assert ok and text == _REPORT_DELHI
    assert len(delhi.calls) == 2  # nothing refetched


def test_report_cache_expires_after_ttl(delhi, monkeypatch):
    monkeypatch.setattr(config, "WEATHER_CACHE_TTL_S", 0)
    weather.current_report("delhi")

    weather.current_report("delhi")

    assert len(delhi.calls) == 3  # geocode cached, forecast refetched


def test_unknown_place_is_spoken_not_raised(monkeypatch, fresh_caches):
    monkeypatch.setattr(weather, "_http_get_json", _FakeHttp({}, _FORECAST_DELHI))

    ok, text = weather.current_report("klathvex")

    assert not ok
    assert text == "I couldn't find klathvex on the map."


def test_network_failure_returns_the_reason(monkeypatch, fresh_caches):
    def _dead(url: str, params: dict[str, str]) -> dict:
        raise weather.WeatherUnavailable("connection reset")

    monkeypatch.setattr(weather, "_http_get_json", _dead)

    ok, text = weather.current_report("Delhi")

    assert not ok
    assert text == "Sorry, I couldn't reach the weather service."


# -------------------------------------------------------------- place defaults
def test_empty_place_uses_the_default_city(delhi, monkeypatch):
    monkeypatch.setattr(config, "WEATHER_DEFAULT_CITY", "Delhi")

    ok, _ = weather.current_report("")

    assert ok
    assert delhi.calls[0][1]["name"] == "delhi"


def test_whitespace_place_uses_the_default_city(delhi, monkeypatch):
    monkeypatch.setattr(config, "WEATHER_DEFAULT_CITY", "Delhi")

    ok, _ = weather.current_report("   ")

    assert ok
    assert delhi.calls[0][1]["name"] == "delhi"


def test_blank_default_city_asks_which_place(monkeypatch, fresh_caches):
    monkeypatch.setattr(config, "WEATHER_DEFAULT_CITY", "")
    fake = _FakeHttp(_GEOCODE_DELHI, _FORECAST_DELHI)
    monkeypatch.setattr(weather, "_http_get_json", fake)

    ok, text = weather.current_report("")

    assert not ok
    assert text == "Which place should I check?"
    assert fake.calls == []


# ------------------------------------------------------------------- units
def test_imperial_units_request_fahrenheit(delhi, monkeypatch):
    monkeypatch.setattr(config, "WEATHER_UNITS", "imperial")

    weather.current_report("Delhi")

    forecast_calls = [call for call in delhi.calls if call[0] != _GEOCODE_URL]
    assert forecast_calls[0][1]["temperature_unit"] == "fahrenheit"


# ------------------------------------------------------------------ wiring
def test_weather_handler_is_registered():
    """The intent routes to handler="weather" — ACTION_MAP must know it."""
    assert actions.ACTION_MAP["weather"] is actions.get_weather
