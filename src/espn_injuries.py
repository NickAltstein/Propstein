"""ESPN injury report: same-day availability for NFL and NBA.

The official feeds the models used miss roster moves for days: nflverse's
injury report only carries practice/game designations (Puka Nacua stayed
"Did Not Participate" for two days after the Rams put him on IR on Nov 12,
2026, so the slate kept projecting him), and the NBA model had no injury
input at all (four of last night's 20 rebound/assist rows were players who
did not play). ESPN's public injuries endpoint lists IR and Out the same day
and is reachable from GitHub Actions.

Players are identified by display name + team (ESPN ships no athlete id
here), so matching is name-normalized and team-scoped; ambiguous names are
skipped, never guessed.

    python3 src/espn_injuries.py nfl  > data/espn_injuries_nfl.json
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/injuries"
PATHS = {"nfl": "football/nfl", "nba": "basketball/nba"}
#: ESPN abbreviations -> the codes each project uses (nflverse / NBA model).
TEAM_ALIASES = {
    "nfl": {"LAR": "LA", "WSH": "WAS"},
    "nba": {"NY": "NYK", "GS": "GSW", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"},
}
#: Statuses that mean "will not play" -> excluded from projections.
UNAVAILABLE = {"out", "injured reserve", "doubtful", "suspension", "suspended",
               "physically unable to perform", "pup", "non-football injury"}
#: Statuses that mean "may not play / limited" -> kept, flagged.
UNCERTAIN = {"questionable", "day-to-day", "game time decision", "probable"}


def name_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def fetch(sport: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(URL.format(path=PATHS[sport]),
                                 headers={"Accept": "application/json", "User-Agent": "propstein-data/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return normalize(payload, sport)


def normalize(payload: dict, sport: str) -> dict:
    aliases = TEAM_ALIASES[sport]
    players = []
    for team in payload.get("injuries", []):
        for entry in team.get("injuries", []):
            athlete = entry.get("athlete") or {}
            abbr = ((athlete.get("team") or {}).get("abbreviation") or "").upper()
            status = str(entry.get("status") or "").strip()
            name = athlete.get("displayName") or ""
            if not (abbr and status and name):
                continue
            players.append({
                "player_name": name,
                "team": aliases.get(abbr, abbr),
                "status": status,
                "unavailable": status.lower() in UNAVAILABLE,
                "uncertain": status.lower() in UNCERTAIN,
                "reported_at": entry.get("date"),
                "comment": (entry.get("shortComment") or "")[:240],
            })
    return {
        "source": "ESPN injuries API",
        "source_url": URL.format(path=PATHS[sport]),
        "sport": sport,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "players": players,
    }


def match(report: dict | None, roster: list[dict], name_field: str, team_field: str = "team") -> dict[int, dict]:
    """Map roster row index -> ESPN entry by (normalized name, team). Ambiguous keys are skipped."""
    if not report:
        return {}
    by_key: dict[tuple[str, str], list[dict]] = {}
    for p in report.get("players", []):
        by_key.setdefault((name_key(p["player_name"]), p["team"]), []).append(p)
    out = {}
    for i, row in enumerate(roster):
        hits = by_key.get((name_key(row.get(name_field, "")), str(row.get(team_field, "")).upper()))
        if hits and len(hits) == 1:
            out[i] = hits[0]
    return out


def is_fresh(report: dict | None, max_age_hours: float = 36.0, now: datetime | None = None) -> bool:
    try:
        fetched = datetime.fromisoformat(str(report["fetched_at"]).replace("Z", "+00:00"))
    except (TypeError, KeyError, ValueError):
        return False
    now = now or datetime.now(timezone.utc)
    return 0 <= (now - fetched).total_seconds() / 3600 <= max_age_hours


if __name__ == "__main__":
    print(json.dumps(fetch(sys.argv[1] if len(sys.argv) > 1 else "nfl"), indent=1))
