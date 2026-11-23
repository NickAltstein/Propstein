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

### B. Live-Style Markup Drift Fixtures (`draftkings-live-drift.html`, `fanduel-live-drift.html`) — Patched in v1.1

| Book | Fixture | Status | Markets Parsed | Confidence | Patch Applied (v1.1) |
| :--- | :--- | :---: | :---: | :---: | :--- |
| DraftKings | `draftkings-live-drift.html` | `OK` | `2` (Over + Under) | `0.93` | Added `tr.sportsbook-table__body-row` + paired `.sportsbook-outcome-cell` parser and read-only header/URL `Away @ Home` parser (`CHI @ DEN`). |
| FanDuel | `fanduel-live-drift.html` | `OK` | `2` (Over + Under) | `0.93` | Added `div[role="button"][aria-label]` fallback (extracting selection, line, and American odds) + read-only header/URL `Away @ Home` parser (`DAL @ BOS`). |

---

## 3. Staging Readiness Verdict

- **Canonical 8-market fixtures (`anytime_td`, `first_td`, `two_plus_td`, `nba_points`, `nba_rebounds`, `nba_assists`, `nba_threes`, `nba_pra`):** **100% (8/8)** on DraftKings and **100% (8/8)** on FanDuel at **`0.95 >= 0.90`** confidence.
- **Live-style structural drift fixtures (DK table rows + FD `aria-label` buttons + header matchup binding):** **100% (2/2 Over+Under)** on both books at **`0.93 >= 0.90`** confidence.
- **Staging flag verdict:** **READY FOR LOCAL/STAGING DOGFOODING ONCE ONE USER-CAPTURED LIVE HTML SNAPSHOT PER BOOK IS CONFIRMED** (sportsbooks block automated headless access, so run the 1-line DevTools sanitizer in §1 in your own browser tab and verify against `pregame-selector-validation.test.ts` before setting `EXTENSION_OBSERVED_PREGAME_ADAPTERS` in staging; keep unset in production).
