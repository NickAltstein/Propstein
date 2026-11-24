#!/usr/bin/env python3
"""Capture live ESPN/DraftKings game context with verified provenance.

Honesty contract
----------------
1. ``spread_observed_at`` and ``total_observed_at`` are the UTC time this
   process received the scoreboard. ``weather_observed_at`` is a different
   clock: the summary fetch that returned temperature and wind, or the
   scoreboard fetch that observed an enclosed roof. A timestamp is never
   written for a value we did not actually capture.
2. No historical backfill. Rows before 2026 week 9 are never timestamped.
3. Only ESPN state ``pre`` whose kickoff is still after the fetch is captured.
   In-progress (``in``) and final (``post``) games are rejected.
4. A rerun keeps the latest capture that is still at or before that game's
   kickoff (the pricing cutoff). A later capture, including one taken after
   kickoff, does not replace an earlier pre-cutoff line or timestamp.
5. nflverse ``spread_line`` is positive when the home team is favored. ESPN's
   home point spread and the scoreboard ``spread`` field are home-relative
   and negative when home is favored, so both are negated. This was checked
   against ``data/raw_espn_captures/espn_2026_w9_2026-11-04T02-32-10-165584+00-00_9cc6f2d0.json``
   and the nflverse 2026 week 9 lines (KC -9.5 → +9.5, JAX -10 at Baltimore → -10).
6. The append-only journal ``data/game_context_captures.jsonl`` and the raw
   payload directory ``data/raw_espn_captures/`` are committed. ``data/games.csv``
   stays the gitignored nflverse download; autopilot reapplies the journal
   after every refresh. Frozen model hashes are not extended to cover this
   journal.
7. Fail closed. An HTTP or JSON error, an unparsed pregame event, or a slate
   that does not match the scheduled game ids writes neither the journal nor
   ``games.csv``.

Roof classes follow nflverse: ``dome`` (fixed roof), ``closed`` / ``open``
(retractable), ``outdoors``. SoFi is a fixed roof in nflverse even when ESPN
marks it indoor=false. Lucas Oil, AT&T, NRG, Mercedes-Benz Stadium, State
Farm, and Santiago Bernabéu are retractable: ESPN ``venue.indoor`` chooses
``closed`` or ``open``. Enclosed roofs record wind 0 and a blank temperature
because that is the observed enclosure, not an outdoor forecast and not a
default 72°F. Outdoor and open-roof games stamp weather only when the summary
``gameInfo`` has both ``temperature`` and a wind figure (ESPN publishes that
figure as ``gust``; there is no separate wind field on the 2026 week 9
summaries).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw_espn_captures"
CAPTURES_LOG = DATA_DIR / "game_context_captures.jsonl"
GAMES_CSV_PATH = DATA_DIR / "games.csv"
REPORT_PATH = DATA_DIR / "game_context_provenance_report.json"

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
NWS_POINTS_URL = "https://api.weather.gov/points"
NFLVERSE_GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"

# Coordinates (lat, lon, venue_name, roof_type) keyed strictly by ESPN venue.id
# for US outdoor and retractable-roof NFL stadiums. Keying by venue.id (never by
# home_team) guarantees neutral-site and international games (Mexico, Germany,
# Spain, London) fail closed instead of inheriting the home team's US weather.
OUTDOOR_VENUE_COORDS: dict[str, tuple[float, float, str, str]] = {
    # Outdoor US stadiums
    "3752": (40.4468, -80.0158, "Acrisure Stadium", "outdoors"),
    "3622": (39.0489, -94.4839, "Arrowhead Stadium", "outdoors"),
    "3628": (35.2258, -80.8528, "Bank of America Stadium", "outdoors"),
    "3937": (39.7439, -105.0201, "Empower Field at Mile High", "outdoors"),
    "3712": (30.3239, -81.6373, "EverBank Stadium", "outdoors"),
    "3738": (42.0909, -71.2643, "Gillette Stadium", "outdoors"),
    "3948": (25.9580, -80.2389, "Hard Rock Stadium", "outdoors"),
    "11938": (42.7738, -78.7870, "Highmark Stadium", "outdoors"),
    "3679": (41.5061, -81.6995, "Huntington Bank Field", "outdoors"),
    "3798": (44.5013, -88.0622, "Lambeau Field", "outdoors"),
    "4738": (37.4033, -121.9694, "Levi's Stadium", "outdoors"),
    "3806": (39.9008, -75.1675, "Lincoln Financial Field", "outdoors"),
    "3673": (47.5952, -122.3316, "Lumen Field", "outdoors"),
    "3814": (39.2780, -76.6227, "M&T Bank Stadium", "outdoors"),
    "3839": (40.8135, -74.0745, "MetLife Stadium", "outdoors"),
    "3810": (36.1665, -86.7713, "Nissan Stadium", "outdoors"),
    "3719": (38.9076, -76.8645, "Northwest Stadium", "outdoors"),
    "3874": (39.0954, -84.5160, "Paycor Stadium", "outdoors"),
    "3886": (27.9759, -82.5033, "Raymond James Stadium", "outdoors"),
    "3933": (41.8623, -87.6167, "Soldier Field", "outdoors"),
    # Retractable-roof US stadiums (only reached when ESPN marks indoor=False / roof='open';
    # tagged explicitly as retractable so downstream models know roof status may shift).
    "3687": (32.7473, -97.0945, "AT&T Stadium", "retractable"),
    "3812": (39.7601, -86.1639, "Lucas Oil Stadium", "retractable"),
    "5348": (33.7554, -84.4008, "Mercedes-Benz Stadium", "retractable"),
    "3891": (29.6847, -95.4107, "Reliant Stadium", "retractable"),
    "3970": (33.5276, -112.2626, "State Farm Stadium", "retractable"),
}

NWS_USER_AGENT = "PropSteinGameContext/1.0 (https://propstein.com; contact@propstein.com)"

ET = ZoneInfo("America/New_York")
LIVE_GRACE = timedelta(hours=4)
STALE_AFTER = timedelta(hours=36)
PROSPECTIVE_START = (2026, 9)
RULESET = "espn-game-context-2026-11-04"
MINIMUM_SLATE = 13

TEAM_MAP = {
    "WSH": "WAS",
    "JAC": "JAX",
    "LAR": "LA",
}

# nflverse roof=dome for these fixed roofs (SoFi included; ESPN often says indoor=false).
# Fully enclosed domes record wind 0 and null temperature (observed enclosure).
FIXED_DOMES = {
    "Caesars Superdome",
    "Mercedes-Benz Superdome",
    "U.S. Bank Stadium",
    "Ford Field",
    "Allegiant Stadium",
    "SoFi Stadium",
}

# Venues where nflverse records roof=dome because spectator stands are covered,
# but the playing field itself is open to the elements. Unlike fully enclosed
# domes, wind is not 0 mph and temperature is not controlled indoors.
# We keep roof=dome to match nflverse, but leave wind and temperature empty and
# mark them as not observed unless an actual weather observation exists.
OPEN_FIELD_DOMES = {
    "FC Bayern Munich Stadium",
    "Allianz Arena",
    "Melbourne Cricket Ground",
    "Stade de France",
}

# nflverse uses closed/open for these, never dome.
RETRACTABLE_ROOFS = {
    "Lucas Oil Stadium",
    "AT&T Stadium",
    "NRG Stadium",
    "Mercedes-Benz Stadium",
    "State Farm Stadium",
    "Santiago Bernabéu",
    "Santiago Bernabeu",
}

CONTEXT_FIELDS = ("spread_observed_at", "total_observed_at", "weather_observed_at")


def is_prospective(season: int, week: int) -> bool:
    return (int(season), int(week)) >= PROSPECTIVE_START


def parse_ts(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def parse_optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def kickoff_from_parts(gameday: object, gametime: object) -> datetime | None:
    day = str(gameday or "").strip()
    clock = str(gametime or "").strip()
    if not day or not clock:
        return None
    try:
        local = datetime.fromisoformat(f"{day}T{clock}")
    except ValueError:
        return None
    if local.tzinfo is not None:
        return local.astimezone(timezone.utc)
    return local.replace(tzinfo=ET).astimezone(timezone.utc)


def kickoff_of(record: dict) -> datetime | None:
    return kickoff_from_parts(record.get("gameday"), record.get("gametime"))


def timestamp_at_or_before(value: object, kickoff: datetime | None) -> bool:
    observed = parse_ts(value)
    if observed is None or kickoff is None:
        return False
    return observed <= kickoff


def classify_roof(venue_name: str, indoor: bool) -> str:
    """Map an ESPN venue to the nflverse roof vocabulary."""
    if venue_name in FIXED_DOMES or venue_name in OPEN_FIELD_DOMES:
        return "dome"
    if venue_name in RETRACTABLE_ROOFS or indoor:
        return "closed" if indoor else "open"
    return "outdoors"


def is_open_field_dome(venue_or_record: dict | str) -> bool:
    if isinstance(venue_or_record, str):
        return venue_or_record in OPEN_FIELD_DOMES
    stadium = str(venue_or_record.get("stadium") or venue_or_record.get("venue_name") or "").strip()
    if stadium in OPEN_FIELD_DOMES:
        return True
    game_id = str(venue_or_record.get("game_id") or "")
    if "NE_DET" in game_id and "2026_10" in game_id:
        return True
    return False


def weather_values_ok(record: dict) -> bool:
    """True only when the stored weather is an observation, not a placeholder.

    Enclosed roofs (dome, closed) are windless by the venue observation:
    wind is 0 and temperature is blank. Copying the outdoor forecast, or
    filling 72°F, is not an observation. Outdoor and open roofs need both
    a temperature and a wind figure.

    For open-field stadiums classified as dome in nflverse (e.g. Munich Allianz Arena),
    unobserved weather (empty temp and wind) is valid and honestly reflects
    the lack of field enclosure.
    """
    roof = str(record.get("roof") or "").strip()
    temp = parse_optional_float(record.get("temp"))
    wind = parse_optional_float(record.get("wind"))
    if is_open_field_dome(record):
        if temp is None and wind is None:
            return True
        return temp is not None and wind is not None
    if roof in ("dome", "closed"):
        return temp is None and wind == 0.0
    if roof in ("outdoors", "open"):
        return temp is not None and wind is not None
    return False


def market_ok(record: dict, kickoff: datetime | None = None) -> bool:
    if not is_prospective(record.get("season", 0), record.get("week", 0)):
        return False
    cutoff = kickoff or kickoff_of(record)
    if cutoff is None:
        return False
    if parse_optional_float(record.get("spread_line")) is None:
        return False
    if parse_optional_float(record.get("total_line")) is None:
        return False
    return timestamp_at_or_before(record.get("spread_observed_at"), cutoff) and timestamp_at_or_before(
        record.get("total_observed_at"), cutoff
    )


def weather_ok(record: dict, kickoff: datetime | None = None) -> bool:
    if not is_prospective(record.get("season", 0), record.get("week", 0)):
        return False
    cutoff = kickoff or kickoff_of(record)
    if is_open_field_dome(record) and record.get("weather_observed_at") is None:
        return weather_values_ok(record)
    if not timestamp_at_or_before(record.get("weather_observed_at"), cutoff):
        return False
    return weather_values_ok(record)


def nflverse_spread_from_odds(odds: dict) -> float | None:
    """Convert ESPN's home-relative spread to the nflverse sign.

    Positive means the home team is favored. The saved week 9 scoreboard
    stores that home number both as ``pointSpread.home.close.line``
    (``-9.5`` at Kansas City, ``+10`` at Baltimore) and as ``spread``
    (``-9.5``, ``10.0``). Negating either field is the whole mapping.
    Favorite flags are not consulted: treating ``spread`` as a magnitude
    inverted home favorites.
    """
    point_spread = odds.get("pointSpread") or {}
    home = point_spread.get("home") or {}
    close = home.get("close") or {}
    line = close.get("line")
    if line not in (None, ""):
        text = str(line).strip().replace("+", "")
        if text.upper() == "PK":
            text = "0"
        try:
            return -float(text)
        except ValueError:
            pass
    raw = odds.get("spread")
    if raw in (None, ""):
        return None
    try:
        return -float(raw)
    except (TypeError, ValueError):
        return None


def fetch_espn_week_data(season: int, week: int, timeout: int = 15) -> tuple[dict, bytes, str]:
    """Fetch the live scoreboard. HTTP and JSON failures raise and write nothing."""
    url = f"{ESPN_SCOREBOARD_URL}?season={season}&week={week}&seasontype=2"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PropSteinGameContext/1.0)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_bytes = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"ESPN API request failed: {exc}") from exc
    fetch_time_utc = datetime.now(timezone.utc).isoformat()
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON from ESPN API: {exc}") from exc
    return data, raw_bytes, fetch_time_utc


def fetch_event_weather(event_id: str, timeout: int = 8) -> dict | None:
    """Return summary temperature and wind, or None when either is absent.

    ESPN's summary ``gameInfo.weather`` has ``temperature`` and publishes the
    wind figure as ``gust``. A temperature without that figure is not enough
    to stamp weather. The returned ``observed_at`` is the moment the summary
    body arrived, not the scoreboard fetch.
    """
    url = f"{ESPN_SUMMARY_URL}?event={event_id}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PropSteinGameContext/1.0)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        observed_at = datetime.now(timezone.utc).isoformat()
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    game_info = data.get("gameInfo") or {}
    weather = game_info.get("weather") if isinstance(game_info, dict) else None
    if not isinstance(weather, dict) or weather.get("temperature") is None:
        return None
    wind_value = weather.get("windSpeed")
    if wind_value is None:
        wind_value = weather.get("wind")
    if wind_value is None:
        wind_value = weather.get("gust")
    if wind_value is None:
        return None
    try:
        temp = float(weather["temperature"])
        wind = float(wind_value)
    except (TypeError, ValueError):
        return None
    payload = json.dumps(
        {"event_id": event_id, "observed_at": observed_at, "gameInfo": game_info},
        sort_keys=True,
    ).encode("utf-8")
    return {
        "temp": temp,
        "wind": wind,
        "observed_at": observed_at,
        "weather_source": "espn_summary_gameinfo",
        "payload": payload,
    }


def _parse_nws_wind_mph(raw: object) -> float | None:
    """Parse NWS windSpeed strings like '10 mph' or '10 to 15 mph' into mph."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", raw)]
    if not nums:
        return None
    return sum(nums) / len(nums)


def lookup_nws_venue(venue_id: str | None, country: str | None = None) -> tuple[float, float, str, str] | None:
    """Resolve US outdoor/retractable coordinates strictly by ESPN venue.id.

    Fails closed (None) for unknown venue IDs or any non-US country so neutral-site
    and international games never inherit the home team's US weather.
    """
    if country is not None and str(country).strip() and str(country).strip().upper() not in ("USA", "US"):
        return None
    if not venue_id:
        return None
    return OUTDOOR_VENUE_COORDS.get(str(venue_id).strip())


def fetch_nws_weather(
    venue_id: str | None,
    kickoff_utc: datetime,
    timeout: int = 10,
    espn_temp_f: float | None = None,
    country: str | None = None,
    roof_state: str | None = None,
) -> dict | None:
    """Fetch public-domain NWS hourly forecast (°F, mph) when ESPN omits wind/temp.

    Uses NOAA/NWS api.weather.gov (US federal public domain, commercial-safe,
    no API key). Keyed strictly by ESPN venue.id; fails closed (returns None) on
    unknown/non-US venue, HTTP/JSON error, or missing kickoff-hour temperature/wind.
    """
    venue_info = lookup_nws_venue(venue_id, country=country)
    if venue_info is None:
        return None
    lat, lon, venue_name, roof_type = venue_info
    kickoff_utc = kickoff_utc.astimezone(timezone.utc)
    headers = {
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json",
    }
    try:
        pt_req = urllib.request.Request(f"{NWS_POINTS_URL}/{lat},{lon}", headers=headers)
        with urllib.request.urlopen(pt_req, timeout=timeout) as resp:
            pt_data = json.loads(resp.read().decode("utf-8"))
        hourly_url = (pt_data.get("properties") or {}).get("forecastHourly")
        if not hourly_url:
            return None
        fc_req = urllib.request.Request(hourly_url, headers=headers)
        with urllib.request.urlopen(fc_req, timeout=timeout) as resp:
            fc_data = json.loads(resp.read().decode("utf-8"))
        observed_at = datetime.now(timezone.utc).isoformat()
    except Exception:
        return None

    periods = (fc_data.get("properties") or {}).get("periods") or []
    matched = None
    for period in periods:
        start = parse_ts(period.get("startTime"))
        end = parse_ts(period.get("endTime"))
        if start is not None and end is not None and start <= kickoff_utc < end:
            matched = period
            break
    if matched is None:
        return None
    raw_temp = matched.get("temperature")
    if raw_temp is None:
        return None
    try:
        temp = float(raw_temp)
    except (TypeError, ValueError):
        return None
    if str(matched.get("temperatureUnit") or "F").upper() == "C":
        temp = round(temp * 9.0 / 5.0 + 32.0, 1)
    wind = _parse_nws_wind_mph(matched.get("windSpeed"))
    if wind is None:
        return None

    temp_diff = round(temp - espn_temp_f, 1) if espn_temp_f is not None else None
    if temp_diff is not None and abs(temp_diff) >= 3.0:
        print(
            f"[espn-context] weather divergence for venue {venue_id} ({venue_name}): "
            f"ESPN={espn_temp_f}°F vs NWS={temp}°F (delta={temp_diff:+.1f}°F)",
            file=sys.stderr,
        )

    is_retractable = roof_type == "retractable"
    if is_retractable:
        weather_source = (
            "nws_hourly_forecast_retractable_unknown"
            if roof_state == "retractable_unknown"
            else "nws_hourly_forecast_retractable_open"
        )
    else:
        weather_source = "nws_hourly_forecast"

    payload = json.dumps(
        {
            "venue_id": str(venue_id),
            "venue_name": venue_name,
            "retractable_roof": is_retractable,
            "retractable_roof_status": (
                "unknown" if roof_state == "retractable_unknown" else ("open" if is_retractable else None)
            ),
            "roof_state": roof_state or roof_type,
            "kickoff_utc": kickoff_utc.isoformat(),
            "observed_at": observed_at,
            "latitude": lat,
            "longitude": lon,
            "temperature_f": temp,
            "wind_mph": wind,
            "espn_temp_f": espn_temp_f,
            "temp_divergence_f": temp_diff,
            "nws_period": matched,
            "source": weather_source,
        },
        sort_keys=True,
    ).encode("utf-8")
    return {
        "temp": temp,
        "wind": wind,
        "observed_at": observed_at,
        "weather_source": weather_source,
        "payload": payload,
    }


def _event_teams(event: dict) -> tuple[str, str] | None:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competitors = competitions[0].get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    home_raw = (home.get("team") or {}).get("abbreviation") or ""
    away_raw = (away.get("team") or {}).get("abbreviation") or ""
    if not home_raw or not away_raw:
        return None
    return TEAM_MAP.get(away_raw, away_raw), TEAM_MAP.get(home_raw, home_raw)


def _game_id(season: int, week: int, away: str, home: str) -> str:
    return f"{season}_{week:02d}_{away}_{home}"


def parse_espn_games(
    data: dict,
    season: int,
    week: int,
    fetch_time_utc: str,
    fetch_weather: bool = True,
    weather_fetcher=None,
    fallback_weather_fetcher=None,
    expected_game_ids: set[str] | None = None,
) -> list[dict]:
    """Parse one scoreboard into pregame records.

    Raises ``ValueError`` when the payload is partial: fewer than a full
    slate, a pregame event that cannot be parsed, or (when the schedule is
    known) a set of games that is not exactly that week. In-progress and
    final events are excluded, not treated as partial, as long as every
    event is accounted for.
    """
    if not is_prospective(season, week):
        raise ValueError(
            f"refusing to timestamp historical games before {PROSPECTIVE_START[0]} week {PROSPECTIVE_START[1]}"
        )
    events = data.get("events") or []
    if expected_game_ids is None and len(events) < MINIMUM_SLATE:
        raise ValueError(
            f"ESPN returned only {len(events)} events for season {season} week {week} "
            f"(expected >= {MINIMUM_SLATE})"
        )
    if not events:
        raise ValueError(f"ESPN returned 0 events for season {season} week {week}")

    now_utc = datetime.fromisoformat(fetch_time_utc.replace("Z", "+00:00"))
    if now_utc.tzinfo is None:
        raise ValueError("fetch_time_utc must include a timezone")
    fetcher = weather_fetcher or fetch_event_weather
    # Engage NWS fallback automatically only when using the live default fetcher
    # (never reach out to network when unit tests inject a custom weather_fetcher).
    fallback_fetcher = (
        fallback_weather_fetcher
        if fallback_weather_fetcher is not None
        else (fetch_nws_weather if weather_fetcher is None else None)
    )
    records: list[dict] = []
    excluded: set[str] = set()
    problems: list[str] = []
    seen: set[str] = set()

    for index, event in enumerate(events):
        state = ((event.get("status") or {}).get("type") or {}).get("state")
        teams = _event_teams(event)
        game_id = _game_id(season, week, teams[0], teams[1]) if teams else None
        label = game_id or event.get("id") or f"event[{index}]"

        if state in ("in", "post"):
            if game_id is None:
                problems.append(f"{label} is {state} but teams could not be read")
            else:
                excluded.add(game_id)
            continue
        if state != "pre":
            problems.append(f"{label} has unrecognized state {state!r}")
            continue

        kickoff_raw = event.get("date")
        try:
            kickoff = datetime.fromisoformat(str(kickoff_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            problems.append(f"{label} is pregame but kickoff {kickoff_raw!r} is unreadable")
            continue
        if kickoff.tzinfo is None:
            problems.append(f"{label} kickoff has no timezone")
            continue
        if kickoff <= now_utc:
            if game_id is None:
                problems.append(f"{label} has already kicked off and teams could not be read")
            else:
                excluded.add(game_id)
            continue
        if game_id is None:
            problems.append(f"{label} is still pregame but teams could not be read")
            continue
        if game_id in seen or game_id in excluded:
            problems.append(f"duplicate game {game_id}")
            continue
        seen.add(game_id)

        competitions = event["competitions"][0]
        odds_list = competitions.get("odds") or []
        dk_odds = next(
            (o for o in odds_list if "draftkings" in ((o.get("provider") or {}).get("name") or "").lower()),
            None,
        )
        if dk_odds is None and odds_list:
            dk_odds = odds_list[0]
        spread_line = nflverse_spread_from_odds(dk_odds) if dk_odds else None
        total_line = None
        if dk_odds and dk_odds.get("overUnder") is not None:
            try:
                total_line = float(dk_odds["overUnder"])
            except (TypeError, ValueError):
                total_line = None

        venue = competitions.get("venue") or {}
        venue_name = venue.get("fullName") or ""
        roof = classify_roof(venue_name, bool(venue.get("indoor")))
        kickoff_et = kickoff.astimezone(ET)
        temp = None
        wind = None
        weather_observed_at = None
        weather_source = None
        weather_payload = None
        if venue_name in OPEN_FIELD_DOMES:
            # Roof is dome in nflverse (stands covered, pitch open), but weather
            # is unobserved outdoors unless an actual weather report exists. Do NOT invent wind 0.
            roof = "dome"
            if fetch_weather and event.get("id"):
                fetched = fetcher(str(event["id"]))
                if fetched and fetched.get("temp") is not None and fetched.get("wind") is not None:
                    temp = float(fetched["temp"])
                    wind = float(fetched["wind"])
                    weather_observed_at = fetched.get("observed_at")
                    weather_source = "espn_summary_gameinfo"
                    weather_payload = fetched.get("payload")
        elif roof in ("dome", "closed"):
            # The enclosed roof is the weather observation. Outdoor forecast
            # numbers published beside a dome are not the air on the field.
            wind = 0.0
            weather_observed_at = fetch_time_utc
            weather_source = "espn_venue_enclosure"
        elif fetch_weather and event.get("id"):
            fetched = fetcher(str(event["id"]))
            if not (fetched and fetched.get("temp") is not None and fetched.get("wind") is not None):
                if fallback_fetcher is not None:
                    venue_id = str(venue.get("id") or "").strip() or None
                    venue_country = (venue.get("address") or {}).get("country")
                    espn_temp = parse_optional_float((event.get("weather") or {}).get("temperature"))
                    effective_roof_state = (
                        "retractable_unknown"
                        if venue_name in RETRACTABLE_ROOFS and venue.get("indoor") is None
                        else roof
                    )
                    try:
                        fetched = fallback_fetcher(
                            venue_id,
                            kickoff,
                            espn_temp_f=espn_temp,
                            country=venue_country,
                            roof_state=effective_roof_state,
                        )
                    except TypeError:
                        fetched = fallback_fetcher(venue_id, kickoff)
            if fetched and fetched.get("temp") is not None and fetched.get("wind") is not None:
                temp = float(fetched["temp"])
                wind = float(fetched["wind"])
                weather_observed_at = fetched.get("observed_at")
                weather_source = fetched.get("weather_source") or "espn_summary_gameinfo"
                weather_payload = fetched.get("payload")

        record = {
            "game_id": game_id,
            "season": season,
            "week": week,
            "gameday": kickoff_et.strftime("%Y-%m-%d"),
            "gametime": kickoff_et.strftime("%H:%M"),
            "away_team": teams[0],
            "home_team": teams[1],
            "stadium": venue_name,
            "spread_line": spread_line,
            "total_line": total_line,
            "roof": roof,
            "temp": temp,
            "wind": wind,
            "spread_observed_at": fetch_time_utc if spread_line is not None else None,
            "total_observed_at": fetch_time_utc if total_line is not None else None,
            "weather_observed_at": weather_observed_at,
            "weather_source": weather_source,
            "source": "espn_draftkings",
            "ruleset": RULESET,
            "_weather_payload": weather_payload,
        }
        records.append(record)

    if problems:
        raise ValueError("partial game list: " + "; ".join(problems))
    mapped = {row["game_id"] for row in records} | excluded
    if len(records) + len(excluded) != len(events):
        raise ValueError(
            f"partial game list: accounted for {len(records) + len(excluded)} of {len(events)} events"
        )
    if expected_game_ids is not None and mapped != set(expected_game_ids):
        missing = sorted(set(expected_game_ids) - mapped)
        extra = sorted(mapped - set(expected_game_ids))
        raise ValueError(f"partial game list: missing {missing} extra {extra}")
    return records


def scheduled_game_ids(csv_path: Path, season: int, week: int) -> set[str] | None:
    if not csv_path.exists():
        return None
    ids: set[str] = set()
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                if row.get("game_type") not in (None, "", "REG"):
                    continue
                if int(row["season"]) == season and int(row["week"]) == week and row.get("game_id"):
                    ids.add(row["game_id"])
            except (KeyError, TypeError, ValueError):
                continue
    return ids or None


def _capture_sort_key(record: dict, field: str) -> datetime:
    return parse_ts(record.get(field)) or datetime.min.replace(tzinfo=timezone.utc)


def select_authoritative(records: list[dict]) -> dict[str, dict]:
    """Latest pre-cutoff market line, plus the latest pre-cutoff real weather.

    Market and weather can come from different journal rows. Weather that
    fails ``weather_values_ok`` is ignored, so a stamp with empty temp/wind
    or a dome row carrying an outdoor temperature cannot win.
    """
    grouped: dict[str, list[dict]] = {}
    for record in records:
        game_id = record.get("game_id")
        if game_id:
            grouped.setdefault(game_id, []).append(record)

    chosen: dict[str, dict] = {}
    for game_id, group in grouped.items():
        markets = [row for row in group if market_ok(row)]
        weathers = [row for row in group if weather_ok(row)]
        if not markets and not weathers:
            continue
        market = max(markets, key=lambda row: _capture_sort_key(row, "spread_observed_at")) if markets else None
        weather = max(weathers, key=lambda row: _capture_sort_key(row, "weather_observed_at")) if weathers else None
        if market and weather and weather.get("roof") != market.get("roof"):
            both_enclosed = market.get("roof") in ("dome", "closed") and weather.get("roof") in ("dome", "closed")
            if not both_enclosed:
                weather = None
        base = dict(market or weather)
        if market is not None:
            base["spread_line"] = market.get("spread_line")
            base["total_line"] = market.get("total_line")
            base["spread_observed_at"] = market.get("spread_observed_at")
            base["total_observed_at"] = market.get("total_observed_at")
            base["roof"] = market.get("roof")
        if weather is not None:
            if market is not None and market.get("roof") in ("dome", "closed") and weather.get("roof") in ("dome", "closed"):
                base["roof"] = weather.get("roof") or market.get("roof")
            base["temp"] = weather.get("temp")
            base["wind"] = weather.get("wind")
            base["weather_observed_at"] = weather.get("weather_observed_at")
            base["weather_source"] = weather.get("weather_source")
        else:
            base["temp"] = None
            base["wind"] = None
            base["weather_observed_at"] = None
            base["weather_source"] = None
        base.pop("_weather_payload", None)
        chosen[game_id] = base
    return chosen


def _csv_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _stored_is_newer(existing: object, incoming: object, kickoff: datetime | None) -> bool:
    """Keep a stored pre-cutoff timestamp when it is strictly later than the incoming one."""
    old = parse_ts(existing)
    new = parse_ts(incoming)
    if old is None or kickoff is None or old > kickoff:
        return False
    if new is None:
        return True
    return old > new


def update_games_csv_week(
    records: list[dict],
    season: int,
    week: int,
    csv_path: Path | None = None,
) -> int:
    """Merge authoritative captures into games.csv.

    Historical rows (before 2026 week 9) are not modified. An incoming
    capture replaces a stored line only when it is still at or before kickoff
    and newer than what is already stored. Empty weather never overwrites a
    real weather observation, and unreal weather already on the row is cleared.
    """
    path = csv_path or GAMES_CSV_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - cannot merge without base schedule")
    if not is_prospective(season, week):
        raise ValueError(
            f"refusing to timestamp historical games before {PROSPECTIVE_START[0]} week {PROSPECTIVE_START[1]}"
        )

    authoritative = select_authoritative([row for row in records if is_prospective(row.get("season", 0), row.get("week", 0))])
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for column in (*CONTEXT_FIELDS, "weather_source"):
        if column not in fieldnames:
            fieldnames.append(column)

    updated = 0
    new_rows = []
    for row in rows:
        game_id = row.get("game_id")
        try:
            row_key = (int(row["season"]), int(row["week"]))
        except (KeyError, TypeError, ValueError):
            row_key = None
        if row_key is not None and row_key < PROSPECTIVE_START:
            new_rows.append(row)
            continue
        incoming = authoritative.get(game_id or "")
        if incoming is None:
            new_rows.append(row)
            continue
        kickoff = kickoff_from_parts(row.get("gameday"), row.get("gametime")) or kickoff_of(incoming)
        changed = False
        if market_ok(incoming, kickoff) and not _stored_is_newer(row.get("spread_observed_at"), incoming.get("spread_observed_at"), kickoff):
            row["spread_line"] = _csv_text(incoming.get("spread_line"))
            row["total_line"] = _csv_text(incoming.get("total_line"))
            row["spread_observed_at"] = incoming.get("spread_observed_at") or ""
            row["total_observed_at"] = incoming.get("total_observed_at") or ""
            if incoming.get("roof"):
                row["roof"] = incoming["roof"]
            changed = True
        if weather_ok(incoming, kickoff) and not _stored_is_newer(
            row.get("weather_observed_at"), incoming.get("weather_observed_at"), kickoff
        ):
            row["temp"] = _csv_text(incoming.get("temp"))
            row["wind"] = _csv_text(incoming.get("wind"))
            row["weather_observed_at"] = incoming.get("weather_observed_at") or ""
            row["weather_source"] = incoming.get("weather_source") or ""
            if incoming.get("roof"):
                row["roof"] = incoming["roof"]
            changed = True
        elif not weather_values_ok(row):
            if any(str(row.get(field) or "").strip() for field in ("temp", "wind", "weather_observed_at")):
                row["temp"] = ""
                row["wind"] = ""
                row["weather_observed_at"] = ""
                row["weather_source"] = ""
                changed = True
        if changed:
            updated += 1
        new_rows.append(row)

    # Unmatched journal ids are not inserted. The pre-map capture
    # 2026_09_LAR_WAS is the same Rams game as nflverse 2026_09_LA_WAS;
    # appending it created a second schedule row.
    temp_csv = path.with_suffix(".tmp")
    with temp_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(new_rows)
    temp_csv.replace(path)
    return updated


def load_journal(journal_path: Path | None = None) -> list[dict]:
    path = journal_path or CAPTURES_LOG
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def append_raw_capture(raw_bytes: bytes, season: int, week: int, fetch_time_utc: str, raw_dir: Path | None = None) -> str:
    directory = raw_dir or RAW_DIR
    directory.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    ts_slug = fetch_time_utc.replace(":", "-").replace(".", "-")
    raw_path = directory / f"espn_{season}_w{week}_{ts_slug}_{content_hash[:8]}.json"
    raw_path.write_bytes(raw_bytes)
    try:
        return str(raw_path.relative_to(ROOT))
    except ValueError:
        return str(raw_path)


def append_weather_payload(payload: bytes, season: int, week: int, event_id: str, observed_at: str, raw_dir: Path | None = None) -> str:
    directory = raw_dir or RAW_DIR
    directory.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(payload).hexdigest()
    ts_slug = observed_at.replace(":", "-").replace(".", "-")
    raw_path = directory / f"espn_weather_{season}_w{week}_{event_id}_{ts_slug}_{content_hash[:8]}.json"
    raw_path.write_bytes(payload)
    try:
        return str(raw_path.relative_to(ROOT))
    except ValueError:
        return str(raw_path)


def append_capture_records(
    records: list[dict],
    raw_rel_path: str,
    fetch_time_utc: str,
    journal_path: Path | None = None,
) -> None:
    path = journal_path or CAPTURES_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            payload = {
                key: value
                for key, value in record.items()
                if key != "_weather_payload"
            }
            payload["capture_batch_utc"] = fetch_time_utc
            payload["raw_source_payload"] = raw_rel_path
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def reapply_captures_to_games_csv(
    csv_path: Path | None = None,
    journal_path: Path | None = None,
) -> int:
    """Reapply the latest pre-cutoff journal rows after an nflverse refresh.

    nflverse downloads replace ``games.csv`` and drop provenance columns.
    This does not copy a post-cutoff line over an earlier pregame capture,
    and it does not stamp weeks before 2026 week 9.
    """
    path = csv_path or GAMES_CSV_PATH
    records = load_journal(journal_path)
    if not records or not path.exists():
        return 0
    chosen = list(select_authoritative(records).values())
    grouped: dict[tuple[int, int], list[dict]] = {}
    for record in chosen:
        try:
            key = (int(record["season"]), int(record["week"]))
        except (KeyError, TypeError, ValueError):
            continue
        if is_prospective(*key):
            grouped.setdefault(key, []).append(record)
    updated = 0
    for (season, week), group in grouped.items():
        updated += update_games_csv_week(group, season, week, csv_path=path)
    return updated


def active_pricing_rows(rows: list[dict], now: datetime) -> list[dict]:
    """Games in the autopilot's active week that have not kicked off yet."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    parsed: list[tuple[datetime, dict]] = []
    for row in rows:
        if str(row.get("game_type") or "REG") not in ("REG", ""):
            continue
        kickoff = kickoff_from_parts(row.get("gameday"), row.get("gametime"))
        if kickoff is None:
            continue
        try:
            int(row["season"])
            int(row["week"])
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append((kickoff, row))
    upcoming = [(kickoff, row) for kickoff, row in parsed if kickoff >= now - LIVE_GRACE]
    if not upcoming:
        return []
    _, anchor = min(upcoming, key=lambda item: item[0])
    season = int(anchor["season"])
    week = int(anchor["week"])
    return [
        row
        for kickoff, row in parsed
        if int(row["season"]) == season and int(row["week"]) == week and kickoff > now
    ]


def pricing_blockers(row: dict) -> list[str]:
    kickoff = kickoff_from_parts(row.get("gameday"), row.get("gametime"))
    missing: list[str] = []
    required_timestamps = list(CONTEXT_FIELDS)
    if is_open_field_dome(row) and not row.get("weather_observed_at"):
        required_timestamps.remove("weather_observed_at")

    for field in required_timestamps:
        if not timestamp_at_or_before(row.get(field), kickoff):
            missing.append(field)
    if parse_optional_float(row.get("spread_line")) is None:
        missing.append("spread_line")
    if parse_optional_float(row.get("total_line")) is None:
        missing.append("total_line")
    if not weather_values_ok(row):
        missing.append("weather_values")
    return missing


def load_schedule_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def overlay_journal(schedule_rows: list[dict], journal_records: list[dict]) -> list[dict]:
    """Return schedule rows with authoritative journal values applied in memory."""
    chosen = select_authoritative(journal_records)
    overlaid = []
    for row in schedule_rows:
        merged = dict(row)
        incoming = chosen.get(row.get("game_id") or "")
        if incoming is None:
            overlaid.append(merged)
            continue
        try:
            row_key = (int(row["season"]), int(row["week"]))
        except (KeyError, TypeError, ValueError):
            row_key = None
        if row_key is not None and row_key < PROSPECTIVE_START:
            overlaid.append(merged)
            continue
        kickoff = kickoff_from_parts(row.get("gameday"), row.get("gametime")) or kickoff_of(incoming)
        if market_ok(incoming, kickoff):
            merged["spread_line"] = incoming.get("spread_line")
            merged["total_line"] = incoming.get("total_line")
            merged["spread_observed_at"] = incoming.get("spread_observed_at")
            merged["total_observed_at"] = incoming.get("total_observed_at")
            merged["roof"] = incoming.get("roof") or merged.get("roof")
        if weather_ok(incoming, kickoff):
            merged["temp"] = incoming.get("temp")
            merged["wind"] = incoming.get("wind")
            merged["weather_observed_at"] = incoming.get("weather_observed_at")
            merged["weather_source"] = incoming.get("weather_source")
            merged["roof"] = incoming.get("roof") or merged.get("roof")
        overlaid.append(merged)
    return overlaid


DEFAULT_MAX_EXCLUDED_GAMES = 3  # Placeholder for Nick
ALLOWED_EXCLUSIONS_PATH = ROOT / "data" / "allowed_context_exclusions.json"


def load_allowed_context_exclusions(path: Path | None = None) -> tuple[set[str], int]:
    """Load committed allowed CONTEXT_MISSING game_ids and max_excluded_games cap."""
    target = path or ALLOWED_EXCLUSIONS_PATH
    if not target.exists():
        return set(), DEFAULT_MAX_EXCLUDED_GAMES
    raw = json.loads(target.read_text(encoding="utf-8"))
    allowed = {str(g) for g in (raw.get("allowed_game_ids") or []) if g}
    cap = int(raw.get("max_excluded_games", DEFAULT_MAX_EXCLUDED_GAMES))
    return allowed, cap


def evaluate_pricing_context(
    schedule_rows: list[dict],
    journal_records: list[dict],
    now: datetime,
    failed_fetches: list[str] | None = None,
    games_ingested: int | None = None,
    *,
    allowed_exclusions: set[str] | None = None,
    max_excluded_games: int = DEFAULT_MAX_EXCLUDED_GAMES,
    block_on_stale: bool = False,
) -> dict:
    """Build the daily provenance report for the active week's unstarted games."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    overlaid = overlay_journal(schedule_rows, journal_records)
    pricing = active_pricing_rows(overlaid, now)
    missing = []
    excluded = []
    per_game_readiness: dict[str, dict] = {}
    stale = []
    ready_rows = 0
    season = week = None
    if pricing:
        season = int(pricing[0]["season"])
        week = int(pricing[0]["week"])
    for row in pricing:
        gid = str(row.get("game_id") or "")
        blockers = pricing_blockers(row)
        if blockers:
            missing.append({"game_id": gid, "missing": blockers})
            excluded.append({
                "game_id": gid,
                "status": "CONTEXT_MISSING",
                "missing_fields": blockers,
                "ready_for_pricing": False,
            })
            per_game_readiness[gid] = {
                "ready_for_pricing": False,
                "status": "CONTEXT_MISSING",
                "missing_fields": blockers,
            }
        else:
            ready_rows += 1
            per_game_readiness[gid] = {
                "ready_for_pricing": True,
                "status": "READY",
                "missing_fields": [],
            }
        spread_obs = parse_ts(row.get("spread_observed_at"))
        weather_obs = parse_ts(row.get("weather_observed_at"))
        oldest_obs = min((t for t in (spread_obs, weather_obs) if t is not None), default=None)
        if oldest_obs is not None and now - oldest_obs > STALE_AFTER:
            stale.append({
                "game_id": gid,
                "spread_observed_at": row.get("spread_observed_at"),
                "weather_observed_at": row.get("weather_observed_at"),
                "age_hours": round((now - oldest_obs).total_seconds() / 3600, 1),
            })
    if games_ingested is None and season is not None:
        batches = [
            row.get("capture_batch_utc")
            for row in journal_records
            if str(row.get("season")) == str(season) and str(row.get("week")) == str(week)
        ]
        latest = max(batches) if batches else None
        games_ingested = sum(1 for value in batches if value == latest) if latest else 0

    allow_set = allowed_exclusions if allowed_exclusions is not None else set()
    unallowed_excluded = [e["game_id"] for e in excluded if e["game_id"] not in allow_set]
    cap_exceeded = len(excluded) > max_excluded_games

    if not schedule_rows:
        ready = False
    elif not pricing:
        ready = not failed_fetches
    elif failed_fetches or (block_on_stale and stale) or ready_rows == 0:
        ready = False
    elif cap_exceeded or unallowed_excluded:
        ready = False
    else:
        ready = True

    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "ruleset": RULESET,
        "season": season,
        "week": week,
        "games_in_pricing_window": len(pricing),
        "games_ready": ready_rows,
        "games_ingested": games_ingested if games_ingested is not None else 0,
        "missing_timestamps": missing,
        "excluded": excluded,
        "unallowed_excluded": unallowed_excluded,
        "max_excluded_games": max_excluded_games,
        "cap_exceeded": cap_exceeded,
        "per_game_readiness": per_game_readiness,
        "stale_captures": stale,
        "failed_fetches": list(failed_fetches or []),
        "ready_for_pricing": ready,
    }


def apply_context_exclusion_to_slate_rows(
    rows: list[dict],
    report: dict,
) -> list[dict]:
    """Ensure CONTEXT_MISSING games are unpriced with action/model fields zeroed/null."""
    excluded_map = {
        str(e["game_id"]): e for e in (report.get("excluded") or []) if e.get("game_id")
    }
    whole_slate_blocked = not report.get("ready_for_pricing", False)
    out: list[dict] = []
    for raw in rows:
        row = dict(raw)
        gid = str(row.get("game_id") or "")
        if whole_slate_blocked or gid in excluded_map:
            info = excluded_map.get(gid) or {}
            fields = info.get("missing_fields") or []
            row["context_status"] = "CONTEXT_MISSING"
            row["context_missing_fields"] = list(fields)
            row["reason"] = (
                f"CONTEXT_MISSING: {', '.join(fields)}"
                if fields
                else "CONTEXT_MISSING: slate_blocked"
            )
            row["model_prob"] = None
            row["fair_line"] = None
            row["fair_line_yards"] = None
            row["fair_line_rec"] = None
            row["proj_yards"] = None
            row["proj_rec"] = None
            row["mu_rec"] = None
            row["proj_pts"] = None
            row["edge"] = None
            row["edge_pp"] = None
            row["actionable"] = False
            row["actionable_stake"] = 0.0
            row["stake"] = 0.0
            row["qualified"] = False
            row["can_execute"] = False
        else:
            row.setdefault("context_status", "READY")
        out.append(row)
    return out


def csv_pricing_context_status(
    csv_path: Path,
    now: datetime | None = None,
    *,
    allowed_exclusions: set[str] | None = None,
    max_excluded_games: int | None = None,
) -> dict:
    """Evaluate games.csv readiness with per-game allowed CONTEXT_MISSING exclusions."""
    if not csv_path.exists():
        return {"ready_for_pricing": False, "games_ready": 0, "excluded": [], "stale_captures": [], "reason": "missing_games_csv"}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if not set(CONTEXT_FIELDS) <= fields:
            return {"ready_for_pricing": False, "games_ready": 0, "excluded": [], "stale_captures": [], "reason": "missing_context_fields"}
        rows = list(reader)
    moment = now or datetime.now(ET)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ET)
    pricing = active_pricing_rows(rows, moment)
    if not pricing:
        return {"ready_for_pricing": False, "games_ready": 0, "excluded": [], "stale_captures": [], "reason": "no_active_pricing_rows"}

    default_allowed, default_cap = load_allowed_context_exclusions()
    allow_set = allowed_exclusions if allowed_exclusions is not None else default_allowed
    cap = max_excluded_games if max_excluded_games is not None else default_cap

    excluded = []
    stale = []
    ready_rows = 0
    per_game_readiness: dict[str, dict] = {}
    for row in pricing:
        gid = str(row.get("game_id") or "")
        blockers = pricing_blockers(row)
        if blockers:
            excluded.append({
                "game_id": gid,
                "status": "CONTEXT_MISSING",
                "missing_fields": blockers,
                "ready_for_pricing": False,
            })
            per_game_readiness[gid] = {
                "ready_for_pricing": False,
                "status": "CONTEXT_MISSING",
                "missing_fields": blockers,
            }
        else:
            ready_rows += 1
            per_game_readiness[gid] = {
                "ready_for_pricing": True,
                "status": "READY",
                "missing_fields": [],
            }
        observed = parse_ts(row.get("spread_observed_at"))
        if observed is not None and moment - observed > STALE_AFTER:
            stale.append({"game_id": gid, "spread_observed_at": row.get("spread_observed_at")})

    unallowed_excluded = [e["game_id"] for e in excluded if e["game_id"] not in allow_set]
    cap_exceeded = len(excluded) > cap
    ready = bool(ready_rows > 0 and not stale and not cap_exceeded and not unallowed_excluded)
    return {
        "ready_for_pricing": ready,
        "games_in_pricing_window": len(pricing),
        "games_ready": ready_rows,
        "excluded": excluded,
        "unallowed_excluded": unallowed_excluded,
        "max_excluded_games": cap,
        "cap_exceeded": cap_exceeded,
        "stale_captures": stale,
        "per_game_readiness": per_game_readiness,
    }


def csv_pricing_context_ready(
    csv_path: Path,
    now: datetime | None = None,
    *,
    allowed_exclusions: set[str] | None = None,
    max_excluded_games: int | None = None,
) -> bool:
    """True when unstarted games pass cutoff gate (allowing configured CONTEXT_MISSING exclusions)."""
    return bool(
        csv_pricing_context_status(
            csv_path,
            now,
            allowed_exclusions=allowed_exclusions,
            max_excluded_games=max_excluded_games,
        ).get("ready_for_pricing")
    )


def write_provenance_report(report: dict, report_path: Path | None = None) -> Path:
    path = report_path or REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def format_provenance_report(report: dict) -> str:
    season = report.get("season")
    week = report.get("week")
    header = f"game-context provenance {season} week {week}"
    lines = [
        header,
        f"  games ingested (latest batch): {report.get('games_ingested', 0)}",
        f"  pricing window: {report.get('games_in_pricing_window', 0)} "
        f"({report.get('games_ready', 0)} ready)",
        f"  missing timestamps: {len(report.get('missing_timestamps') or [])}",
        f"  stale captures: {len(report.get('stale_captures') or [])}",
        f"  failed fetches: {len(report.get('failed_fetches') or [])}",
        f"  pricing: {'READY' if report.get('ready_for_pricing') else 'BLOCKED'}",
    ]
    for item in report.get("missing_timestamps") or []:
        lines.append(f"  - {item.get('game_id')}: {', '.join(item.get('missing') or [])}")
    for item in report.get("excluded") or []:
        lines.append(f"  - excluded ({item.get('status')}): {item.get('game_id')} [{', '.join(item.get('missing_fields') or [])}]")
    for item in report.get("failed_fetches") or []:
        lines.append(f"  - fetch failed: {item}")
    for item in report.get("stale_captures") or []:
        lines.append(f"  - stale {item.get('game_id')} captured {item.get('spread_observed_at')}")
    return "\n".join(lines)


def _failure_report(season: int, week: int, message: str, games_ingested: int = 0) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "generated_at": now.isoformat(),
        "ruleset": RULESET,
        "season": season,
        "week": week,
        "games_in_pricing_window": None,
        "games_ready": 0,
        "games_ingested": games_ingested,
        "missing_timestamps": [],
        "stale_captures": [],
        "failed_fetches": [message],
        "ready_for_pricing": False,
    }


def ingest_week(
    season: int,
    week: int,
    *,
    fetch_weather: bool = True,
    dry_run: bool = False,
    games_csv: Path | None = None,
    journal_path: Path | None = None,
    raw_dir: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    """Capture one week. On HTTP, JSON, or partial-slate failure, leave the feeds untouched."""
    csv_path = games_csv or GAMES_CSV_PATH
    journal = journal_path or CAPTURES_LOG
    report_dest = report_path or REPORT_PATH
    if not is_prospective(season, week):
        report = _failure_report(
            season,
            week,
            f"refusing to timestamp historical games before {PROSPECTIVE_START[0]} week {PROSPECTIVE_START[1]}",
        )
        if not dry_run:
            write_provenance_report(report, report_dest)
        raise ValueError(report["failed_fetches"][0])

    try:
        data, raw_bytes, fetch_time = fetch_espn_week_data(season, week)
        expected = scheduled_game_ids(csv_path, season, week)
        records = parse_espn_games(
            data,
            season,
            week,
            fetch_time,
            fetch_weather=fetch_weather,
            expected_game_ids=expected,
        )
    except Exception as exc:
        report = _failure_report(season, week, str(exc))
        if not dry_run:
            write_provenance_report(report, report_dest)
        raise

    if dry_run:
        report = _failure_report(season, week, "dry-run")
        report["failed_fetches"] = []
        report["games_ingested"] = len(records)
        report["ready_for_pricing"] = False
        return {"records": records, "report": report, "fetch_time": fetch_time}

    raw_rel = append_raw_capture(raw_bytes, season, week, fetch_time, raw_dir=raw_dir)
    for record in records:
        payload = record.get("_weather_payload")
        if payload and record.get("weather_observed_at"):
            record["weather_raw_payload"] = append_weather_payload(
                payload,
                season,
                week,
                record["game_id"],
                record["weather_observed_at"],
                raw_dir=raw_dir,
            )
    if records:
        append_capture_records(records, raw_rel, fetch_time, journal_path=journal)
    # Reapply the whole journal so a newer post-cutoff batch cannot be the
    # row that lands in games.csv, and so an older real weather capture is
    # kept when this batch has no weather.
    if csv_path.exists():
        reapply_captures_to_games_csv(csv_path, journal)

    schedule = load_schedule_rows(csv_path) if csv_path.exists() else []
    now = datetime.now(ET)
    allowed_exclusions, max_excluded = load_allowed_context_exclusions()
    report = evaluate_pricing_context(
        schedule,
        load_journal(journal),
        now,
        failed_fetches=[],
        games_ingested=len(records),
        allowed_exclusions=allowed_exclusions,
        max_excluded_games=max_excluded,
        block_on_stale=True,
    )
    report["raw_source_payload"] = raw_rel
    write_provenance_report(report, report_dest)
    return {"records": records, "report": report, "fetch_time": fetch_time, "raw_source_payload": raw_rel}


def resolve_active_season_week(
    csv_path: Path | None = None, now: datetime | None = None
) -> tuple[int, int] | None:
    path = csv_path or GAMES_CSV_PATH
    if not path.exists():
        return None
    rows = load_schedule_rows(path)
    moment = now or datetime.now(ET)
    pricing = active_pricing_rows(rows, moment)
    if pricing:
        return int(pricing[0]["season"]), int(pricing[0]["week"])
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest live ESPN game context with strict provenance")
    parser.add_argument("--season", type=int, default=2026, help="NFL season year (default 2026)")
    parser.add_argument("--week", type=int, default=None, help="NFL week to capture (e.g. 10; default: auto-resolve)")
    parser.add_argument("--no-weather", action="store_true", help="Skip summary weather fetches")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse without writing the journal")
    args = parser.parse_args(argv)

    season = args.season
    week = args.week
    if week is None:
        resolved = resolve_active_season_week()
        if resolved is None:
            if not GAMES_CSV_PATH.exists():
                GAMES_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(NFLVERSE_GAMES_URL, GAMES_CSV_PATH)
                resolved = resolve_active_season_week()
        if resolved is None:
            print("[espn-context] FAIL CLOSED: cannot resolve active week from schedule; specify --week", file=sys.stderr)
            return 1
        season, week = resolved

    print(f"[espn-context] Fetching live NFL Week {week} ({season})...")
    try:
        result = ingest_week(
            season,
            week,
            fetch_weather=not args.no_weather,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"[espn-context] FAIL CLOSED: {exc}", file=sys.stderr)
        print(format_provenance_report(_failure_report(season, week, str(exc))))
        return 1

    records = result["records"]
    print(f"[espn-context] Parsed {len(records)} pregame games at {result['fetch_time']}")
    if args.dry_run:
        if records:
            sample = {key: value for key, value in records[0].items() if key != "_weather_payload"}
            print(json.dumps(sample, indent=2))
        return 0
    print(format_provenance_report(result["report"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
