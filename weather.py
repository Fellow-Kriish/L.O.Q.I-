"""
LOQI — Weather Skill (Open-Meteo)

Fetch-only, keyless weather for the deterministic router:

  * geocoding-api.open-meteo.com  — spoken place name → coordinates
  * api.open-meteo.com            — coordinates → current conditions + today

Open-Meteo is free for non-commercial use with no API key and no account,
which keeps this skill local-first: nothing to sign up for, no secret in .env.

Caching, on purpose: a voice assistant gets asked the same weather question
several times in a row ("weather", "will it rain", "temperature") and the
answer does not change minute to minute. Place lookups live for the process —
places do not move — and reports live for WEATHER_CACHE_TTL_S.

Failures never raise into the caller — brain.py's rule, same reason: a spoken
assistant must always say something. (False, reason) comes back and gets
spoken. A network failure is deliberately NOT ActionUnavailable: the LLM
fallback has no live weather data, so falling through to it would trade an
honest "I couldn't reach the weather service" for a confident guess.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import config
from logging_setup import get_logger

log = get_logger(__name__)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_FORECAST_CURRENT_FIELDS = ("temperature_2m", "apparent_temperature", "weather_code")
_FORECAST_DAILY_FIELDS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
)

# WMO weather interpretation codes → spoken phrases. Values are noun phrases
# so they slot into "it's 24 degrees and <phrase>".
_WMO_PHRASES: dict[int, str] = {
    0: "clear skies",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    85: "snow showers",
    86: "snow showers",
    95: "a thunderstorm",
    96: "a thunderstorm with hail",
    99: "a thunderstorm with hail",
}

# Feels-like is only worth saying when it differs noticeably from the actual
# temperature — a one-degree gap is noise when spoken aloud.
_FEELS_LIKE_GAP = 2.0


class WeatherUnavailable(Exception):
    """Network or API failure — the caller should speak a failure line."""


class PlaceNotFound(Exception):
    """The spoken place name did not geocode."""


# Process-lifetime caches. Unsynchronized by design: the voice loop resolves
# one command at a time and nothing else touches these.
_geocode_cache: dict[str, tuple[float, float, str]] = {}
_report_cache: dict[tuple[float, float], tuple[float, str]] = {}


def _http_get_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    """GET ``url?params`` and parse the JSON body. Raises WeatherUnavailable."""
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "LOQI/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=config.WEATHER_REQUEST_TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WeatherUnavailable(str(e)) from e


def _resolve_place(place: str) -> tuple[float, float, str]:
    """
    Spoken place name → (latitude, longitude, display name).

    The top population hit wins, and its name is spoken back in the report —
    so a wrong Springfield is at least a self-identifying one.
    """
    key = " ".join(place.split()).lower()
    if not key:
        raise PlaceNotFound(place)
    cached = _geocode_cache.get(key)
    if cached is not None:
        return cached

    payload = _http_get_json(
        _GEOCODE_URL, {"name": key, "count": "1", "language": "en", "format": "json"}
    )
    results = payload.get("results") or []
    if not results:
        raise PlaceNotFound(place)
    first = results[0]
    resolved = (float(first["latitude"]), float(first["longitude"]), str(first["name"]))
    _geocode_cache[key] = resolved
    return resolved


def _round(value: float) -> int:
    return int(round(value))


def _format_report(payload: dict[str, Any], place: str) -> str:
    """Render the forecast payload as a short spoken report (TTS rules: no symbols)."""
    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    temp = current.get("temperature_2m")
    if temp is None:
        raise WeatherUnavailable("forecast payload had no current temperature")

    code = current.get("weather_code")
    phrase = _WMO_PHRASES.get(code) if isinstance(code, int) else None
    if phrase:
        parts = [f"In {place}, it's {_round(temp)} degrees and {phrase}"]
    else:
        parts = [f"In {place}, it's {_round(temp)} degrees"]

    apparent = current.get("apparent_temperature")
    if apparent is not None and abs(apparent - temp) >= _FEELS_LIKE_GAP:
        parts.append(f"Feels like {_round(apparent)}")

    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    rain_chances = daily.get("precipitation_probability_max") or []

    if highs and lows:
        today = f"Today, high of {_round(highs[0])}, low of {_round(lows[0])}"
        if rain_chances and rain_chances[0] is not None:
            today += f", with a {_round(rain_chances[0])} percent chance of rain"
        parts.append(today)

    return ". ".join(parts) + "."


def _fetch_report(lat: float, lon: float, place: str) -> str:
    params: dict[str, str] = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": ",".join(_FORECAST_CURRENT_FIELDS),
        "daily": ",".join(_FORECAST_DAILY_FIELDS),
        "timezone": "auto",
        "forecast_days": "1",
    }
    if config.WEATHER_UNITS == "imperial":
        params["temperature_unit"] = "fahrenheit"

    payload = _http_get_json(_FORECAST_URL, params)
    return _format_report(payload, place)


def current_report(place: str = "") -> tuple[bool, str]:
    """
    Build the spoken current-conditions report for ``place``.

    An empty ``place`` falls back to WEATHER_DEFAULT_CITY. Returns
    (False, spoken reason) on failure — the action layer speaks it directly.
    """
    query = " ".join(place.split()) or config.WEATHER_DEFAULT_CITY.strip()
    if not query:
        return False, "Which place should I check?"

    try:
        lat, lon, name = _resolve_place(query)
        now = time.monotonic()
        cached = _report_cache.get((lat, lon))
        if cached is not None and now - cached[0] < config.WEATHER_CACHE_TTL_S:
            return True, cached[1]

        report = _fetch_report(lat, lon, name)
        _report_cache[(lat, lon)] = (now, report)
        return True, report
    except PlaceNotFound:
        return False, f"I couldn't find {query} on the map."
    except WeatherUnavailable as e:
        log.warning("Weather fetch failed: %s", e)
        return False, "Sorry, I couldn't reach the weather service."
