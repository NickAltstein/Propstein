# Monetization implementation status

The production viewer is the static `viewer/` Vercel output. The FastAPI service
in `api/main.py` is a separate Railway surface. This split is the main release
boundary for paid freshness: `viewer/live_odds_feed.json` is currently published
as a public static file by the 15-minute GitHub Actions refresh. It cannot be
made Pro-only with a browser check. Paid access and checkout therefore stay
closed, regardless of the displayed tier plan.

## B — Readiness

- `config/monetization.json` sets a 15-minute feed age ceiling, minimum verified
  books, minimum receipt-verified qualified plays and the 10% edge hurdle.
- `src/services/monetization.py` evaluates the current feed fail-closed and
  requires a prospective evidence report, compliance approval and an explicit
  checkout config switch before checkout can open. The old environment switch
  alone is insufficient. `GET /api/billing/readiness` exposes safe diagnostics.
- The 15-minute odds workflow records degraded ET dates in
  `data/commercial_degraded_days.json` for credit/pause **review**. It does not
  issue credits. The existing quote-age UI continues to label stale prices.

## A — Access tiers

- The feature-to-tier map lives in one config file. Research stays free.
- The pricing dialog describes Research, Pro and Quant as planned. No prices or
  checkout controls are published. The checkout service now requires a named
  plan and a configured Stripe price ID; no implicit dollar amount or simulated
  checkout is possible.
- **Not released:** paid endpoint enforcement, sign-in/session flow, delayed
  public snapshot, live Pro feed, seconds-level alerts, Quant API and exports.
  These require moving live data out of the Vercel static output and into an
  authenticated feed service. The current `/api/sniper/stream` is also public.

## D — Free-side growth

- `/widget/line-pricer/` uses the same published NegBin/Gamma math as the
  terminal and reads the current public model slate. Its oEmbed response is
  `/widget/line-pricer/oembed.json`. The Vercel widget route allows framing;
  the rest of the site retains `frame-ancestors 'none'` and X-Frame-Options DENY.
- `npm run share:generate` renders all rows in the legacy eight-row ledger,
  including losses, plus one summary as 1200×630 PNGs. The visible SHA-256 is
  a digest of the published record, **not** a verified sportsbook quote hash.
  NFL P10–P90 ranges are recomputed from the published NegBin/Gamma inputs;
  Brier skill renders when the graded row supplies both Brier scores. Missing
  values are labeled unavailable. This is not yet a complete post-slate render
  route or a verified quote receipt graphic.
- The no-verified-plays state explains the fail-closed rule and offers a share
  link to the public audit.

## C — Price comparison

- Quoted cards can expand a price table for matching lines, sorted by the
  modeled side's American price. Age, receipt status and affiliate disclosure
  appear on the table. No operator link is present.
- `viewer/operator-jurisdictions.json` is empty until approved state/operator
  scope is supplied. Geo-filtered outbound links are not released.

## E — Instrumentation

- The FastAPI `/api/events` collector accepts a fixed event list and stores
  only daily aggregate counts in SQLite. The viewer has fire-and-forget hooks
  for pricing encounters, alert engagement, rail expansion, widget load and
  fail-closed sharing. They require a same-origin
  `meta[name="propstein-events-endpoint"]` route. Vercel does not currently
  route `/api/events` to the Railway API, so browser beacons are inactive on
  the live site and do not create failing requests.
- Upgrade, operator-click and graphic-generation counters remain inactive
  because checkout and operator links are frozen and the image job has no
  shared event store.

## Release gates and evidence

- The current feed has zero verified books and zero qualified plays. Readiness
  and checkout both return closed.
- All 178 Node viewer tests and 671 Python tests outside the separate DRIVE-REC
  research pilot pass (three skipped). The four DRIVE-REC pilot tests require
  two intentionally gitignored model artifacts to be regenerated before that
  suite can run; they are unrelated to this release. Widget browser rendering,
  phone/desktop overflow checks and Vercel preview framing are verified.
- Before a paid release: move live odds and alerts behind authenticated API
  routes, publish yesterday's snapshot for Research, connect a stable event
  route, wire credit decisions, load approved geo scope, and validate both Pro
  and Quant with browser and API tests. Prices remain unset by user choice.
