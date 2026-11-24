/**
 * Integration tests — Pregame DOM Selector Validation (DraftKings + FanDuel).
 * Loads representative HTML fixtures from tests/integration/fixtures/ via
 * linkedom and verifies:
 *   1. All 8 canonical PropStein market keys (NFL TD + NBA multi-stat) parse
 *      at >= 0.90 confidence on the canonical data-testid fixtures.
 *   2. Match rate against MarketMatcher is 8/8 (100%) with Unicode folding.
 *   3. Live-style markup drift fixtures (missing data-event-id / data-testid)
 *      fail closed (ADAPTER_DEGRADED or UNMATCHED) instead of guessing.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { parseHTML } from "linkedom";
import {
  normalizeObservedMarket,
  MarketMatcher,
  type MarketTypeCode,
} from "@propstein/core";
import {
  DraftKingsAdapter,
  FanDuelAdapter,
  enforceParseConfidence,
  MIN_PREGAME_PARSE_CONFIDENCE,
} from "@propstein/detection";

const CLOCK = () => new Date("2026-11-23T18:10:00.000Z");
const FIXTURES_DIR = path.resolve("tests/integration/fixtures");

const EXPECTED_CANONICAL_MARKETS: MarketTypeCode[] = [
  "anytime_td",
  "first_td",
  "two_plus_td",
  "nba_points",
  "nba_rebounds",
  "nba_assists",
  "nba_threes",
  "nba_pra",
];

function loadFixture(name: string): Document {
  const html = fs.readFileSync(path.join(FIXTURES_DIR, name), "utf8");
  return parseHTML(html).document;
}

test("DraftKings canonical pregame fixture: 8/8 NFL TD + NBA multi-stat markets parse at >= 0.90 and match 100%", async () => {
  const adapter = new DraftKingsAdapter({ now: CLOCK });
  const result = await adapter.detectVisibleMarkets(loadFixture("draftkings-pregame-all-markets.html"));
  assert.equal(result.status, "OK");
  assert.equal(result.markets.length, 8);
  assert.ok(result.confidence >= MIN_PREGAME_PARSE_CONFIDENCE);

  const gated = enforceParseConfidence(result.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 8);
  assert.equal(gated.unmatched.length, 0);

  const matcher = new MarketMatcher({
    roster: [
      { playerId: "00-0036358", playerName: "CeeDee Lamb", team: "DAL", eventId: "2026_11_TEN_DAL", league: "NFL" },
      { playerId: "203999", playerName: "Nikola Jokic", team: "DEN", eventId: "nba-20261123-DEN-CHI", league: "NBA" },
      { playerId: "201939", playerName: "Stephen Curry", team: "GSW", eventId: "nba-20261123-DEN-CHI", league: "NBA" },
    ],
    coverage: [
      { key: { league: "NFL", eventId: "2026_11_TEN_DAL", playerId: "00-0036358", marketType: "anytime_td" }, available: true },
      { key: { league: "NFL", eventId: "2026_11_TEN_DAL", playerId: "00-0036358", marketType: "first_td" }, available: true },
      { key: { league: "NFL", eventId: "2026_11_TEN_DAL", playerId: "00-0036358", marketType: "two_plus_td" }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DEN-CHI", playerId: "203999", marketType: "nba_points", line: 27.5 }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DEN-CHI", playerId: "203999", marketType: "nba_rebounds", line: 12.5 }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DEN-CHI", playerId: "203999", marketType: "nba_assists", line: 9.5 }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DEN-CHI", playerId: "201939", marketType: "nba_threes", line: 4.5 }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DEN-CHI", playerId: "203999", marketType: "nba_pra", line: 49.5 }, available: true },
    ],
  });

  const seenMarkets = new Set<MarketTypeCode>();
  for (const m of gated.matched) {
    assert.ok(m.detectionConfidence >= 0.90);
    const norm = normalizeObservedMarket(m);
    const match = matcher.match(norm);
    assert.equal(match.status, "MATCHED", `Expected MATCHED for ${norm.marketTypeCode}, got ${match.status}: ${match.reasons.join("; ")}`);
    seenMarkets.add(norm.marketTypeCode);
  }
  assert.deepEqual([...seenMarkets].sort(), [...EXPECTED_CANONICAL_MARKETS].sort());
});

test("FanDuel canonical pregame fixture: 8/8 NFL TD + NBA multi-stat markets parse at >= 0.90 and match 100%", async () => {
  const adapter = new FanDuelAdapter({ now: CLOCK });
  const result = await adapter.detectVisibleMarkets(loadFixture("fanduel-pregame-all-markets.html"));
  assert.equal(result.status, "OK");
  assert.equal(result.markets.length, 8);
  assert.ok(result.confidence >= MIN_PREGAME_PARSE_CONFIDENCE);

  const gated = enforceParseConfidence(result.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 8);
  assert.equal(gated.unmatched.length, 0);

  const matcher = new MarketMatcher({
    roster: [
      { playerId: "00-0036358", playerName: "CeeDee Lamb", team: "DAL", eventId: "2026_11_TEN_DAL", league: "NFL" },
      { playerId: "1629029", playerName: "Luka Doncic", team: "DAL", eventId: "nba-20261123-DAL-BOS", league: "NBA" },
    ],
    coverage: [
      { key: { league: "NFL", eventId: "2026_11_TEN_DAL", playerId: "00-0036358", marketType: "anytime_td" }, available: true },
      { key: { league: "NFL", eventId: "2026_11_TEN_DAL", playerId: "00-0036358", marketType: "first_td" }, available: true },
      { key: { league: "NFL", eventId: "2026_11_TEN_DAL", playerId: "00-0036358", marketType: "two_plus_td" }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DAL-BOS", playerId: "1629029", marketType: "nba_points", line: 31.5 }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DAL-BOS", playerId: "1629029", marketType: "nba_rebounds", line: 8.5 }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DAL-BOS", playerId: "1629029", marketType: "nba_assists", line: 8.5 }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DAL-BOS", playerId: "1629029", marketType: "nba_threes", line: 3.5 }, available: true },
      { key: { league: "NBA", eventId: "nba-20261123-DAL-BOS", playerId: "1629029", marketType: "nba_pra", line: 48.5 }, available: true },
    ],
  });

  const seenMarkets = new Set<MarketTypeCode>();
  for (const m of gated.matched) {
    assert.ok(m.detectionConfidence >= 0.90);
    const norm = normalizeObservedMarket(m);
    const match = matcher.match(norm);
    assert.equal(match.status, "MATCHED", `Expected MATCHED for ${norm.marketTypeCode}, got ${match.status}: ${match.reasons.join("; ")}`);
    seenMarkets.add(norm.marketTypeCode);
  }
  assert.deepEqual([...seenMarkets].sort(), [...EXPECTED_CANONICAL_MARKETS].sort());
});

test("DraftKings live-style table fixture parses paired Over/Under cells + header matchup at >= 0.90 confidence", async () => {
  const adapter = new DraftKingsAdapter({ now: CLOCK });
  const result = await adapter.detectVisibleMarkets(loadFixture("draftkings-live-drift.html"));
  assert.equal(result.status, "OK");
  assert.equal(result.markets.length, 2);
  assert.ok(result.confidence >= MIN_PREGAME_PARSE_CONFIDENCE);

  const gated = enforceParseConfidence(result.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 2);
  assert.equal(gated.unmatched.length, 0);

  const over = normalizeObservedMarket(gated.matched[0]!);
  const under = normalizeObservedMarket(gated.matched[1]!);
  assert.equal(over.marketTypeCode, "nba_points");
  assert.equal(over.playerNameKey, "nikola jokic");
  assert.equal(over.selectionKey, "OVER");
  assert.equal(over.observed.line, 27.5);
  assert.equal(over.observed.awayTeam, "CHI");
  assert.equal(over.observed.homeTeam, "DEN");
  assert.equal(under.selectionKey, "UNDER");
  assert.equal(under.observed.line, 27.5);
});

test("FanDuel live-style aria-label fixture parses Over/Under buttons + header matchup at >= 0.90 confidence", async () => {
  const adapter = new FanDuelAdapter({ now: CLOCK });
  const result = await adapter.detectVisibleMarkets(loadFixture("fanduel-live-drift.html"));
  assert.equal(result.status, "OK");
  assert.equal(result.markets.length, 2);
  assert.ok(result.confidence >= MIN_PREGAME_PARSE_CONFIDENCE);

  const gated = enforceParseConfidence(result.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 2);
  assert.equal(gated.unmatched.length, 0);

  const over = normalizeObservedMarket(gated.matched[0]!);
  const under = normalizeObservedMarket(gated.matched[1]!);
  assert.equal(over.marketTypeCode, "nba_points");
  assert.equal(over.playerNameKey, "luka doncic");
  assert.equal(over.selectionKey, "OVER");
  assert.equal(over.observed.line, 31.5);
  assert.equal(over.americanOdds, -112);
  assert.equal(over.observed.awayTeam, "DAL");
  assert.equal(over.observed.homeTeam, "BOS");
  assert.equal(under.selectionKey, "UNDER");
  assert.equal(under.americanOdds, -108);
});

test("Edge cases: hashed CSS class, Unicode minus (−110), ½ fraction, NBSP/zero-width chars, empty row, and betslip exclusion", async () => {
  const dkHtml = `<!doctype html><html><body><main>
    <header><h1>CHI @ DEN</h1></header>
    <div data-testid="sportsbook-event-accordion">
      <div class="sportsbook-table-header__title">Player Points</div>
      <table><tbody>
        <tr class="sportsbook-table__body-row--a3f9c2">
          <th class="sportsbook-row-name">Nikola\u00A0Jokić\u200B</th>
          <td><div class="sportsbook-outcome-cell"><span class="sportsbook-outcome-cell__label">O</span><span class="sportsbook-outcome-cell__line">24\u00BD</span><span class="sportsbook-odds">\u2212110</span></div></td>
        </tr>
        <tr class="sportsbook-table__body-row--placeholder">
          <th class="sportsbook-row-name">Loading Row</th>
          <td><div class="sportsbook-outcome-cell"><span class="sportsbook-odds"></span></div></td>
        </tr>
      </tbody></table>
      <aside class="betslip-drawer">
        <table><tbody>
          <tr class="sportsbook-table__body-row">
            <th class="sportsbook-row-name">Slip Ghost</th>
            <td><div class="sportsbook-outcome-cell"><span class="sportsbook-outcome-cell__label">O</span><span class="sportsbook-outcome-cell__line">10.5</span><span class="sportsbook-odds">+110</span></div></td>
          </tr>
        </tbody></table>
      </aside>
    </div>
  </main></body></html>`;

  const dk = new DraftKingsAdapter({ now: CLOCK });
  const dkRes = await dk.detectVisibleMarkets(parseHTML(dkHtml).document);
  assert.equal(dkRes.status, "OK");
  assert.equal(dkRes.markets.length, 1); // placeholder & betslip rows both excluded
  const dkNorm = normalizeObservedMarket(dkRes.markets[0]!);
  assert.equal(dkNorm.playerNameKey, "nikola jokic");
  assert.equal(dkNorm.observed.line, 24.5);
  assert.equal(dkNorm.americanOdds, -110);

  const fdHtml = `<!doctype html><html><body><main>
    <header><h1>DAL @ BOS</h1></header>
    <div data-testid="market-group">
      <h2>Player Points</h2>
      <ul>
        <li class="market-row">
          <span class="participant-name">Luka\u00A0Dončić</span>
          <div role="button" aria-label="O 24\u00BD (\u2212115)">O 24.5</div>
        </li>
        <li class="market-row">
          <span class="participant-name">Missing Odds Player</span>
          <div role="button" aria-label="Over 18.5">O 18.5</div>
        </li>
      </ul>
    </div>
  </main></body></html>`;

  const fd = new FanDuelAdapter({ now: CLOCK });
  const fdRes = await fd.detectVisibleMarkets(parseHTML(fdHtml).document);
  assert.equal(fdRes.status, "OK");
  assert.equal(fdRes.markets.length, 1); // missing-odds row fails closed
  const fdNorm = normalizeObservedMarket(fdRes.markets[0]!);
  assert.equal(fdNorm.playerNameKey, "luka doncic");
  assert.equal(fdNorm.observed.line, 24.5);
  assert.equal(fdNorm.americanOdds, -115);
});

// ---------------------------------------------------------------------------
// DraftKings v1.2 ladder ("N+") layout, reduced from a manual capture.
// ---------------------------------------------------------------------------
const DK_LADDER_URL = "https://sportsbook.draftkings.com/event/la-clippers-%40-hou-rockets/35062797?category=odds&subcategory=points";

function loadLadder(url?: string, extraHtml = ""): Document {
  const html = fs.readFileSync(path.join(FIXTURES_DIR, "draftkings-ladder-points.html"), "utf8");
  const doc = parseHTML(`<!doctype html><html><body>${extraHtml}${html}</body></html>`).document;
  if (url) Object.defineProperty(doc, "location", { value: new URL(url), configurable: true });
  return doc;
}

test("DraftKings ladder capture: N+ rungs map to Over N-0.5 and pass the gate with URL market + matchup", async () => {
  const adapter = new DraftKingsAdapter({ now: CLOCK });
  const result = await adapter.detectVisibleMarkets(loadLadder(DK_LADDER_URL));
  assert.equal(result.status, "OK");
  assert.equal(result.markets.length, 3);
  assert.ok(result.confidence >= MIN_PREGAME_PARSE_CONFIDENCE);

  const gated = enforceParseConfidence(result.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 3);
  const norm = gated.matched.map((m) => normalizeObservedMarket(m));
  assert.deepEqual(norm.map((n) => n.observed.line), [19.5, 20.5, 24.5]);
  assert.deepEqual(norm.map((n) => n.americanOdds), [-110, -108, 145]);
  for (const n of norm) {
    assert.equal(n.marketTypeCode, "nba_points");
    assert.equal(n.selectionKey, "OVER");
    assert.equal(n.playerNameKey, "alperen sengun");
    assert.equal(n.observed.awayTeam, "LAC");
    assert.equal(n.observed.homeTeam, "HOU");
    assert.equal(n.observed.adapterVersion, "dk-pregame-observed-v1.2");
    assert.equal(n.observed.provenance, "visible-page");
  }
});

test("DraftKings ladder fails closed without market identity or matchup", async () => {
  const adapter = new DraftKingsAdapter({ now: CLOCK });

  // Captured row alone: no title element, no URL → market unknown → all UNMATCHED.
  let result = await adapter.detectVisibleMarkets(loadLadder());
  assert.equal(result.markets.length, 3);
  let gated = enforceParseConfidence(result.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 0);
  assert.equal(gated.unmatched.length, 3);

  // Event header h1 names the game, not the market: still unknown.
  result = await adapter.detectVisibleMarkets(loadLadder(undefined, "<h1>LA Clippers @ HOU Rockets</h1>"));
  gated = enforceParseConfidence(result.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 0);

  // Market from URL but no decodable matchup → below the gate.
  result = await adapter.detectVisibleMarkets(loadLadder("https://sportsbook.draftkings.com/event/35062797?subcategory=points"));
  gated = enforceParseConfidence(result.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 0);
  assert.ok(result.markets.every((m) => m.detectionConfidence < MIN_PREGAME_PARSE_CONFIDENCE));

  // Unrecognized subcategory → unknown market.
  result = await adapter.detectVisibleMarkets(loadLadder(DK_LADDER_URL.replace("subcategory=points", "subcategory=popular")));
  gated = enforceParseConfidence(result.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 0);
});

test("DraftKings ladder edge cases: bad rungs/odds skipped, missing player skipped, betslip excluded", async () => {
  const adapter = new DraftKingsAdapter({ now: CLOCK });
  const base = fs.readFileSync(path.join(FIXTURES_DIR, "draftkings-ladder-points.html"), "utf8");
  const mutate = (html: string) => {
    const doc = parseHTML(`<!doctype html><html><body>${html}</body></html>`).document;
    Object.defineProperty(doc, "location", { value: new URL(DK_LADDER_URL), configurable: true });
    return doc;
  };

  // A non-rung label ("Over 19.5" text, "20") and empty odds are skipped, not guessed.
  let res = await adapter.detectVisibleMarkets(mutate(base
    .replace('<span class="cb-selection-picker__selection-label">20+</span>', '<span class="cb-selection-picker__selection-label">20</span>')
    .replace('<span class="cb-selection-picker__selection-odds">-108</span>', '<span class="cb-selection-picker__selection-odds"></span>')));
  assert.equal(res.markets.length, 1);
  assert.equal(res.markets[0]!.line, 24.5);
  assert.equal(res.markets[0]!.americanOdds, 145);

  // Unicode minus is normalized by the shared odds parser.
  res = await adapter.detectVisibleMarkets(mutate(base.replace(">-110<", ">−110<")));
  assert.equal(res.markets.find((m) => m.line === 19.5)?.americanOdds, -110);

  // Missing player name → row dropped → degraded, nothing emitted.
  res = await adapter.detectVisibleMarkets(mutate(base.replace(">Alperen Sengun<", "><")));
  assert.equal(res.status, "ADAPTER_DEGRADED");
  assert.equal(res.markets.length, 0);

  // Ladder rows inside a betslip container are never read.
  res = await adapter.detectVisibleMarkets(mutate(`<div data-testid="betslip-container">${base}</div>`));
  assert.equal(res.status, "ADAPTER_DEGRADED");
  assert.equal(res.markets.length, 0);
});

test("DraftKings gap fixes: accordion+ladder coexistence, URL subcategories (pts-reb-ast/threes), disabled rungs, NY Knicks URL, table URL fallback", async () => {
  const adapter = new DraftKingsAdapter({ now: CLOCK });
  const base = fs.readFileSync(path.join(FIXTURES_DIR, "draftkings-ladder-points.html"), "utf8");
  const withDoc = (html: string, url: string) => {
    const doc = parseHTML(`<!doctype html><html><body>${html}</body></html>`).document;
    Object.defineProperty(doc, "location", { value: new URL(url), configurable: true });
    return doc;
  };

  // 1. Ladder rows coexist with an empty sportsbook-event-accordion wrapper (must not degrade).
  let res = await adapter.detectVisibleMarkets(
    withDoc(`<div data-testid="sportsbook-event-accordion">${base}</div>`, DK_LADDER_URL),
  );
  assert.equal(res.status, "OK");
  assert.equal(res.markets.length, 3);
  assert.ok(res.confidence >= MIN_PREGAME_PARSE_CONFIDENCE);

  // 2. URL subcategories: pts-reb-ast -> nba_pra, threes -> nba_threes, plus BOS Celtics @ NY Knicks matchup.
  const praUrl = "https://sportsbook.draftkings.com/event/bos-celtics-%40-ny-knicks/35062798?category=odds&subcategory=pts-reb-ast";
  res = await adapter.detectVisibleMarkets(withDoc(base, praUrl));
  assert.equal(res.status, "OK");
  assert.ok(res.confidence >= MIN_PREGAME_PARSE_CONFIDENCE);
  const praNorm = normalizeObservedMarket(res.markets[0]!);
  assert.equal(praNorm.marketTypeCode, "nba_pra");
  assert.equal(praNorm.observed.awayTeam, "BOS");
  assert.equal(praNorm.observed.homeTeam, "NYK");

  const threesUrl = "https://sportsbook.draftkings.com/event/bos-celtics-%40-ny-knicks/35062798?category=odds&subcategory=threes";
  res = await adapter.detectVisibleMarkets(withDoc(base, threesUrl));
  assert.equal(res.status, "OK");
  assert.equal(normalizeObservedMarket(res.markets[0]!).marketTypeCode, "nba_threes");

  // 3. Disabled / locked ladder button is marked SUSPENDED, never OPEN.
  const lockedHtml = base.replace(
    'data-testid="cb-selection-picker__selection-0"',
    'data-testid="cb-selection-picker__selection-0" disabled aria-disabled="true"',
  );
  res = await adapter.detectVisibleMarkets(withDoc(lockedHtml, DK_LADDER_URL));
  assert.equal(res.status, "OK");
  assert.equal(res.markets[0]!.marketStatus, "SUSPENDED");
  assert.equal(res.markets[1]!.marketStatus, "OPEN");

  // 4. Table row without title element falls back to URL ?subcategory= instead of <h1>CHI @ DEN</h1>,
  //    and disabled outcome cell is marked SUSPENDED.
  const tableHtml = `<header><h1>CHI @ DEN</h1></header>
    <div data-testid="sportsbook-event-accordion">
      <table><tbody>
        <tr class="sportsbook-table__body-row">
          <th class="sportsbook-row-name">Nikola Jokic</th>
          <td><div class="sportsbook-outcome-cell"><span class="sportsbook-outcome-cell__label">O</span><span class="sportsbook-outcome-cell__line">27.5</span><span class="sportsbook-odds">-110</span></div></td>
          <td><div class="sportsbook-outcome-cell" aria-disabled="true"><span class="sportsbook-outcome-cell__label">U</span><span class="sportsbook-outcome-cell__line">27.5</span><span class="sportsbook-odds">-110</span></div></td>
        </tr>
      </tbody></table>
    </div>`;
  res = await adapter.detectVisibleMarkets(
    withDoc(tableHtml, "https://sportsbook.draftkings.com/event/chi-bulls-%40-den-nuggets/35062799?subcategory=points"),
  );
  assert.equal(res.status, "OK");
  assert.equal(res.markets.length, 2);
  assert.ok(res.confidence >= MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(normalizeObservedMarket(res.markets[0]!).marketTypeCode, "nba_points");
  assert.equal(res.markets[0]!.marketStatus, "OPEN");
  assert.equal(res.markets[1]!.marketStatus, "SUSPENDED");
});
