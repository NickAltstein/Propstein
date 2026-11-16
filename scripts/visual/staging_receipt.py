#!/usr/bin/env python3
"""Isolated receipt rehearsal. Requires PROPSTEIN_STAGING_VERIFY=1.

Writes browser-intercept feeds under artifacts only. The published feed is never
changed; verification.v1 supplies both the matched and fail-closed decisions.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from src.contracts.verification import PeerQuote, QuoteObservation, ReceiptRecord, verify_quote

if os.environ.get("PROPSTEIN_STAGING_VERIFY") != "1":
    raise SystemExit("Set PROPSTEIN_STAGING_VERIFY=1 for this staging-only rehearsal")

feed = json.loads((root / "viewer/live_odds_feed.json").read_text())
now = datetime.now(timezone.utc)
quote = dict(feed["quotes"][0])
payload = {key: quote[key] for key in ("quote_id", "book", "player_id", "market", "line", "over_odds", "under_odds")}
raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
digest = hashlib.sha256(raw).hexdigest()
market_id = f"{quote['market']}:{quote['player_id']}"
book_id = "pinnacle"
receipt = ReceiptRecord(
    receipt_id=f"staging_{digest[:16]}", market_id=market_id,
    book_id=book_id, raw_payload_hash=digest, captured_at=now,
)
observation = QuoteObservation(
    source="staging-clob-rehearsal", book_id=book_id, market_id=market_id,
    fetched_at=now, raw_payload_hash=digest, side="over",
    over_odds=-110, under_odds=-110,
    book_line=float(quote["line"]),
)
peers = [PeerQuote(book_id="betonlineag", market_id=market_id,
                   no_vig_prob=0.5, fetched_at=now)]
matched = verify_quote(observation=observation, now=now, receipts=[receipt], peers=peers)
failed = verify_quote(observation=observation, now=now, receipts=[], peers=peers)
assert matched.receipt_id == receipt.receipt_id
assert next(c for c in matched.clauses if c.clause_id == "V1_receipt").passed
assert not next(c for c in failed.clauses if c.clause_id == "V1_receipt").passed
assert matched.status.value == "VERIFIED" and failed.status.value != "VERIFIED"

out = root / "artifacts/graphite-review/staging"
out.mkdir(parents=True, exist_ok=True)
for name, decision in (("receipt-matched", matched), ("receipt-failed", failed)):
    item = dict(quote)
    item.update(book="Pinnacle", observed_at=now.isoformat(), content_hash=digest, raw_payload_sha256=digest,
                over_odds=-110, under_odds=-110, verified=decision.status.value == "VERIFIED",
                verification_engine="verification.v1", verification_status=decision.status.value,
                verification_receipt_id=decision.receipt_id,
                verification_clauses=[c.model_dump(mode="json") for c in decision.clauses],
                verification_checked_at=now.isoformat(), _staging_rehearsal=True)
    version = dict(feed, quotes=[item], updated_at=now.isoformat(), total_quotes=1,
                   receipt_matched_quotes_count=int(bool(decision.receipt_id)),
                   verified_quotes_count=int(decision.status.value == "VERIFIED"),
                   feed_status="verified" if decision.status.value == "VERIFIED" else "unverified")
    (out / f"{name}.json").write_text(json.dumps(version, indent=2))
    print(f"{name}: {decision.status.value}, receipt={decision.receipt_id}, sha256={digest[:16]}")
