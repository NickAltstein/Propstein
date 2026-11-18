"""Tests for Commit 3: price_slip (POST /v1/slips), three frozen buckets, fail-closed states, and 5-leg covariance tax."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

from api.main import app
try:
    from src.contracts.qualification_policy import build_qualification_policy
except ImportError:
    from src.contracts.slip_pricer import build_qualification_policy
from src.contracts.slip_pricer import (
    BUCKET_INDEPENDENT,
    BUCKET_OPPOSING_SKILL,
    BUCKET_QB_WR,
    BUCKET_SAME_TEAM_RECEIVING,
    PUBLISHED_RHO_CALIBRATION,
    STATUS_RHO_UNCALIBRATED,
    STATUS_RHO_UNSUPPORTED,
    STATUS_SUPPORTED,
    STATUS_UNEVALUABLE,
    BucketCalibration,
    classify_leg_pair_bucket,
    estimate_bucket_correlation,
    price_slip,
)


def _leg(
    *,
    player: str,
    team: str,
    position: str,
    game_id: str = "2026_11_DET_GB",
    market: str = "receiving_yards",
    devig_p: float = 0.80,
    source_ts: str | None = None,
    observed_at: str | None = None,
    raw_hash: str = "a" * 64,
    v2_hash: str | None = None,
):
    now = datetime.now(timezone.utc)
    return {
        "market_id": f"{game_id}:{player}:{market}",
        "game_id": game_id,
        "player_id": player,
        "team": team,
        "position": position,
        "market": market,
        "line": 49.5,
        "book": "polymarket",
        "quote_source": "polymarket",
        "american": -400,
        "devig_p": devig_p,
        "source_ts": source_ts or (now - timedelta(seconds=30)).isoformat(),
        "observed_at": observed_at or now.isoformat(),
        "raw_payload_sha256": raw_hash,
        "opening_snapshot_schema": "propwire.closing_snapshot.v2",
        "opening_snapshot_hash": v2_hash or ("b" * 64),
    }


def test_frozen_bucket_precedence_and_exclusions():
    qb = {"game_id": "g1", "team": "DET", "position": "QB", "market": "receiving_yards"}
    wr1 = {"game_id": "g1", "team": "DET", "position": "WR", "market": "receiving_yards"}
    wr2 = {"game_id": "g1", "team": "DET", "position": "TE", "market": "receptions"}
    opp_wr = {"game_id": "g1", "team": "GB", "position": "WR", "market": "receiving_yards"}
    opp_qb = {"game_id": "g1", "team": "GB", "position": "QB", "market": "receiving_yards"}
    rush = {"game_id": "g1", "team": "DET", "position": "RB", "market": "rushing_yards"}
    other_game = {"game_id": "g2", "team": "BUF", "position": "WR", "market": "receiving_yards"}

    assert classify_leg_pair_bucket(qb, wr1) == BUCKET_QB_WR
    assert classify_leg_pair_bucket(wr1, wr2) == BUCKET_SAME_TEAM_RECEIVING
    assert classify_leg_pair_bucket(wr1, opp_wr) == BUCKET_OPPOSING_SKILL
    assert classify_leg_pair_bucket(qb, opp_qb) == BUCKET_INDEPENDENT
    assert classify_leg_pair_bucket(wr1, rush) == BUCKET_INDEPENDENT
    assert classify_leg_pair_bucket(wr1, other_game) == BUCKET_INDEPENDENT


def test_five_same_game_receiving_overs_covariance_tax_and_material_drop():
    policy = build_qualification_policy()
    five_legs = [
        _leg(player=f"wr{i}", team="DET", position="WR", devig_p=0.80)
        for i in range(5)
    ]
    res5 = price_slip(five_legs, qualification_policy=policy)
    assert res5["status"] == STATUS_SUPPORTED
    assert res5["action_enabled"] is True
    # Must show covariance tax: copula < independent
    assert res5["copula_joint_prob"] < res5["independent_joint_prob"]
    assert res5["covariance_tax"] < 0.0

    res4 = price_slip(five_legs[:4], qualification_policy=policy)
    rel_change = abs(res4["copula_joint_prob"] - res5["copula_joint_prob"]) / res5["copula_joint_prob"]
    # Dropping 1 correlated leg must NOT move by ~1/5th (0.20 / 0.25); negative covariance tax jumps materially
    assert rel_change > 0.45


def test_fail_closed_states_and_post_v1_slips_endpoint():
    policy = build_qualification_policy()
    legs = [
        _leg(player="wr1", team="DET", position="WR", devig_p=0.60),
        _leg(player="wr2", team="DET", position="WR", devig_p=0.60),
    ]

    # 1. RHO_UNCALIBRATED -> both numbers shown, action_enabled = False
    uncal = {
        **PUBLISHED_RHO_CALIBRATION,
        BUCKET_SAME_TEAM_RECEIVING: BucketCalibration(
            bucket=BUCKET_SAME_TEAM_RECEIVING,
            rho=-0.20,
            ci_low=-0.30,
            ci_high=-0.10,
            holdout_version="test",
            fixture_hash="f" * 64,
            calibrated=False,
        ),
    }
    res_uncal = price_slip(legs, qualification_policy=policy, calibrations=uncal)
    assert res_uncal["status"] == STATUS_RHO_UNCALIBRATED
    assert res_uncal["action_enabled"] is False
    assert res_uncal["independent_joint_prob"] is not None
    assert res_uncal["copula_joint_prob"] is not None

    # 2. Interval includes zero -> RHO_UNSUPPORTED, fall back to independent, strike through copula
    zero_ci = {
        **PUBLISHED_RHO_CALIBRATION,
        BUCKET_SAME_TEAM_RECEIVING: BucketCalibration(
            bucket=BUCKET_SAME_TEAM_RECEIVING,
            rho=-0.05,
            ci_low=-0.15,
            ci_high=0.05,
            holdout_version="test",
            fixture_hash="f" * 64,
            calibrated=True,
        ),
    }
    res_unsup = price_slip(legs, qualification_policy=policy, calibrations=zero_ci)
    assert res_unsup["status"] == STATUS_RHO_UNSUPPORTED
    assert res_unsup["copula_struck_through"] is True
    assert res_unsup["effective_joint_prob"] == res_unsup["independent_joint_prob"]
    assert res_unsup["action_enabled"] is False

    # 3. Stale or unhashable leg -> UNEVALUABLE
    stale_legs = [legs[0], {**legs[1], "source_ts": ""}]
    assert price_slip(stale_legs, qualification_policy=policy)["status"] == STATUS_UNEVALUABLE

    # 4. Missing v2 opening hash or tampered policy -> action_enabled = False even if SUPPORTED
    v1_leg = [{**legs[0], "opening_snapshot_schema": "propwire.closing_snapshot.v1"}, legs[1]]
    res_v1 = price_slip(v1_leg, qualification_policy=policy)
    assert res_v1["status"] == STATUS_SUPPORTED
    assert res_v1["action_enabled"] is False

    bad_policy = {**policy, "policy_spec_hash": "0" * 64}
    res_bad_pol = price_slip(legs, qualification_policy=bad_policy)
    assert res_bad_pol["status"] == STATUS_SUPPORTED
    assert res_bad_pol["action_enabled"] is False

    # 5. Historical estimator produces rho, interval, holdout_version, fixture_hash
    est = estimate_bucket_correlation([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [1.2, 1.9, 3.1, 3.8, 5.2, 5.9], bucket=BUCKET_QB_WR, holdout_version="2025-v1")
    assert est.rho > 0.9
    assert est.ci_low > 0.0
    assert len(est.fixture_hash) == 64

    # 6. POST /v1/slips endpoint
    client = TestClient(app)
    resp = client.post("/v1/slips", json={"legs": legs, "qualification_policy": policy})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == STATUS_SUPPORTED
    assert body["action_enabled"] is True
