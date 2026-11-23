# PropStein Live Sniper — Pregame DOM Capture Runbook & Selector Validation Report

**Status:** `EXTENSION_OBSERVED_PREGAME_ADAPTERS` remains **UNSET** (all real-book adapters disabled in production).  
**Mode:** Read-only `OBSERVED · RESEARCH` intelligence only. Zero bet-placement code paths, zero credential collection, zero anti-bot bypass.

---

## 1. Read-Only Pregame DOM Capture Runbook (DraftKings & FanDuel)

Use this manual, user-initiated procedure to capture a sanitized pregame DOM snapshot from an already-open sportsbook tab (no login automation, no headless scraping, no anti-bot bypass):

1. Open an upcoming **pregame** NFL (TD Scorer tab) or NBA (Player Props: Points / Rebounds / Assists / Threes / PRA) event page in your normal Chrome browser.
2. Confirm the target prop rows are **visible on screen** (scroll once if virtualized). Do **not** open or interact with the betslip.
3. Open **Chrome DevTools → Console** (`Cmd+Option+J`) and run the read-only sanitizer snippet below. It clones only the visible market container, strips account/balance/betslip/script nodes, and copies clean HTML to your clipboard:

```js
(() => {
  // Pick the visible prop market container (adjust selector if book layout changed)
  const root =
    document.querySelector('[data-testid="player-prop-markets"], [data-testid="sportsbook-event-accordion"], [data-testid="market-group"], main') ||
    document.body;
  const clone = root.cloneNode(true);
  // Strip scripts, styles, SVGs, account/balance/betslip/iframe nodes
  clone.querySelectorAll('script, style, noscript, iframe, svg, [class*="betslip"], [class*="balance"], [class*="account"], [data-testid*="betslip"]').forEach((el) => el.remove());
  // Strip inline event handlers and tracking attributes
  clone.querySelectorAll('*').forEach((el) => {
    for (const attr of [...el.attributes]) {
      if (attr.name.startsWith('on') || attr.name.startsWith('data-track')) {
        el.removeAttribute(attr.name);
      }
    }
  });
  const html = `<!doctype html><html lang="en"><body>${clone.outerHTML}</body></html>`;
  copy(html);
  console.log('Copied sanitized pregame snapshot:', html.length, 'bytes');
})();
```

4. Save the clipboard output into `extensions/tests/integration/fixtures/<book>-<league>-<YYYY-MM-DD>.html`.
5. Run the `linkedom` validation suite locally:
   ```bash
   cd extensions && node --test tests/integration/pregame-selector-validation.test.ts
   ```
6. Inspect the parse confidence, match rate, and any missing fields before committing the sanitized fixture (verify with `gitleaks protect --staged -v` that zero tokens/emails/cookies are present).

---

## 2. Selector Validation Results (linkedom Fixture Suite)

Evaluated against [pregame-selector-validation.test.ts](file:///Users/nick/Desktop/Projects/PropStein/extensions/tests/integration/pregame-selector-validation.test.ts) using representative canonical fixtures (`*-pregame-all-markets.html`) and live-style drift fixtures (`*-live-drift.html`).

### A. Canonical `data-testid` Pregame Fixtures

| Book | Market Key | Player (Unicode tested) | Parse Confidence | Matched (`MarketMatcher`) | Missing / Drift Fields |
| :--- | :--- | :--- | :---: | :---: | :--- |
| DraftKings | `anytime_td` | CeeDee Lamb | `0.95` | YES | None |
| DraftKings | `first_td` | CeeDee Lamb | `0.95` | YES | None |
| DraftKings | `two_plus_td` | CeeDee Lamb | `0.95` | YES | None |
| DraftKings | `nba_points` | Nikola Jokić → `nikola jokic` | `0.95` | YES | None |
| DraftKings | `nba_rebounds` | Nikola Jokić → `nikola jokic` | `0.95` | YES | None |
| DraftKings | `nba_assists` | Nikola Jokić → `nikola jokic` | `0.95` | YES | None |
| DraftKings | `nba_threes` | Stephen Curry | `0.95` | YES | None |
| DraftKings | `nba_pra` | Nikola Jokić → `nikola jokic` | `0.95` | YES | None |
| FanDuel | `anytime_td` | CeeDee Lamb | `0.95` | YES | None |
| FanDuel | `first_td` | CeeDee Lamb | `0.95` | YES | None |
| FanDuel | `two_plus_td` | CeeDee Lamb | `0.95` | YES | None |
| FanDuel | `nba_points` | Luka Dončić → `luka doncic` | `0.95` | YES | None |
| FanDuel | `nba_rebounds` | Luka Dončić → `luka doncic` | `0.95` | YES | None |
| FanDuel | `nba_assists` | Luka Dončić → `luka doncic` | `0.95` | YES | None |
| FanDuel | `nba_threes` | Luka Dončić → `luka doncic` | `0.95` | YES | None |
| FanDuel | `nba_pra` | Luka Dončić → `luka doncic` | `0.95` | YES | None |

- **Canonical fixture match rate:** **8/8 (100%)** on DraftKings, **8/8 (100%)** on FanDuel, all at **`0.95 >= 0.90`** confidence.

### B. Live-Style Markup Drift Fixtures (`draftkings-live-drift.html`, `fanduel-live-drift.html`)

| Book | Fixture | Status | Markets Parsed | Confidence | Root Cause of Drift |
| :--- | :--- | :---: | :---: | :---: | :--- |
| DraftKings | `draftkings-live-drift.html` | `ADAPTER_DEGRADED` | `0` | `0.00` | Real DK tables use `tr.sportsbook-table__body-row` + `.sportsbook-outcome-cell` without `data-testid="prop-card"`, and omit `data-event-id` on row elements (matchup lives in page header/URL). |
| FanDuel | `fanduel-live-drift.html` | `ADAPTER_DEGRADED` | `0` | `0.00` | Real FD cards often use `div[role="button"][aria-label*="Odds"]` without `data-testid="odds-button"`, and omit `data-event-id` / `data-home-team` attributes. |

Both drift fixtures **failed closed** (`ADAPTER_DEGRADED`, `0` markets) — zero hallucinated matches.

---

## 3. Staging Readiness Verdict

**Are the current v1.0 selectors stable enough for a staging flag flip? — NO (not yet against live production sportsbook DOMs).**

### Why
- On our canonical `data-testid` fixtures, both adapters pass **8/8 markets at `0.95` confidence**.
- However, **no real user-captured live DraftKings or FanDuel HTML snapshot has been checked in yet**, and our live-style drift tests confirm three concrete gaps that will cause v1.0 to fail closed (`ADAPTER_DEGRADED` or `0.82 < 0.90` `UNMATCHED`) on real pages:
  1. **DraftKings row selector:** `DRAFTKINGS_SELECTORS.marketRow` must also match `tr.sportsbook-table__body-row` and extract `O`/`U` + line + odds from `.sportsbook-outcome-cell` (including parsing both Over and Under cells per row).
  2. **FanDuel odds/selection selector:** `FANDUEL_SELECTORS.oddsButton` must also match `div[role="button"][aria-label]` and parse `Over/Under <line>` + American odds from `aria-label` when `data-testid="odds-button"` is absent.
  3. **Event/matchup binding without `data-event-id`:** Real DK/FD pages do not expose PropStein's `data-event-id` attribute. Both adapters need a read-only header/URL matchup parser (`Away @ Home` from `<h1>` / breadcrumb / URL slug) to populate `homeTeam` + `awayTeam` so `detectionConfidence` stays `>= 0.90` instead of dropping to `0.82` (`UNMATCHED`).

### Next Gate Before Flipping Staging
Capture one real sanitized NFL TD + one real sanitized NBA Player Props HTML snapshot per book using the DevTools snippet in §1, add them to `extensions/tests/integration/fixtures/`, patch the 3 selector items above, and re-run `pregame-selector-validation.test.ts` until real captured snapshots hit `>= 0.90` confidence.
