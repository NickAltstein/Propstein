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

test("DraftKings live-style drift fixture (sportsbook-table without data-testid='prop-card' or data-event-id) fails closed", async () => {
  const adapter = new DraftKingsAdapter({ now: CLOCK });
  const result = await adapter.detectVisibleMarkets(loadFixture("draftkings-live-drift.html"));
  // Current v1.0 selectors require tr[data-testid*='prop-row'] or div[data-testid='prop-card'],
  // so sportsbook-table__body-row fails closed as ADAPTER_DEGRADED with 0 markets.
  assert.equal(result.status, "ADAPTER_DEGRADED");
  assert.equal(result.markets.length, 0);
  assert.equal(result.confidence, 0);
});

test("FanDuel live-style drift fixture (aria-label buttons without data-testid='odds-button') fails closed", async () => {
  const adapter = new FanDuelAdapter({ now: CLOCK });
  const result = await adapter.detectVisibleMarkets(loadFixture("fanduel-live-drift.html"));
  // Current v1.0 selectors require data-testid='odds-button' or button[class*='selection'],
  // so role="button" aria-label markup fails closed as ADAPTER_DEGRADED with 0 markets.
  assert.equal(result.status, "ADAPTER_DEGRADED");
  assert.equal(result.markets.length, 0);
  assert.equal(result.confidence, 0);
});
