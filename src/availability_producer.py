"""Scheduled NFL availability and IR producer (feeds #195 via append-only JSONL).

Polls two sources plus human-attested overrides:
  1. ESPN NFL injuries endpoint (`espn_injuries`, same-day IR/Out/Questionable)
  2. nflverse roster status (`nflverse_roster`, durable RES/INA status)
  3. Human-attested override (`viewer/official_inactives_{season}_w{week}.json`)

Writes hash-chained, idempotent events to `data/availability_events.jsonl` and a
compact viewer-safe summary to `viewer/availability_report.json`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import urllib.request

from src.crypto_utils import canonical_json_sha256

ESPN_NFL_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
NFLVERSE_ROSTER_URL = "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{season}.parquet"
NFLVERSE_PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.parquet"

NFL_TEAM_ALIASES = {"LAR": "LA", "WSH": "WAS"}

STICKY_EXCLUDED_CATEGORIES = frozenset({"OUT", "IR", "DOUBTFUL", "INACTIVE", "REPORTED_OUT"})

CATEGORY_RANK = {
    "IR": 8,
    "INACTIVE": 7,
    "OUT": 6,
    "REPORTED_OUT": 5,
    "DOUBTFUL": 4,
    "QUESTIONABLE": 3,
    "LIMITED": 2,
    "ACTIVE": 1,
    "UNKNOWN": 0,
}

DEFAULT_MAX_SOURCE_AGE_SECONDS = 36 * 3600
DEFAULT_MIN_EXCLUSION_AGE_HOURS = 48.0
DEFAULT_REVIEW_AGE_DAYS = 7


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _line_sha256(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def extract_espn_id(athlete: Mapping[str, Any]) -> str | None:
    """Extract numeric ESPN athlete ID from athlete.links or athlete.id."""
    raw_id = athlete.get("id")
    if raw_id is not None and str(raw_id).strip().isdigit():
        return str(raw_id).strip()
    for link in athlete.get("links") or ():
        if not isinstance(link, Mapping):
            continue
        href = str(link.get("href") or "")
        m = re.search(r"/id/(\d+)", href)
        if m:
            return m.group(1)
    return None


def build_crosswalk(
    roster_rows: Sequence[Mapping[str, Any]],
    player_rows: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, str], set[str]]:
    """Build strict espn_id -> gsis_id map from nflverse roster/player tables.

    Returns `(espn_to_gsis, ambiguous_espn_ids)`. Ambiguous IDs are never guessed.
    """
    candidates: dict[str, set[str]] = {}
    for row in (*roster_rows, *player_rows):
        eid_raw = row.get("espn_id")
        gid_raw = row.get("gsis_id")
        if eid_raw is None or gid_raw is None:
            continue
        eid = str(eid_raw).split(".")[0].strip()
        gid = str(gid_raw).strip()
        if not eid or eid.lower() == "nan" or not gid or gid.lower() == "nan":
            continue
        candidates.setdefault(eid, set()).add(gid)

    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for eid, gids in candidates.items():
        if len(gids) == 1:
            mapping[eid] = next(iter(gids))
        else:
            ambiguous.add(eid)
    return mapping, ambiguous


def categorize_espn_status(status: Any) -> str:
    s = str(status or "").strip().lower()
    if s in {"injured reserve", "ir", "reserve/injured"}:
        return "IR"
    if s in {"inactive", "ina"}:
        return "INACTIVE"
    if s in {"out", "suspension", "suspended", "physically unable to perform", "pup", "non-football injury"}:
        return "OUT"
    if s == "doubtful":
        return "DOUBTFUL"
    if s in {"questionable", "game time decision", "day-to-day"}:
        return "QUESTIONABLE"
    if s in {"limited", "limited participation in practice", "probable"}:
        return "LIMITED"
    if s == "active":
        return "ACTIVE"
    return "UNKNOWN"


def categorize_roster_status(status: Any, status_abbr: Any = None) -> str:
    s = str(status or "").strip().upper()
    abbr = str(status_abbr or "").strip().upper()
    if s in {"RES", "IR"} or abbr.startswith("R"):
        return "IR"
    if s in {"INA", "INACTIVE"}:
        return "INACTIVE"
    if s in {"PUP", "SUS", "NFI", "OUT"}:
        return "OUT"
    if s in {"ACT", "ACTIVE"}:
        return "ACTIVE"
    return "UNKNOWN"


def load_events_jsonl(jsonl_path: Path) -> tuple[list[dict[str, Any]], str, bool]:
    """Load existing JSONL events, returning `(events, last_line_hash, chain_valid)`."""
    if not jsonl_path.exists():
        return [], "GENESIS", True
    events: list[dict[str, Any]] = []
    prev_hash = "GENESIS"
    chain_valid = True
    for raw_line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        ev = json.loads(line)
        if ev.get("prev_event_hash") != prev_hash:
            chain_valid = False
        prev_hash = _line_sha256(line)
        events.append(ev)
    return events, prev_hash, chain_valid


def current_sticky_state(events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Compute current per-player state from event history (sticky until explicit ACTIVE)."""
    state: dict[str, dict[str, Any]] = {}
    for ev in events:
        pid = str(ev.get("player_id") or "").strip()
        if not pid:
            continue
        cat = str(ev.get("category") or ev.get("new_status") or "UNKNOWN").upper()
        excluded = cat in STICKY_EXCLUDED_CATEGORIES
        prev = state.get(pid)
        first_excluded = (
            (prev.get("first_excluded_at") or ev.get("observed_at"))
            if (excluded and prev and prev.get("excluded"))
            else (ev.get("observed_at") if excluded else None)
        )
        state[pid] = {
            "player_id": pid,
            "team": ev.get("team"),
            "category": cat,
            "new_status": ev.get("new_status"),
            "source": ev.get("source"),
            "observed_at": ev.get("observed_at"),
            "first_excluded_at": first_excluded,
            "excluded": excluded,
        }
    return state


def fetch_espn_nfl_injuries(url: str = ESPN_NFL_INJURIES_URL, timeout: float = 20.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "propstein-availability-producer/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_nflverse_parquet_rows(url: str, columns: Sequence[str], timeout: float = 30.0) -> list[dict[str, Any]]:
    import pandas as pd

    req = urllib.request.Request(url, headers={"User-Agent": "propstein-availability-producer/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        df = pd.read_parquet(io.BytesIO(resp.read()))
    keep = [c for c in columns if c in df.columns]
    return df[keep].to_dict("records")


def _compute_latency_seconds(source_published_at: str | None, observed_dt: datetime) -> float | None:
    pub_dt = _parse_utc(source_published_at)
    if pub_dt is None:
        return None
    return round(max(0.0, (observed_dt - pub_dt).total_seconds()), 3)


def _make_event(
    *,
    player_id: str,
    team: str,
    previous_status: str | None,
    new_status: str,
    category: str,
    source: str,
    source_url: str,
    source_published_at: str | None,
    observed_dt: datetime,
    raw_payload_sha256: str,
    prev_event_hash: str,
) -> tuple[dict[str, Any], str]:
    observed_iso = observed_dt.isoformat()
    event_id = canonical_json_sha256({
        "player_id": player_id,
        "previous_status": previous_status,
        "new_status": new_status,
        "category": category,
        "source": source,
        "raw_payload_sha256": raw_payload_sha256,
    })[:24]
    ev = {
        "event_id": event_id,
        "player_id": player_id,
        "team": team,
        "previous_status": previous_status,
        "new_status": new_status,
        "category": category,
        "source": source,
        "source_url": source_url,
        "source_published_at": source_published_at,
        "observed_at": observed_iso,
        "raw_payload_sha256": raw_payload_sha256,
        "latency_seconds": _compute_latency_seconds(source_published_at, observed_dt),
        "prev_event_hash": prev_event_hash,
    }
    line = json.dumps(ev, sort_keys=True, separators=(",", ":"))
    return ev, line


def run_availability_poll(
    *,
    jsonl_path: Path,
    report_path: Path,
    espn_payload: Mapping[str, Any] | None = None,
    espn_error: str | None = None,
    roster_rows: Sequence[Mapping[str, Any]] | None = None,
    roster_error: str | None = None,
    player_rows: Sequence[Mapping[str, Any]] = (),
    override_paths: Sequence[Path] = (),
    now: datetime | None = None,
    max_source_age_seconds: int = DEFAULT_MAX_SOURCE_AGE_SECONDS,
    min_exclusion_age_hours: float = DEFAULT_MIN_EXCLUSION_AGE_HOURS,
    slate_end_utc: datetime | None = None,
    review_age_days: int = DEFAULT_REVIEW_AGE_DAYS,
) -> dict[str, Any]:
    """Run one idempotent availability poll, append new events, and write compact report."""
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    existing_events, prev_hash, chain_valid = load_events_jsonl(jsonl_path)
    seen_event_ids = {str(e.get("event_id")) for e in existing_events}
    prior_state = current_sticky_state(existing_events)

    prior_report: dict[str, Any] = {}
    if report_path.exists():
        try:
            prior_report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            prior_report = {}

    roster_non_act_observed: set[str] = {
        str(pid) for pid in (prior_report.get("roster_non_act_observed") or ())
    }
    last_poll = dict(prior_report.get("last_successful_poll") or {})
    stale_reasons: list[str] = []
    if not chain_valid:
        stale_reasons.append("EVENT_CHAIN_BROKEN")
    if espn_error:
        stale_reasons.append(f"ESPN_FETCH_FAILED:{espn_error}")
    if roster_error:
        stale_reasons.append(f"ROSTER_FETCH_FAILED:{roster_error}")

    if espn_payload is not None and not espn_error:
        ts_dt = _parse_utc(espn_payload.get("timestamp"))
        if ts_dt is not None and (now_dt - ts_dt).total_seconds() > max_source_age_seconds:
            stale_reasons.append("ESPN_PAYLOAD_STALE")
        else:
            last_poll["espn_injuries"] = now_dt.isoformat()
    if roster_rows is not None and not roster_error:
        last_poll["nflverse_roster"] = now_dt.isoformat()

    espn_to_gsis, ambiguous_espn_ids = build_crosswalk(roster_rows or (), player_rows)

    # Collect per-player observations from each source: player_id -> list[obs]
    observations: dict[str, list[dict[str, Any]]] = {}
    unmatched: list[dict[str, Any]] = []
    espn_matched_count = 0
    espn_total_count = 0

    if espn_payload is not None and not espn_error:
        payload_hash = canonical_json_sha256(espn_payload)
        for team_block in espn_payload.get("injuries") or ():
            for entry in team_block.get("injuries") or ():
                espn_total_count += 1
                athlete = entry.get("athlete") or {}
                abbr = ((athlete.get("team") or {}).get("abbreviation") or "").upper()
                team = NFL_TEAM_ALIASES.get(abbr, abbr)
                status = str(entry.get("status") or "").strip()
                name = str(athlete.get("displayName") or "").strip()
                eid = extract_espn_id(athlete)

                if eid is None or eid in ambiguous_espn_ids or eid not in espn_to_gsis:
                    unmatched.append({
                        "player_name": name,
                        "team": team,
                        "status": status,
                        "espn_id": eid,
                        "reason": "AMBIGUOUS_ESPN_ID" if eid in ambiguous_espn_ids else "UNMATCHED_ESPN_ID",
                    })
                    continue

                espn_matched_count += 1
                pid = espn_to_gsis[eid]
                cat = categorize_espn_status(status)
                observations.setdefault(pid, []).append({
                    "player_id": pid,
                    "team": team,
                    "new_status": status,
                    "category": cat,
                    "source": "espn_injuries",
                    "source_url": ESPN_NFL_INJURIES_URL,
                    "source_published_at": entry.get("date"),
                    "raw_payload_sha256": payload_hash,
                })

    if roster_rows is not None and not roster_error:
        roster_hash = canonical_json_sha256(list(roster_rows))
        for row in roster_rows:
            pid = str(row.get("gsis_id") or "").strip()
            if not pid or pid.lower() == "nan":
                continue
            team = str(row.get("team") or "").strip().upper()
            r_status = str(row.get("status") or "").strip()
            r_abbr = str(row.get("status_description_abbr") or "").strip()
            cat = categorize_roster_status(r_status, r_abbr)
            if cat == "UNKNOWN":
                continue
            observations.setdefault(pid, []).append({
                "player_id": pid,
                "team": team,
                "new_status": f"{r_status}:{r_abbr}" if r_abbr else r_status,
                "category": cat,
                "source": "nflverse_roster",
                "source_url": NFLVERSE_ROSTER_URL.format(season=2026),
                "source_published_at": None,
                "raw_payload_sha256": roster_hash,
            })
            if cat in STICKY_EXCLUDED_CATEGORIES:
                roster_non_act_observed.add(pid)

    overrides_applied: list[dict[str, Any]] = []
    for ov_path in override_paths:
        if not ov_path.exists():
            continue
        ov_data = json.loads(ov_path.read_text(encoding="utf-8"))
        src_url = str(ov_data.get("source_url") or "").strip()
        if not src_url:
            continue  # Fail closed: human override requires source_url
        ov_hash = canonical_json_sha256(ov_data)
        scope = str(ov_data.get("scope") or "").lower()
        is_official = bool(ov_data.get("official")) or (
            "official nfl inactive" in scope and "not an official" not in scope
        )
        cat = "INACTIVE" if is_official else "REPORTED_OUT"
        pub_at = ov_data.get("published_at")
        for p in ov_data.get("players") or ():
            pid = str(p.get("player_id") or "").strip()
            if not pid:
                continue
            team = str(p.get("team") or "").strip().upper()
            observations.setdefault(pid, []).append({
                "player_id": pid,
                "team": team,
                "new_status": cat,
                "category": cat,
                "source": "human_override",
                "source_url": src_url,
                "source_published_at": pub_at,
                "raw_payload_sha256": ov_hash,
            })
            overrides_applied.append({
                "file": ov_path.name,
                "player_id": pid,
                "category": cat,
                "source_url": src_url,
            })
        for p in ov_data.get("active_players") or ():
            pid = str(p.get("player_id") or "").strip()
            if not pid:
                continue
            team = str(p.get("team") or "").strip().upper()
            observations.setdefault(pid, []).append({
                "player_id": pid,
                "team": team,
                "new_status": "ACTIVE",
                "category": "ACTIVE",
                "source": "human_override",
                "source_url": src_url,
                "source_published_at": pub_at,
                "raw_payload_sha256": ov_hash,
            })
            overrides_applied.append({
                "file": ov_path.name,
                "player_id": pid,
                "category": "ACTIVE",
                "source_url": src_url,
            })

    # Resolve disagreements per player: most restrictive category wins.
    disagreements: list[dict[str, Any]] = []
    new_lines: list[str] = []
    new_events: list[dict[str, Any]] = []

    fetch_degraded = bool(espn_error or roster_error)
    for pid, obs_list in sorted(observations.items()):
        cats = {o["category"] for o in obs_list}
        if len(cats) > 1:
            disagreements.append({
                "player_id": pid,
                "sources": [{"source": o["source"], "category": o["category"]} for o in obs_list],
            })
        # Most restrictive wins; prefer espn_injuries / human_override on rank ties
        source_tiebreak = {"human_override": 2, "espn_injuries": 1, "nflverse_roster": 0}
        winner = max(
            obs_list,
            key=lambda o: (CATEGORY_RANK.get(o["category"], 0), source_tiebreak.get(o["source"], 0)),
        )

        prev = prior_state.get(pid)
        prev_cat = prev["category"] if prev else None
        prev_status = prev["new_status"] if prev else None

        # Fail closed: never clear a sticky exclusion if any source fetch failed.
        if fetch_degraded and prev and prev["excluded"] and winner["category"] == "ACTIVE":
            continue
        # Positive-evidence-only clearing rule with 48h minimum exclusion age:
        # Never clear on absence from a feed or on an unchanged stale ACT.
        if prev and prev["excluded"] and winner["category"] == "ACTIVE":
            first_dt = _parse_utc(prev.get("first_excluded_at") or prev.get("observed_at"))
            if first_dt is not None and (now_dt - first_dt).total_seconds() < float(min_exclusion_age_hours) * 3600.0:
                continue
            if prev["source"] == "human_override":
                slate_expired = slate_end_utc is not None and now_dt >= slate_end_utc.astimezone(timezone.utc)
                if winner["source"] != "human_override" and not slate_expired:
                    continue
            else:
                roster_transitioned = (
                    winner["source"] == "nflverse_roster"
                    and (prev["source"] == "nflverse_roster" or pid in roster_non_act_observed)
                )
                if winner["source"] not in {"espn_injuries", "human_override"} and not roster_transitioned:
                    continue
            roster_non_act_observed.discard(pid)

        # Only emit when category changes (or first time seeing a non-ACTIVE/UNKNOWN state).
        if prev is None and winner["category"] in {"ACTIVE", "UNKNOWN"}:
            continue
        if prev is not None and prev_cat == winner["category"]:
            continue

        ev, line = _make_event(
            player_id=pid,
            team=winner["team"],
            previous_status=prev_status,
            new_status=winner["new_status"],
            category=winner["category"],
            source=winner["source"],
            source_url=winner["source_url"],
            source_published_at=winner["source_published_at"],
            observed_dt=now_dt,
            raw_payload_sha256=winner["raw_payload_sha256"],
            prev_event_hash=prev_hash,
        )
        if ev["event_id"] in seen_event_ids:
            continue
        seen_event_ids.add(ev["event_id"])
        prev_hash = _line_sha256(line)
        new_lines.append(line)
        new_events.append(ev)
        excluded_now = ev["category"] in STICKY_EXCLUDED_CATEGORIES
        first_excluded = (
            (prev.get("first_excluded_at") or ev["observed_at"])
            if (excluded_now and prev and prev.get("excluded"))
            else (ev["observed_at"] if excluded_now else None)
        )
        prior_state[pid] = {
            "player_id": pid,
            "team": ev["team"],
            "category": ev["category"],
            "new_status": ev["new_status"],
            "source": ev["source"],
            "observed_at": ev["observed_at"],
            "first_excluded_at": first_excluded,
            "excluded": excluded_now,
        }

    if new_lines:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as fh:
            for line in new_lines:
                fh.write(line + "\n")

    counts_by_category: dict[str, int] = {}
    excluded_by_source: dict[str, int] = {}
    excluded_ids: list[str] = []
    stale_exclusions_for_review: list[dict[str, Any]] = []
    for pid, st in sorted(prior_state.items()):
        cat = st["category"]
        counts_by_category[cat] = counts_by_category.get(cat, 0) + 1
        if st["excluded"]:
            excluded_ids.append(pid)
            src = str(st.get("source") or "unknown")
            excluded_by_source[src] = excluded_by_source.get(src, 0) + 1
            first_dt = _parse_utc(st.get("first_excluded_at") or st.get("observed_at"))
            if first_dt is not None:
                age_days = (now_dt - first_dt).total_seconds() / 86400.0
                if age_days >= float(review_age_days):
                    stale_exclusions_for_review.append({
                        "player_id": pid,
                        "category": cat,
                        "source": src,
                        "first_excluded_at": first_dt.isoformat(),
                        "age_days": round(age_days, 2),
                    })

    report = {
        "schema": "propwire.availability_report.v1",
        "generated_at": now_dt.isoformat(),
        "stale_context": bool(stale_reasons),
        "stale_reasons": stale_reasons,
        "new_events_count": len(new_events),
        "total_events_count": len(existing_events) + len(new_events),
        "counts_by_category": counts_by_category,
        "excluded_by_source": excluded_by_source,
        "sticky_excluded_count": len(excluded_ids),
        "excluded_player_ids": excluded_ids,
        "roster_non_act_observed": sorted(roster_non_act_observed),
        "stale_exclusions_for_review": stale_exclusions_for_review,
        "disagreements": disagreements,
        "unmatched": unmatched,
        "crosswalk_stats": {
            "espn_total": espn_total_count,
            "espn_matched": espn_matched_count,
            "match_rate": round(espn_matched_count / espn_total_count, 4) if espn_total_count else 1.0,
        },
        "overrides_applied": overrides_applied,
        "last_successful_poll": last_poll,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll NFL availability sources into append-only JSONL.")
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--week", type=int, default=11)
    parser.add_argument("--jsonl", type=Path, default=Path("data/availability_events.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("viewer/availability_report.json"))
    args = parser.parse_args()

    espn_payload: dict[str, Any] | None = None
    espn_error: str | None = None
    try:
        espn_payload = fetch_espn_nfl_injuries()
    except Exception as exc:
        espn_error = str(exc)

    roster_rows: list[dict[str, Any]] | None = None
    roster_error: str | None = None
    player_rows: list[dict[str, Any]] = []
    try:
        roster_rows = fetch_nflverse_parquet_rows(
            NFLVERSE_ROSTER_URL.format(season=args.season),
            columns=("gsis_id", "espn_id", "team", "status", "status_description_abbr", "full_name"),
        )
        player_rows = fetch_nflverse_parquet_rows(
            NFLVERSE_PLAYERS_URL,
            columns=("gsis_id", "espn_id", "display_name", "latest_team"),
        )
    except Exception as exc:
        roster_error = str(exc)

    override_path = Path(f"viewer/official_inactives_{args.season}_w{args.week}.json")
    report = run_availability_poll(
        jsonl_path=args.jsonl,
        report_path=args.report,
        espn_payload=espn_payload,
        espn_error=espn_error,
        roster_rows=roster_rows,
        roster_error=roster_error,
        player_rows=player_rows,
        override_paths=[override_path],
    )
    print(json.dumps({
        "new_events_count": report["new_events_count"],
        "sticky_excluded_count": report["sticky_excluded_count"],
        "stale_context": report["stale_context"],
        "unmatched_count": len(report["unmatched"]),
    }))


if __name__ == "__main__":
    main()
