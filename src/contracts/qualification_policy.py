"""Canonical qualification_policy contract and hash verification."""

from __future__ import annotations

from typing import Any, Mapping

from src.crypto_utils import canonical_json_sha256

REQUIRED_POLICY_FIELDS = (
    "policy_version",
    "market_scope",
    "approved_source_list",
    "staleness_rules",
    "edge_bounds",
    "sample_rules",
)


def policy_parameter_payload(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only the canonical parameter fields (never markdown or the hash itself)."""
    return {field: policy[field] for field in REQUIRED_POLICY_FIELDS}


def compute_policy_spec_hash(policy: Mapping[str, Any]) -> str:
    """Compute SHA-256 of canonical JSON of the parameter object."""
    return canonical_json_sha256(policy_parameter_payload(policy))


def build_qualification_policy(
    *,
    policy_version: str = "2026.11.18-am003",
    market_scope: list[str] | None = None,
    approved_source_list: list[str] | None = None,
    staleness_rules: dict[str, Any] | None = None,
    edge_bounds: dict[str, Any] | None = None,
    sample_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a self-verifying qualification_policy object for slate payloads."""
    params: dict[str, Any] = {
        "policy_version": policy_version,
        "market_scope": market_scope or ["receiving_yards", "receptions"],
        "approved_source_list": approved_source_list or ["polymarket"],
        "staleness_rules": staleness_rules or {
            "contract": "verification.v1",
            "staleness_pregame_minutes": 90,
            "staleness_near_kickoff_minutes": 15,
            "staleness_in_game_minutes": 2,
            "near_kickoff_window_minutes": 30,
        },
        "edge_bounds": edge_bounds or {
            "min_edge_pp": 10.0,
            "data_check_edge_pp": 12.0,
            "high_edge_caution_pp": 15.0,
            "stake_cap": 0.03,
        },
        "sample_rules": sample_rules or {
            "confirmatory_n": 600,
            "cluster_unit": "game_id",
            "one_sided_alpha": 0.0125,
            "min_mean_clv_pp": 1.0,
        },
    }
    return {
        **params,
        "policy_spec_hash": compute_policy_spec_hash(params),
    }


def verify_qualification_policy(policy: Any) -> bool:
    """Fail closed if qualification_policy is missing, incomplete, or hash-mismatched."""
    if not isinstance(policy, Mapping):
        return False
    for field in REQUIRED_POLICY_FIELDS:
        if field not in policy or policy[field] is None:
            return False
    supplied_hash = policy.get("policy_spec_hash")
    if not isinstance(supplied_hash, str) or len(supplied_hash) != 64:
        return False
    return compute_policy_spec_hash(policy) == supplied_hash


def fail_closed_action_fields(row: Mapping[str, Any], policy: Any) -> dict[str, Any]:
    """Zero/disable action fields on a slate row when qualification_policy fails verification."""
    out = dict(row)
    if verify_qualification_policy(policy):
        return out
    out["actionable"] = False
    out["actionable_stake"] = 0.0
    out["stake"] = 0.0
    out["qualified"] = False
    out["can_execute"] = False
    out["policy_status"] = "POLICY_INVALID"
    return out
