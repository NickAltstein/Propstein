/**
 * PropStein Live Sniper — DraftKings Read-Only Pregame Adapter (OBSERVED mode)
 * ============================================================================
 * Intelligence only: reads visible pregame player prop DOM elements for NFL
 * TD markets and NBA multi-stat markets. Supports both canonical data-testid
 * cards and DraftKings sportsbook-table__body-row Over/Under outcome cells.
 * Never clicks, submits, or interacts with wager controls. Never treats any
 * DOM drive ID as a possession feed.
 *
 * Gated behind api/extension_router.py capability flags + kill switch.
 * Rows with parse confidence < 0.90 are rejected as UNMATCHED.
 */

import type { MarketChangeEvent, ObservedMarket } from "@propstein/core";
import { classifyMarketLabel, parseAmericanOddsText } from "@propstein/core";
import {
  parseMatchupFromHeaderOrUrl,
  type AdapterDetectionResult,
  type VisibleMarketAdapter,
} from "./adapter.ts";
import { diffMarkets, type DifferHooks } from "../dom-extractor.ts";

export const DRAFTKINGS_ADAPTER_VERSION = "dk-pregame-observed-v1.1";

/** Versioned read-only pregame selector set. */
export const DRAFTKINGS_SELECTORS = {
  marketGroup: "div[data-testid='player-prop-markets'], div[data-testid='sportsbook-event-accordion'], section[data-testid='pregame-props']",
  marketRow: "tr[data-testid*='prop-row'], div[data-testid='prop-card'], div[data-testid='dk-prop-row'], tr[class*='sportsbook-table__body-row']",
  outcomeCell: ".sportsbook-outcome-cell",
  playerName: "[data-testid='prop-player-name'], .sportsbook-row-name",
  marketTitle: "[data-testid='prop-market-title'], [class*='market-title'], .sportsbook-table-header__title",
  line: "[data-testid='prop-line'], .sportsbook-outcome-cell__line",
  selection: "[data-testid='prop-selection'], .sportsbook-outcome-cell__label",
  oddsButton: "button[data-testid*='outcome-button'], div[data-testid*='odds'], .sportsbook-odds",
} as const;

const HOST_ALLOWLIST = /^https:\/\/sportsbook\.draftkings\.com$/;
const SUPPORTED_PATHS = /^\/(leagues\/(football\/nfl|nfl|basketball\/nba|nba)|event\/|in-game|live-betting)/;

function parseNumericLine(raw: string | null | undefined): number | undefined {
  if (!raw) return undefined;
  const cleaned = raw.replace(/\u00BD/g, ".5").replace(/[−–—]/g, "-");
  const m = cleaned.match(/-?\d+(?:\.\d+)?/);
  if (!m) return undefined;
  const v = Number(m[0]);
  return Number.isFinite(v) ? v : undefined;
}

function normalizeDkSelection(raw: string, isTdMarket: boolean): string {
  const t = raw.trim().toUpperCase();
  if (t === "O" || t === "OVER") return "OVER";
  if (t === "U" || t === "UNDER") return "UNDER";
  if (t === "YES" || t === "Y") return "YES";
  if (t === "NO" || t === "N") return "NO";
  return isTdMarket ? "YES" : "OVER";
}

function inferLeagueAndSport(root: Document, row: Element, marketTitle: string): { league: string; sport: string } {
  const attr = (row.getAttribute("data-league") ?? root.querySelector(DRAFTKINGS_SELECTORS.marketGroup)?.getAttribute("data-league") ?? "").toUpperCase();
  if (attr === "NBA") return { league: "NBA", sport: "basketball" };
  if (attr === "NFL") return { league: "NFL", sport: "football" };
  const code = classifyMarketLabel(marketTitle).code;
  if (code.startsWith("nba_")) return { league: "NBA", sport: "basketball" };
  return { league: "NFL", sport: "football" };
}

export class DraftKingsAdapter implements VisibleMarketAdapter {
  readonly platform = "draftkings" as const;
  readonly adapterVersion = DRAFTKINGS_ADAPTER_VERSION;
  private readonly now: () => Date;
  private unsubscribe: (() => void) | undefined;

  constructor(opts: { now?: () => Date } = {}) {
    this.now = opts.now ?? (() => new Date());
  }

  isSupportedPage(url: URL): boolean {
    return HOST_ALLOWLIST.test(url.origin) && SUPPORTED_PATHS.test(url.pathname);
  }

  async detectVisibleMarkets(root: Document): Promise<AdapterDetectionResult> {
    const group = root.querySelector(DRAFTKINGS_SELECTORS.marketGroup);
    if (!group) {
      return this.degraded("pregame prop market container not found — DK layout may have changed or this page has no props");
    }
    const rows = group.querySelectorAll(DRAFTKINGS_SELECTORS.marketRow);
    if (rows.length === 0) {
      return this.degraded("market container present but no rows matched the versioned selectors");
    }
    const markets: ObservedMarket[] = [];
    for (const row of Array.from(rows)) {
      markets.push(...this.parseRowMarkets(root, group, row));
    }
    if (markets.length === 0) {
      return this.degraded("rows found but none parsed end-to-end — failing closed");
    }
    const minConfidence = Math.min(...markets.map((m) => m.detectionConfidence));
    return {
      markets,
      status: "OK",
      confidence: minConfidence,
      notes: ["OBSERVED · RESEARCH pregame read-only parse (alerting disabled)"],
    };
  }

  private parseRowMarkets(root: Document, group: Element | null, row: Element): ObservedMarket[] {
    if (row.closest("[class*='slip'], [data-testid*='slip']")) return [];
    const playerName = row.querySelector(DRAFTKINGS_SELECTORS.playerName)?.textContent?.trim() ?? "";
    if (!playerName) return [];

    const marketTitle =
      row.querySelector(DRAFTKINGS_SELECTORS.marketTitle)?.textContent?.trim()
      ?? group?.querySelector(DRAFTKINGS_SELECTORS.marketTitle)?.textContent?.trim()
      ?? root.querySelector("h1")?.textContent?.trim()
      ?? "";

    const classification = classifyMarketLabel(marketTitle);
    const isTdMarket = classification.code === "anytime_td" || classification.code === "first_td" || classification.code === "two_plus_td";
    const { league, sport } = inferLeagueAndSport(root, row, marketTitle);

    const headerMatchup = parseMatchupFromHeaderOrUrl(root, league);
    const homeTeam = row.getAttribute("data-home-team") ?? group?.getAttribute("data-home-team") ?? headerMatchup.homeTeam;
    const awayTeam = row.getAttribute("data-away-team") ?? group?.getAttribute("data-away-team") ?? headerMatchup.awayTeam;
    const eventId = row.getAttribute("data-event-id") ?? group?.getAttribute("data-event-id") ?? undefined;
    const rawStatus = (row.getAttribute("data-market-status") ?? "OPEN").toUpperCase();
    const marketStatus = rawStatus === "SUSPENDED" ? "SUSPENDED" : rawStatus === "CLOSED" ? "CLOSED" : "OPEN";

    // Sportsbook table rows often contain paired Over / Under outcome cells.
    const outcomeCells = Array.from(row.querySelectorAll(DRAFTKINGS_SELECTORS.outcomeCell));
    const containers: Element[] = outcomeCells.length > 0 ? outcomeCells : [row];
    const results: ObservedMarket[] = [];

    for (const cell of containers) {
      const oddsEl = cell.querySelector(DRAFTKINGS_SELECTORS.oddsButton);
      const americanOdds = parseAmericanOddsText(oddsEl?.textContent ?? "");
      if (americanOdds === undefined) continue;

      const line = parseNumericLine(cell.querySelector(DRAFTKINGS_SELECTORS.line)?.textContent);
      const rawSelection = cell.querySelector(DRAFTKINGS_SELECTORS.selection)?.textContent?.trim()
        ?? row.getAttribute("data-selection")
        ?? "";
      const selection = normalizeDkSelection(rawSelection, isTdMarket);

      let confidence = 0.95;
      if (classification.code === "unknown") confidence = 0.55;
      else if (!isTdMarket && line === undefined) confidence = 0.72;
      else if (!eventId && !(homeTeam && awayTeam)) confidence = 0.82;
      else if (!eventId && homeTeam && awayTeam) confidence = 0.93;

      results.push({
        source: "draftkings",
        ...(eventId ? { eventId } : {}),
        ...(homeTeam ? { homeTeam } : {}),
        ...(awayTeam ? { awayTeam } : {}),
        sport,
        league,
        playerName,
        marketType: marketTitle,
        marketLabel: marketTitle,
        selection,
        ...(line !== undefined ? { line } : {}),
        americanOdds,
        marketStatus,
        observedAt: this.now().toISOString(),
        adapterVersion: this.adapterVersion,
        detectionConfidence: confidence,
        provenance: "visible-page",
      });
    }
    return results;
  }

  private degraded(reason: string): AdapterDetectionResult {
    return {
      markets: [],
      status: "ADAPTER_DEGRADED",
      confidence: 0,
      notes: [reason, "OBSERVED · RESEARCH read-only adapter failed closed"],
    };
  }

  observeChanges(callback: (event: MarketChangeEvent) => void): () => void {
    const hooks: DifferHooks = {
      selectCards: (root) => Array.from(root.querySelectorAll(DRAFTKINGS_SELECTORS.marketRow)),
      parseCard: (el) => {
        const doc = el.ownerDocument ?? document;
        return this.parseRowMarkets(doc, doc.querySelector(DRAFTKINGS_SELECTORS.marketGroup), el)[0];
      },
    };
    this.unsubscribe = diffMarkets(hooks, callback, { targetSelector: DRAFTKINGS_SELECTORS.marketGroup });
    return () => this.unsubscribe?.();
  }
}
