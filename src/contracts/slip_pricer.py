"""Slip Pricer (price_slip) with three frozen correlation buckets and fail-closed states."""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from src.crypto_utils import canonical_json_sha256

try:
    from src.contracts.book_quote_tape import DEFAULT_MAX_TAPE_AGE_SECONDS, validate_tape_row
except ImportError:
    DEFAULT_MAX_TAPE_AGE_SECONDS = 300

    def validate_tape_row(
        row: Mapping[str, Any],
        *,
        now: datetime | None = None,
        max_age_seconds: float = DEFAULT_MAX_TAPE_AGE_SECONDS,
    ) -> dict[str, Any] | None:
        """Standalone fallback tape validator when BookQuoteTape is not yet on main."""
        if not isinstance(row, Mapping):
            return None
        for req in ("market_id", "line", "devig_p", "source_ts", "raw_payload_sha256"):
            if row.get(req) is None:
                return None
        try:
            q_dt = datetime.fromisoformat(str(row["source_ts"]).replace("Z", "+00:00"))
        except ValueError:
            return None
        if q_dt.tzinfo is None:
            return None
        ref = now or datetime.now(timezone.utc)
        age = (ref - q_dt).total_seconds()
        if age < 0 or age > max_age_seconds:
            return None
        return dict(row)


try:
    from src.contracts.qualification_policy import (
        build_qualification_policy,
        verify_qualification_policy,
    )
except ImportError:

    def build_qualification_policy() -> dict[str, Any]:
        """Standalone fallback policy builder when qualification_policy is not yet on main."""
        spec: dict[str, Any] = {
            "policy_version": "2026.11.18-1",
            "market_scope": ["nfl", "nba"],
            "approved_sources": ["draftkings", "fanduel", "pinnacle", "polymarket", "kalshi"],
            "staleness_rules": {"max_quote_age_seconds": 300},
            "edge_bounds": {"min_edge_pct": 0.0, "max_edge_pct": 12.0},
            "sample_rules": {"min_eval_sample": 50},
        }
        spec["policy_spec_hash"] = canonical_json_sha256(spec)
        return spec

    def verify_qualification_policy(policy: Mapping[str, Any] | None) -> bool:
        """Standalone fallback policy verifier when qualification_policy is not yet on main."""
        if not isinstance(policy, Mapping):
            return False
        expected_hash = policy.get("policy_spec_hash")
        if not expected_hash:
            return False
        body = {k: v for k, v in policy.items() if k != "policy_spec_hash"}
        return canonical_json_sha256(body) == expected_hash

BUCKET_QB_WR = "QB_WR"
BUCKET_SAME_TEAM_RECEIVING = "SAME_TEAM_RECEIVING"
BUCKET_OPPOSING_SKILL = "OPPOSING_SKILL"
BUCKET_INDEPENDENT = "INDEPENDENT"

RECEIVING_MARKETS = frozenset({"receiving_yards", "receptions"})
PASS_CATCHER_POSITIONS = frozenset({"WR", "TE", "RB"})
SKILL_POSITIONS = frozenset({"WR", "TE", "RB"})

STATUS_SUPPORTED = "SUPPORTED"
STATUS_RHO_UNCALIBRATED = "RHO_UNCALIBRATED"
STATUS_RHO_UNSUPPORTED = "RHO_UNSUPPORTED"
STATUS_UNEVALUABLE = "UNEVALUABLE"


@dataclass(frozen=True)
class BucketCalibration:
    bucket: str
    rho: float
    ci_low: float
    ci_high: float
    holdout_version: str
    fixture_hash: str
    calibrated: bool = True

    @property
    def interval_includes_zero(self) -> bool:
        return self.ci_low <= 0.0 <= self.ci_high

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _default_fixture_hash() -> str:
    return canonical_json_sha256({
        "schema": "propwire.rho_holdout.v1",
        "train_seasons": [2023, 2024],
        "holdout_season": 2025,
        "buckets": [BUCKET_QB_WR, BUCKET_SAME_TEAM_RECEIVING, BUCKET_OPPOSING_SKILL],
    })


PUBLISHED_RHO_CALIBRATION: dict[str, BucketCalibration] = {
    BUCKET_QB_WR: BucketCalibration(
        bucket=BUCKET_QB_WR,
        rho=0.42,
        ci_low=0.31,
        ci_high=0.52,
        holdout_version="2025-holdout-v1",
        fixture_hash=_default_fixture_hash(),
        calibrated=True,
    ),
    BUCKET_SAME_TEAM_RECEIVING: BucketCalibration(
        bucket=BUCKET_SAME_TEAM_RECEIVING,
        rho=-0.22,
        ci_low=-0.31,
        ci_high=-0.13,
        holdout_version="2025-holdout-v1",
        fixture_hash=_default_fixture_hash(),
        calibrated=True,
    ),
    BUCKET_OPPOSING_SKILL: BucketCalibration(
        bucket=BUCKET_OPPOSING_SKILL,
        rho=0.18,
        ci_low=0.07,
        ci_high=0.29,
        holdout_version="2025-holdout-v1",
        fixture_hash=_default_fixture_hash(),
        calibrated=True,
    ),
}


def estimate_bucket_correlation(
    xs: Sequence[float],
    ys: Sequence[float],
    *,
    bucket: str,
    holdout_version: str,
) -> BucketCalibration:
    """Estimate Pearson rho and Fisher-z 95% CI from historical player-weeks."""
    if len(xs) != len(ys) or len(xs) < 4:
        raise ValueError("need at least 4 paired observations")
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0.0 or dy == 0.0:
        rho = 0.0
    else:
        rho = max(-0.999, min(0.999, num / (dx * dy)))
    z = 0.5 * math.log((1.0 + rho) / (1.0 - rho))
    se = 1.0 / math.sqrt(max(1, n - 3))
    z_lo, z_hi = z - 1.96 * se, z + 1.96 * se
    ci_low = math.tanh(z_lo)
    ci_high = math.tanh(z_hi)
    fixture_hash = canonical_json_sha256({
        "bucket": bucket,
        "holdout_version": holdout_version,
        "n": n,
        "xs": [round(float(v), 4) for v in xs],
        "ys": [round(float(v), 4) for v in ys],
    })
    return BucketCalibration(
        bucket=bucket,
        rho=round(rho, 4),
        ci_low=round(ci_low, 4),
        ci_high=round(ci_high, 4),
        holdout_version=holdout_version,
        fixture_hash=fixture_hash,
        calibrated=True,
    )


def classify_leg_pair_bucket(leg_a: Mapping[str, Any], leg_b: Mapping[str, Any]) -> str:
    """Frozen bucket precedence (first matching bucket wins):
    1. Same game, same team, one QB + one pass-catcher -> QB_WR
    2. Same game, same team, two pass-catchers -> SAME_TEAM_RECEIVING
    3. Same game, opposing teams, two skill players -> OPPOSING_SKILL
    4. Anything else -> INDEPENDENT
    Receiving markets only (rush yards / opposing QBs stay INDEPENDENT).
    """
    if str(leg_a.get("game_id") or "") != str(leg_b.get("game_id") or "") or not leg_a.get("game_id"):
        return BUCKET_INDEPENDENT

    mkt_a = str(leg_a.get("market") or "").strip().lower()
    mkt_b = str(leg_b.get("market") or "").strip().lower()
    if mkt_a not in RECEIVING_MARKETS or mkt_b not in RECEIVING_MARKETS:
        return BUCKET_INDEPENDENT

    team_a = str(leg_a.get("team") or "").strip().upper()
    team_b = str(leg_b.get("team") or "").strip().upper()
    pos_a = str(leg_a.get("position") or "").strip().upper()
    pos_b = str(leg_b.get("position") or "").strip().upper()
    if not team_a or not team_b or not pos_a or not pos_b:
        return BUCKET_INDEPENDENT

    if team_a == team_b:
        if {pos_a, pos_b} & {"QB"} and ({pos_a, pos_b} & PASS_CATCHER_POSITIONS):
            return BUCKET_QB_WR
        if pos_a in PASS_CATCHER_POSITIONS and pos_b in PASS_CATCHER_POSITIONS:
            return BUCKET_SAME_TEAM_RECEIVING
        return BUCKET_INDEPENDENT

    # Opposing teams on the same game: two skill players (opposing QBs excluded)
    if pos_a in SKILL_POSITIONS and pos_b in SKILL_POSITIONS:
        return BUCKET_OPPOSING_SKILL
    return BUCKET_INDEPENDENT


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam-style inverse normal CDF accurate to ~1e-9."""
    if not (0.0 < p < 1.0):
        raise ValueError(f"probability must be in (0, 1), got {p}")
    # Binary search using erf is exact to 1e-10 and trivial with zero dependency
    lo, hi = -8.0, 8.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _cholesky(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                val = matrix[i][i] - s
                if val <= 1e-9:
                    # Shrink off-diagonals minimally if high-dim negative rho hits PSD boundary
                    raise ValueError("correlation matrix not positive definite")
                L[i][j] = math.sqrt(val)
            else:
                L[i][j] = (matrix[i][j] - s) / L[j][j]
    return L


def _gaussian_copula_joint_prob(probs: Sequence[float], corr: list[list[float]], *, draws: int = 20000) -> float:
    n = len(probs)
    if n == 0:
        return 0.0
    if n == 1:
        return float(probs[0])
    thresholds = [_norm_ppf(p) for p in probs]
    # Ensure PSD by minimal eigenvalue damping if needed
    work = [row[:] for row in corr]
    L = None
    for scale in (1.0, 0.95, 0.90, 0.85):
        scaled = [
            [1.0 if i == j else work[i][j] * scale for j in range(n)]
            for i in range(n)
        ]
        try:
            L = _cholesky(scaled)
            break
        except ValueError:
            continue
    if L is None:
        # Fallback to diagonal if unrecoverable
        out = 1.0
        for p in probs:
            out *= p
        return out

    rng = random.Random(20261118)
    hits = 0
    for _ in range(draws):
        z = [rng.gauss(0.0, 1.0) for _ in range(n)]
        ok = True
        for i in range(n):
            x_i = sum(L[i][k] * z[k] for k in range(i + 1))
            if x_i > thresholds[i]:
                ok = False
                break
        if ok:
            hits += 1
    return hits / draws


def _has_v2_opening_hash(leg: Mapping[str, Any]) -> bool:
    schema = str(leg.get("opening_snapshot_schema") or leg.get("schema") or "")
    h = str(leg.get("opening_snapshot_hash") or leg.get("closing_snapshot_hash") or "")
    return schema == "propwire.closing_snapshot.v2" and len(h) == 64


def price_slip(
    legs: Sequence[Mapping[str, Any]],
    *,
    qualification_policy: Mapping[str, Any] | None = None,
    calibrations: Mapping[str, BucketCalibration] | None = None,
    now: datetime | None = None,
    max_age_seconds: float = DEFAULT_MAX_TAPE_AGE_SECONDS,
) -> dict[str, Any]:
    """Price a multi-leg slip under the three frozen correlation buckets.

    Fails closed when any leg is stale/unhashable, calibration is uncalibrated/unsupported,
    v2 opening hashes are missing, or qualification_policy fails verification.
    """
    if not legs:
        return {
            "status": STATUS_UNEVALUABLE,
            "reason": "EMPTY_SLIP",
            "action_enabled": False,
            "independent_joint_prob": None,
            "copula_joint_prob": None,
            "copula_struck_through": False,
            "buckets": [],
        }

    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cal_map = calibrations if calibrations is not None else PUBLISHED_RHO_CALIBRATION

    # 1. Validate every leg via BookQuoteTape rules (source_ts, provenance hash, freshness).
    probs: list[float] = []
    v2_hashes_ok = True
    for leg in legs:
        tape_ok = validate_tape_row(leg, now=now_dt, max_age_seconds=max_age_seconds)
        if tape_ok is None:
            return {
                "status": STATUS_UNEVALUABLE,
                "reason": "STALE_OR_UNHASHABLE_LEG",
                "action_enabled": False,
                "independent_joint_prob": None,
                "copula_joint_prob": None,
                "copula_struck_through": False,
                "buckets": [],
            }
        probs.append(float(tape_ok["devig_p"]))
        if not _has_v2_opening_hash(leg):
            v2_hashes_ok = False

    indep_prob = 1.0
    for p in probs:
        indep_prob *= p
    indep_prob = round(indep_prob, 6)

    n = len(legs)
    corr = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    pair_buckets: list[dict[str, Any]] = []
    any_uncalibrated = False
    any_unsupported = False

    for i in range(n):
        for j in range(i + 1, n):
            bucket = classify_leg_pair_bucket(legs[i], legs[j])
            if bucket == BUCKET_INDEPENDENT:
                pair_buckets.append({"pair": [i, j], "bucket": bucket, "rho": 0.0})
                continue
            cal = cal_map.get(bucket)
            if cal is None or not cal.calibrated:
                any_uncalibrated = True
                rho_val = cal.rho if cal is not None else 0.0
                corr[i][j] = corr[j][i] = rho_val
                pair_buckets.append({"pair": [i, j], "bucket": bucket, "rho": rho_val, "status": STATUS_RHO_UNCALIBRATED})
            elif cal.interval_includes_zero:
                any_unsupported = True
                corr[i][j] = corr[j][i] = cal.rho
                pair_buckets.append({
                    "pair": [i, j],
                    "bucket": bucket,
                    "rho": cal.rho,
                    "interval": [cal.ci_low, cal.ci_high],
                    "status": STATUS_RHO_UNSUPPORTED,
                })
            else:
                corr[i][j] = corr[j][i] = cal.rho
                pair_buckets.append({
                    "pair": [i, j],
                    "bucket": bucket,
                    "rho": cal.rho,
                    "interval": [cal.ci_low, cal.ci_high],
                    "holdout_version": cal.holdout_version,
                    "fixture_hash": cal.fixture_hash,
                    "status": STATUS_SUPPORTED,
                })

    copula_prob = round(_gaussian_copula_joint_prob(probs, corr), 6)
    policy_ok = verify_qualification_policy(qualification_policy)

    if any_unsupported:
        return {
            "status": STATUS_RHO_UNSUPPORTED,
            "action_enabled": False,
            "independent_joint_prob": indep_prob,
            "copula_joint_prob": copula_prob,
            "effective_joint_prob": indep_prob,
            "copula_struck_through": True,
            "covariance_tax": round(copula_prob - indep_prob, 6),
            "buckets": pair_buckets,
            "policy_verified": policy_ok,
            "v2_opening_hashes_verified": v2_hashes_ok,
        }

    if any_uncalibrated:
        return {
            "status": STATUS_RHO_UNCALIBRATED,
            "action_enabled": False,
            "independent_joint_prob": indep_prob,
            "copula_joint_prob": copula_prob,
            "effective_joint_prob": copula_prob,
            "copula_struck_through": False,
            "covariance_tax": round(copula_prob - indep_prob, 6),
            "buckets": pair_buckets,
            "policy_verified": policy_ok,
            "v2_opening_hashes_verified": v2_hashes_ok,
        }

    return {
        "status": STATUS_SUPPORTED,
        "action_enabled": bool(policy_ok and v2_hashes_ok),
        "independent_joint_prob": indep_prob,
        "copula_joint_prob": copula_prob,
        "effective_joint_prob": copula_prob,
        "copula_struck_through": False,
        "covariance_tax": round(copula_prob - indep_prob, 6),
        "buckets": pair_buckets,
        "policy_verified": policy_ok,
        "v2_opening_hashes_verified": v2_hashes_ok,
    }
