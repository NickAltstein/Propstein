# Live edge alert release gate

`/v1/edges` is the only actionable alert source. It reads the live NFL quote
feed and requires a real book line and side price, `quoted_at`, valid pregame
start evidence, a fresh `verification.v1` pass with all three clauses, and a
passing `edge_10pct_v1` policy. The model projection file and the legacy
`MarketEdge` table cannot create actionable offers. Rejected quote IDs and
reasons appear at `/v1/diagnostics`.

The dispatcher requires `PROPSTEIN_EDGES_URL` to point to the deployed API's
`/v1/edges` endpoint. Without that setting it fails before attempting any
webhook. The manual `allow_unverified` switch and the missing-price `-110`
substitution are gone. A configured endpoint must identify itself as
`source: verified_feed`; the dispatcher independently checks the returned
verification, policy, quote terms, and expiry before sending anything.

The pilot also requires all four GitHub Actions variables
`PROPSTEIN_PILOT_GAME_ID`, `PROPSTEIN_PILOT_PLAYER_ID`,
`PROPSTEIN_PILOT_MARKET=receiving_yards`, and
`PROPSTEIN_PILOT_BOOK=Polymarket`. An edge for any other player, game, market,
or book is discarded. The alert must carry the provider's side token and
scheduled start so its offered terms can be checked later. Leaving any variable
unset keeps the dispatch workflow disabled.

## One-market pilot hold

The checked-in `viewer/live_odds_feed.json` currently has 11 Polymarket rows,
zero `verified: true` rows, and zero `quoted_at` fields. The sportsbook sweep
explicitly writes unverified research rows, and its contract forbids
promoting them to verified. The repo also has no configured
`PROPSTEIN_EDGES_URL` or identified active API host. Until a live provider
quote passes the existing verification pipeline, the pilot must send zero
alerts. No fixture may be used to unlock production dispatch.

For the pilot, keep one NFL receiving market and one accessible book in scope.
Record each alert's quote ID, book, player, market, side, line, price, and
`quoted_at`. On a real user open, fetch the provider again and compare the
exact line and price. Record `exact`, `moved`, `suspended`, or `unknown`;
provider errors and missing open events are `unknown`, never `exact`.
Report `exact / (exact + moved + suspended)` with the numerator, denominator,
unknown count, and book. Do not add a second book until this metric has a
real observed sample and an approved release threshold.

Successful webhook sends now write offered terms to
`.cache/alert_pilot_dispatches.jsonl`, retained as a workflow artifact. At an
observed pilot recipient open, run
`python3 -m scripts.measure_alert_availability --quote-id <quote_id>` with
that dispatch log available locally. It re-reads the alerted Polymarket CLOB
side token and records the result in `.cache/alert_pilot_opens.jsonl`; use
`--report` for the exact-price rate. The token is tied to the captured market
line, so a live ask on that same token tests both the original line and its
price. A missing token, bad timing evidence, or provider error records
`unknown`; a started event or empty book records `suspended`. This is a manual
open probe: it does not infer that a recipient opened an alert, and it does
not add an operator link. The pilot is not complete until a real recipient
open is observed and probed.
