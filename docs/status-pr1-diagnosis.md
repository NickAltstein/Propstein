# Status PR 1: reproduction and root cause

Fixed clock: 2026-11-16 9:50 PM ET. The copied NFL fixture has an odds
`updated_at` of 2026-11-16 3:55 PM ET, 21 non-excluded slate rows, two
receiving-yards rows with ingested quotes, four receipt-matched quotes, and
zero verified quotes. The fixture removes unused provider-list metadata and
URLs. Run `node scripts/visual/status-pr1-capture.mjs before` to reproduce.

## Current renderers and inputs

| Surface | Renderer | Input before the fix |
| --- | --- | --- |
| Header telemetry | `viewer/app.js` `updateFeedHealthUI` / `feedVerificationLabel` | `odds.feed_status`, `feedHealth.oddsFreshness`, and `quoteState(quote)`; `RECEIPTED` becomes `Matched` even with zero verified quotes. |
| Filter counts | `viewer/app.js` `renderBoard` | `state.slate.length` for All; `findQuote` presence for Active; `rows.length` for Top only while Top is selected. |
| Snapshot strip | `viewer/app.js` `updateFeedHealthUI` | `feedHealth.oddsUpdatedAt`, then `odds.generated_at`, then `quote.observed_at`; an independent status sentence comes from `feedVerificationLabel`. |
| Home empty state | `viewer/app.js` `renderHomeEdgePreview` | `feedHealth`, `state.odds.quotes`, `quoteState`, `state.livePicks.picks`, and `state.slate`; it correctly finds no verified play but writes its own copy. |
| Board empty state | `viewer/app.js` `renderBoard` | Filtered slate rows and `findQuote`; no common opportunity count. |
| Refresh toast | `viewer/app.js` `triggerOddsPoll` / `toast` | `freshData.quotes.length` and `quoteState` count, with receipt language unrelated to qualification. |
| Top/All tabs | `viewer/index.html` initial labels, then `viewer/app.js` `renderBoard` | Top starts at static 25 and updates only when selected; All is the 21-row slate length. |

## Why the labels disagree

`RECEIPTED` means ingestion and an independent receipt match, not a verified
qualifying opportunity. The header reduces it to `Matched` while the hero
requires a verified quote. Active counts any row with a quote, regardless of
verification, freshness, qualification, or lifecycle. Top displays a page
limit as a count until its tab is visited. Each surface computes its own
version of status and count. The 390px status strip allocates the explainer
beside the math button and compresses it; at 880px the density switcher
paints over the snapshot strip.

The feed's `observed_at` is ingestion fetch time. The approved contract adds
`quoted_at` for the observed live standing price, preserves it for re-served
snapshots, joins the existing schedule and scores source for lifecycle, and
exports Python `verification.v1` windows with parity protection. These are
requirements for the fix, not fields available to the original renderer.

## Before captures

- `test/screenshots/status-pr1/before/390-board.png`
- `test/screenshots/status-pr1/before/880-board.png`

The 390px capture shows `Matched`, `Active 2`, `Top 25`, `All 21`, the
3:55 PM ET snapshot, and the squeezed fail-closed explainer. The 880px
capture shows the snapshot strip under the density switcher.

The production bug clock is after the LAC–BAL scheduled start. Its correct
presentation is **Event started · pregame research archived**. A second
fixture clock at 6:30 PM ET, before kickoff, tests the distinct **Snapshot
only · 3:55 PM ET** state. Both clocks use the same captured payload.
