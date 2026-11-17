# Public market status contract

`viewer/js/market-status.js` derives one presentation state from separate
feed, per-quote freshness, verification, qualification, and game lifecycle
dimensions. The order is: event started, live prices unavailable, checking,
snapshot only, verified opportunity count, then no qualifying prices.
An unknown provider quote time or unknown game lifecycle is never actionable.

The viewer reads `quoted_at` as the provider quote time. On a successful live
Kalshi or Polymarket read, ingestion writes `quoted_at` from that read's
`observed_at`. Re-serving a stored payload preserves it. `observed_at` is
ingestion time, `verification_checked_at` is validation time, and the viewer
records refresh attempts separately. `verification_windows.json` mirrors the
Python `verification.v1` defaults; `tests/test_viewer_verification_windows.py`
fails if they drift.

Verified means provenance and freshness checks passed; it does not guarantee
execution, availability, model quality, or a winning outcome.

The game lifecycle joins the schedule and scores events already fetched by
the page to the board's game ids. If a game has no matched status but its
scheduled start is past, it is treated as started. A missing future match
remains unknown. No quote with unknown lifecycle is actionable.
