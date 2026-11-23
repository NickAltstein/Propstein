/**
 * Integration tests — Pregame OBSERVED mode (DraftKings, FanDuel, PrizePicks).
 * Covers:
 *   1. linkedom DOM fixture parsing for NFL TD & NBA multi-stat markets
 *   2. >= 0.90 parse confidence gate (below 0.90 → UNMATCHED)
 *   3. api/extension_router.py capability gate & kill switch fail-closed halt
 *   4. OBSERVED · RESEARCH label with zero ALERTABLE transitions
 *   5. Panel view model + 1200x675 receipt card SVG + Share to X (no Kelly)
 *   6. Static audit confirming zero bet-placement code paths
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { parseHTML } from "linkedom";
import {
  normalizeObservedMarket,
  MarketMatcher,
  evaluateSignal,
  signalStatusLabel,
  DEFAULT_SIGNAL_ENGINE_CONFIG,
} from "@propstein/core";
import {
  DraftKingsAdapter,
  FanDuelAdapter,
  PrizePicksAdapter,
  AdapterRegistry,
  enforceParseConfidence,
  MIN_PREGAME_PARSE_CONFIDENCE,
  type RouterHealthGate,
} from "@propstein/detection";
import { toSignalCardViewModel } from "../../packages/ui/src/view-models.ts";
import {
  renderReceiptCardSvg,
  RECEIPT_CARD_WIDTH,
  RECEIPT_CARD_HEIGHT,
} from "../../packages/ui/src/receipt-card.ts";

const CLOCK = () => new Date("2026-11-23T18:00:00.000Z");

const DK_PREGAME_HTML = `<!doctype html>
<html><body>
  <div data-testid="player-prop-markets" data-event-id="2026_11_TEN_DAL" data-home-team="DAL" data-away-team="TEN" data-league="NFL">
    <div data-testid="prop-card" data-market-status="OPEN">
      <span data-testid="prop-player-name">CeeDee Lamb</span>
      <span data-testid="prop-market-title">Anytime Touchdown Scorer</span>
      <button data-testid="outcome-button">-105</button>
    </div>
    <div data-testid="prop-card" data-market-status="OPEN" data-league="NBA" data-event-id="nba-20261123-DEN-CHI" data-home-team="DEN" data-away-team="CHI">
      <span data-testid="prop-player-name">Nikola Jokić</span>
      <span data-testid="prop-market-title">Player Points</span>
      <span data-testid="prop-line">27.5</span>
      <span data-testid="prop-selection">Over</span>
      <button data-testid="outcome-button">-110</button>
    </div>
    <div data-testid="prop-card" data-market-status="OPEN">
      <span data-testid="prop-player-name">Ambiguous Mystery</span>
      <span data-testid="prop-market-title">Mystery Unmapped Prop</span>
      <button data-testid="outcome-button">+150</button>
    </div>
  </div>
</body></html>`;

const FD_PREGAME_HTML = `<!doctype html>
<html><body>
  <div data-testid="market-group" data-event-id="nba-20261123-DAL-BOS" data-home-team="BOS" data-away-team="DAL" data-league="NBA">
    <div data-testid="market-item" data-market-status="OPEN">
      <div data-testid="player-name">Luka Dončić</div>
      <div data-testid="market-title">Points + Rebounds + Assists</div>
      <span data-testid="prop-line">48.5</span>
      <span data-testid="prop-selection">Over</span>
      <div data-testid="odds-button">-115</div>
    </div>
    <div data-testid="market-item" data-market-status="OPEN" data-league="NFL" data-event-id="2026_11_TEN_DAL" data-home-team="DAL" data-away-team="TEN">
      <div data-testid="player-name">CeeDee Lamb</div>
      <div data-testid="market-title">2+ Touchdowns</div>
      <div data-testid="odds-button">+460</div>
    </div>
  </div>
</body></html>`;

const PP_PREGAME_HTML = `<!doctype html>
<html><body>
  <div data-testid="projections-board" data-league="NBA" data-event-id="nba-20261123-DEN-CHI">
    <div data-testid="projection-card" data-home-team="DEN" data-away-team="CHI">
      <span data-testid="player-name">Nikola Jokić</span>
      <span data-testid="stat-type">Pts+Rebs+Asts</span>
      <span data-testid="projection-line">49.5</span>
    </div>
  </div>
</body></html>`;

test("DraftKings pregame fixture parses NFL TD + NBA Points with >= 0.90 confidence and routes low-confidence rows to UNMATCHED", async () => {
  const adapter = new DraftKingsAdapter({ now: CLOCK });
  const { document } = parseHTML(DK_PREGAME_HTML);
  const res = await adapter.detectVisibleMarkets(document);
  assert.equal(res.status, "OK");
  assert.equal(res.markets.length, 3);

  const gated = enforceParseConfidence(res.markets, MIN_PREGAME_PARSE_CONFIDENCE);
  assert.equal(gated.matched.length, 2);
  assert.equal(gated.unmatched.length, 1);
  assert.equal(gated.unmatched[0]!.status, "UNMATCHED");
  assert.equal(gated.unmatched[0]!.market.playerName, "Ambiguous Mystery");

  const td = normalizeObservedMarket(gated.matched[0]!);
  assert.equal(td.marketTypeCode, "anytime_td");
  assert.equal(td.playerNameKey, "ceedee lamb");

  const nba = normalizeObservedMarket(gated.matched[1]!);
  assert.equal(nba.marketTypeCode, "nba_points");
  assert.equal(nba.playerNameKey, "nikola jokic");
  assert.equal(nba.observed.line, 27.5);
});

test("FanDuel pregame fixture parses NBA PRA (Unicode) and NFL 2+ TD cleanly", async () => {
  const adapter = new FanDuelAdapter({ now: CLOCK });
  const { document } = parseHTML(FD_PREGAME_HTML);
  const res = await adapter.detectVisibleMarkets(document);
  assert.equal(res.status, "OK");
  const gated = enforceParseConfidence(res.markets, 0.90);
  assert.equal(gated.matched.length, 2);
  assert.equal(gated.unmatched.length, 0);

  const pra = normalizeObservedMarket(gated.matched[0]!);
  assert.equal(pra.marketTypeCode, "nba_pra");
  assert.equal(pra.playerNameKey, "luka doncic");
  assert.equal(pra.observed.line, 48.5);

  const twoPlus = normalizeObservedMarket(gated.matched[1]!);
  assert.equal(twoPlus.marketTypeCode, "two_plus_td");
  assert.equal(twoPlus.americanOdds, 460);
});

test("PrizePicks pregame fixture parses NBA PRA projection card with >= 0.90 confidence", async () => {
  const adapter = new PrizePicksAdapter({ now: CLOCK });
  assert.equal(adapter.isSupportedPage(new URL("https://app.prizepicks.com/board")), true);
  assert.equal(adapter.isSupportedPage(new URL("https://evil.com/board")), false);
  const { document } = parseHTML(PP_PREGAME_HTML);
  const res = await adapter.detectVisibleMarkets(document);
  assert.equal(res.status, "OK");
  const gated = enforceParseConfidence(res.markets, 0.90);
  assert.equal(gated.matched.length, 1);
  const pra = normalizeObservedMarket(gated.matched[0]!);
  assert.equal(pra.marketTypeCode, "nba_pra");
  assert.equal(pra.playerNameKey, "nikola jokic");
  assert.equal(pra.observed.line, 49.5);
});

test("Router capability gate halts adapters when killSwitch is on or capability is missing", () => {
  const registry = new AdapterRegistry();
  registry.register(new DraftKingsAdapter({ now: CLOCK }));
  registry.register(new FanDuelAdapter({ now: CLOCK }));
  registry.register(new PrizePicksAdapter({ now: CLOCK }));

  const dkUrl = new URL("https://sportsbook.draftkings.com/leagues/basketball/nba");
  const fdUrl = new URL("https://sportsbook.fanduel.com/basketball/nba");

  // 1. Kill switch ON → HALTED
  const killedGate: RouterHealthGate = {
    ok: false,
    killSwitch: true,
    capabilities: {
      mode: "HALTED",
      alertingEnabled: false,
      liveDriveFeedEnabled: false,
      betPlacementEnabled: false,
      minParseConfidence: 0.90,
      adapters: { draftkings: false, fanduel: false, prizepicks: false },
      markets: [],
    },
  };
  const rKilled = registry.resolveWithGate(dkUrl, killedGate);
  assert.equal(rKilled.adapter, undefined);
  assert.equal(rKilled.mode, "HALTED");

  // 2. Missing capability for FanDuel while DraftKings is enabled
  const observedGate: RouterHealthGate = {
    ok: true,
    killSwitch: false,
    capabilities: {
      mode: "OBSERVED_RESEARCH",
      alertingEnabled: false,
      liveDriveFeedEnabled: false,
      betPlacementEnabled: false,
      minParseConfidence: 0.90,
      adapters: { draftkings: true, fanduel: false, prizepicks: true },
      markets: ["anytime_td", "nba_points", "nba_pra"],
    },
  };
  assert.ok(registry.resolveWithGate(dkUrl, observedGate).adapter instanceof DraftKingsAdapter);
  assert.equal(registry.resolveWithGate(fdUrl, observedGate).adapter, undefined);

  // 3. Missing gate object → HALTED
  assert.equal(registry.resolveWithGate(dkUrl, null).adapter, undefined);
});

test("OBSERVED mode never transitions to ALERTABLE and renders OBSERVED · RESEARCH + 1200x675 receipt without Kelly", async () => {
  const adapter = new DraftKingsAdapter({ now: CLOCK });
  const { document } = parseHTML(DK_PREGAME_HTML);
  const res = await adapter.detectVisibleMarkets(document);
  const gated = enforceParseConfidence(res.markets, 0.90);
  const jokicObs = {
    ...gated.matched[1]!,
    sourceTimestamp: "2026-11-23T17:59:55.000Z",
  };
  const normalized = normalizeObservedMarket(jokicObs);
  const matcher = new MarketMatcher({
    roster: [
      { playerId: "203999", playerName: "Nikola Jokic", team: "DEN", eventId: "nba-20261123-DEN-CHI", league: "NBA" },
    ],
    coverage: [
      { key: { league: "NBA", eventId: "nba-20261123-DEN-CHI", playerId: "203999", marketType: "nba_points", line: 27.5 }, available: true },
    ],
  });
  const match = matcher.match(normalized);
  assert.equal(match.status, "MATCHED");

  const snap = evaluateSignal({
    signalKey: "draftkings|nba-20261123-DEN-CHI|203999|nba_points|OVER|27.5",
    observed: jokicObs,
    normalized,
    match,
    model: { probability: 0.65, modelVersion: "NBA-MULTISTAT-V1", confidence: "calibrated" },
    nowMs: CLOCK().getTime(),
    config: { ...DEFAULT_SIGNAL_ENGINE_CONFIG, minDetectionConfidence: 0.90, observedOnly: true },
  });

  // Even with calibrated p=0.65 vs -110, OBSERVED mode MUST NOT transition to ALERTABLE.
  assert.equal(snap.state, "OBSERVED");
  assert.notEqual(snap.state, "ALERTABLE");
  assert.equal(signalStatusLabel(snap), "OBSERVED · RESEARCH");

  const vm = toSignalCardViewModel(snap);
  assert.equal(vm.statusLabel, "OBSERVED · RESEARCH");
  assert.equal(vm.isResearchGated, true);
  assert.match(vm.shortHash, /^[0-9a-f]{8}$/);
  assert.match(vm.shareToXUrl, /^https:\/\/x\.com\/intent\/post\?text=/);
  assert.doesNotMatch(vm.copyText, /\bkelly\b/i);

  const svg = renderReceiptCardSvg(
    {
      signalKey: vm.key,
      playerName: vm.playerName,
      marketName: vm.marketName,
      leagueLine: vm.leagueLine,
      fairDisplay: vm.fairDisplay,
      bookDisplay: vm.bookDisplay,
      deltaDisplay: vm.deltaDisplay,
      statusLabel: vm.statusLabel,
      source: vm.source,
      evaluatedAt: snap.evaluatedAt,
    },
    vm.shortHash,
  );
  assert.ok(svg.includes(`width="${RECEIPT_CARD_WIDTH}" height="${RECEIPT_CARD_HEIGHT}"`));
  assert.ok(svg.includes("OBSERVED · RESEARCH"));
  assert.ok(svg.includes(`#${vm.shortHash}`));
  assert.doesNotMatch(svg, /\bkelly\b/i);
});

test("Static audit: zero bet-placement code paths exist in detection adapters or UI", () => {
  const adapterDir = path.resolve("packages/detection/src/adapters");
  const uiDir = path.resolve("packages/ui/src");
  const forbiddenWager = /\b(placeBet|submitBet|submitWager|addToBetslip|betslip|requestSubmit)\b/i;
  const forbiddenClick = /\.click\(\)/;

  for (const entry of fs.readdirSync(adapterDir, { withFileTypes: true })) {
    if (!entry.isFile() || !/\.(ts|tsx)$/.test(entry.name)) continue;
    const full = path.join(adapterDir, entry.name);
    const src = fs.readFileSync(full, "utf8");
    assert.doesNotMatch(src, forbiddenWager, `Forbidden wager pattern found in ${full}`);
    assert.doesNotMatch(src, forbiddenClick, `Forbidden DOM .click() found in read-only adapter ${full}`);
  }
  for (const entry of fs.readdirSync(uiDir, { withFileTypes: true })) {
    if (!entry.isFile() || !/\.(ts|tsx)$/.test(entry.name)) continue;
    const full = path.join(uiDir, entry.name);
    const src = fs.readFileSync(full, "utf8");
    assert.doesNotMatch(src, forbiddenWager, `Forbidden wager pattern found in ${full}`);
  }
});
