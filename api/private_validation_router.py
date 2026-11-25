"""
PropStein PRIVATE_VALIDATION API Router (strictly isolated from public /check/).
===============================================================================
Requires PROPSTEIN_PRIVATE_VALIDATION=1 AND a matching PRIVATE_VALIDATION_TOKEN.
Public /check/ and /api/v1/extension/* have zero read access to this store.
"""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.private_validation.comparison_engine import (
    BookObservation,
    HealthObservation,
    PrivateObservationStore,
    SlipSnapshot,
    TenSecondTrace,
    evaluate_private_comparison,
)

router = APIRouter(prefix="/api/private-validation", tags=["private-validation"])

_STORE = PrivateObservationStore()


def get_private_store() -> PrivateObservationStore:
    return _STORE


def reset_private_store(store: PrivateObservationStore | None = None) -> None:
    global _STORE
    _STORE = store if store is not None else PrivateObservationStore()


def _private_validation_enabled() -> bool:
    return os.environ.get("PROPSTEIN_PRIVATE_VALIDATION", "0").strip().lower() in ("1", "true", "yes")


def require_private_validation_auth(authorization: str | None = Header(default=None)) -> None:
    if not _private_validation_enabled():
        raise HTTPException(status_code=503, detail="PRIVATE_VALIDATION_DISABLED")
    expected = (os.environ.get("PRIVATE_VALIDATION_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="PRIVATE_VALIDATION_TOKEN_UNCONFIGURED")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="MISSING_PRIVATE_VALIDATION_TOKEN")
    presented = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="INVALID_PRIVATE_VALIDATION_TOKEN")


class BookObservationIn(BaseModel):
    book: str
    game_id: str
    player_id: str
    market: str
    period: str
    rung: int
    american_odds: int | None = None
    observed_at: str
    received_at: str
    adapter_id: str
    run_id: str
    source_env: str = "PRIVATE_VALIDATION"
    source_ts: str | None = None
    observation_seq: int = 0
    is_fixture: bool = False
    is_seeded: bool = False
    is_replayed: bool = False
    is_test: bool = False
    provenance: str = "private_adapter"
    raw_artifact_sha256: str | None = None
    malformed_reason: str | None = None


class HealthObservationIn(BaseModel):
    book: str
    run_id: str
    status: str
    checked_at: str
    latency_ms: float | None = None
    detail: str = ""


class SlipSnapshotIn(BaseModel):
    slip_id: str = Field(..., min_length=1)
    check_version: int = Field(..., ge=1)
    player_id: str
    player_name: str
    game_id: str
    market: str
    period: str
    rung: int
    book: str
    captured_price: int
    captured_at: str
    capture_method: str = "paste"


class CompareRequest(BaseModel):
    run_id: str
    slip: SlipSnapshotIn
    dk_health: HealthObservationIn | None = None
    fd_health: HealthObservationIn | None = None
    expected_check_version: int | None = None
    render_now: str | None = None
    timing_trace: dict[str, Any] | None = None


@router.post("/observations")
def ingest_private_observation(
    payload: BookObservationIn,
    request: Request,
    _: None = Depends(require_private_validation_auth),
) -> dict[str, Any]:
    obs = BookObservation(**payload.model_dump())
    ok, reason = get_private_store().ingest(obs, headers=request.headers)
    if not ok and reason.startswith("REJECTED_FIXTURE_OR_TEST"):
        raise HTTPException(status_code=422, detail={"accepted": False, "reason": reason})
    return {"accepted": ok, "reason": reason}


@router.post("/compare")
def compare_private_slip(
    payload: CompareRequest,
    _: None = Depends(require_private_validation_auth),
) -> dict[str, Any]:
    render_now = (
        datetime.fromisoformat(payload.render_now.replace("Z", "+00:00"))
        if payload.render_now
        else datetime.now(timezone.utc)
    )
    trace = None
    if payload.timing_trace:
        trace = TenSecondTrace(
            t0_capture_start_ms=int(payload.timing_trace["t0_capture_start_ms"]),
            t_capture_complete_ms=int(payload.timing_trace["t_capture_complete_ms"]),
            t_answer_rendered_ms=int(payload.timing_trace["t_answer_rendered_ms"]),
            t_correction_open_ms=payload.timing_trace.get("t_correction_open_ms"),
            t_correction_complete_ms=payload.timing_trace.get("t_correction_complete_ms"),
            t_dk_adapter_ms=payload.timing_trace.get("t_dk_adapter_ms"),
            t_fd_adapter_ms=payload.timing_trace.get("t_fd_adapter_ms"),
            t_return_to_book_ms=payload.timing_trace.get("t_return_to_book_ms"),
        )
    verdict = evaluate_private_comparison(
        SlipSnapshot(**payload.slip.model_dump()),
        store=get_private_store(),
        dk_health=HealthObservation(**payload.dk_health.model_dump()) if payload.dk_health else None,
        fd_health=HealthObservation(**payload.fd_health.model_dump()) if payload.fd_health else None,
        run_id=payload.run_id,
        render_now=render_now,
        expected_check_version=payload.expected_check_version,
        private_validation_enabled=_private_validation_enabled(),
        trace=trace,
    )
    return verdict.as_dict()
