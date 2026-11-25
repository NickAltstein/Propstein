# Standalone page script order: diagnosis (Step 1)

This inventory is from `main` at `cb8968444` on 2026-11-24. It covered **all 1,662 `viewer/**/*.html` files** at that commit; the per-file list was removed (#294) because it went stale with every page change, and `test/viewer-script-order.test.mjs` plus the "Viewer page-load smoke" job now guard the behaviour against the live tree. The tags below are the exact opening tags, in document order. Group 2's JSON-LD tag is structured data, not an executable script.

| Group | Files | Ordered script tags |
| --- | ---: | --- |
| 1 | 864 | None |
| 2 | 787 | `<script type="application/ld+json">` |
| 3 | 3 (`audit.html`, `ledger.html`, `ledger/index.html`) | `<script src="/js/sports/registry.js?v=2026_v179" defer>` → `<script src="/js/sports/nfl.js?v=2026_v181" defer>` → `<script src="/js/sports/nba.js?v=2026_v179" defer>` → `<script src="/js/feed-status.js?v=2026_v260" defer>` → `<script src="/js/market-status.js?v=2026_v5" defer>` → `<script src="/app.js?v=2026_v281" defer>` |
| 4 | 1 (`check/index.html`) | `<script src="/js/quant-math.js?v=2026_v215" defer>` → `<script src="/r/tailcheck.js?v=2026_v283" defer>` → `<script src="/r/ticket-core.js?v=2026_v284" defer>` → `<script src="/check/check.js?v=2026_v283" defer>` |
| 5 | 1 (`index.html`) | `<script src="/js/sports/registry.js?v=2026_v179" defer>` → `<script src="/js/sports/nfl.js?v=2026_v181" defer>` → `<script src="/js/sports/nba.js?v=2026_v221" defer>` → `<script src="/js/quant-math.js?v=2026_v215" defer>` → `<script src="/js/feed-status.js?v=2026_v260" defer>` → `<script src="/js/market-status.js?v=2026_v5" defer>` → `<script src="/app.js?v=2026_v281" defer>` → `<script src="/js/instrument-panel.js?v=2026_v1" defer>` → `<script src="/js/sports/ufc.js?v=2026_v200" defer>` → `<script src="/js/today-schedule.js?v=2026_v274" defer>` → `<script src="/js/front-page.js?v=2026_v254" defer>` → `<script src="/js/refresh-2026.js?v=2026_r2" defer>` |
| 6 | 1 (`r/index.html`) | `<script src="/js/quant-math.js?v=2026_v215" defer>` → `<script src="/r/tailcheck.js?v=2026_v283" defer>` → `<script src="/r/receipt.js?v=2026_v282" defer>` |
| 7 | 1 (`t/index.html`) | `<script src="/js/quant-math.js?v=2026_v215" defer>` → `<script src="/r/tailcheck.js?v=2026_v283" defer>` → `<script src="/r/ticket-core.js?v=2026_v284" defer>` → `<script src="/t/ticket.js?v=2026_v284" defer>` |
| 8 | 1 (`ufc/fighter.html`) | `<script src="/js/ufc-fighter.js?v=2026_v199" defer>` |
| 9 | 1 (`verify.html`) | `<script src="/verify-landing.js?v=2026_v179" defer>` |
| 10 | 1 (`verify/index.html`) | `<script src="/verify/precommit.js">` |
| 11 | 1 (`widget/line-pricer/index.html`) | `<script src="/js/quant-math.js?v=2026_v215" defer>` → `<script src="/widget/line-pricer/widget.js?v=2026_v2" defer>` |

## Required order for pages loading `app.js`

`index.html` establishes: sports registry → NFL adapter → NBA adapter → quant math → feed status → market status → app. The #270 branch inserts price state between market status and app. Thus the post-#270 order is **registry → NFL → NBA → quant math → feed status → market status → price state → app**, followed by page-specific scripts. The standalone pages omit quant math today. They also use NBA adapter cache buster `v179` where index uses `v221`.

`app.js` throws `PropWire: /js/quant-math.js must load before app.js` at line 10478 when `window.PropWireMath` is absent. Its earlier uses of the `devig` constant (for example line 4507) can also produce `Cannot access 'devig' before initialization` while the script is evaluating. Both are consistent with the missing prerequisite; the latter should be verified by the page-load test after repair.

## Ledger root is a separate path issue

The board ribbon calls `fetchJson('clv_report_2026_w7.json', null)` in `app.js` at line 3043. `fetchData` passes that relative path directly to `fetch`. The checked-in root report has a valid `summary` (`beat_close_count: 2`, `total_graded: 37`). On 2026-11-24, the live site returned HTTP 200 JSON for `/clv_report_2026_w7.json` and HTTP 404 for both `/audit/clv_report_2026_w7.json` and `/ledger/clv_report_2026_w7.json`. The route results are:

| Page URL | Resolved report fetch URL | Result |
| --- | --- | --- |
| `/` | `/clv_report_2026_w7.json` | 200 JSON |
| `/audit/` (including redirects from `/audit` and `/audit.html`) | `/audit/clv_report_2026_w7.json` | 404 |
| `/ledger/` (including redirects from `/ledger` and `/ledger.html`) | `/ledger/clv_report_2026_w7.json` | 404 |

Therefore the board's `LEDGER ROOT UNAVAILABLE` is **not purely a boot symptom**: once the script order is repaired, the nested routes will still fail their report fetch until it uses a root-relative URL. The root-query badge has another condition: `checkDeepLinks` reads `clv_audit_report.json` and expects `ledger_root_hash`, which is separate from the board ribbon. This diagnosis does not alter either data file.

## #270 integration

#270 edits the same script blocks in `index.html`, `audit.html`, `ledger.html`, and `ledger/index.html` to add `price-state.js`. Rebase this branch on #270 after #270 merges (or rebase #270 on this branch if this goes first), resolve those HTML blocks once, then rerun the static and browser checks. No code or data changes are part of this Step 1 commit.
