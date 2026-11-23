/**
 * PropStein Live Sniper — FanDuel Read-Only Pregame Adapter (OBSERVED mode)
 * =========================================================================
 * Intelligence only: reads visible pregame player prop DOM elements for NFL
 * TD markets and NBA multi-stat markets. Never clicks, submits, or interacts
 * with wager controls. Never treats any DOM drive ID as a possession feed.
 */

import type { MarketChangeEvent, ObservedMarket } from "@propstein/core";
import { classifyMarketLabel, parseAmericanOddsText } from "@propstein/core";
import type { AdapterDetectionResult, VisibleMarketAdapter } from "./adapter.ts";
import { diffMarkets, type DifferHooks } from "../dom-extractor.ts";

export const FANDUEL_ADAPTER_VERSION = "fd-pregame-observed-v1.0";

export const FANDUEL_SELECTORS = {
  marketGroup: "div[data-testid='market-group'], section[data-testid='fd-pregame-props'], div[class*='live'] af-dropdown-list",
  marketRow: "div[data-testid='market-item'], li[class*='market'], div[data-testid='fd-prop-row']",
  playerName: "div[data-testid='player-name'], span[class*='participant']",
  marketTitle: "div[data-testid='market-title'], h2[class*='title']",
  line: "span[data-testid='prop-line'], span[class*='handicap']",
  selection: "span[data-testid='prop-selection']",
  oddsButton: "div[data-testid='odds-button'], button[class*='selection']",
} as const;

const HOST_ALLOWLIST = /^https:\/\/sportsbook\.fanduel\.com$/;
const SUPPORTED_PATHS = /^\/(navigation\/(nfl|nba)|football\/nfl|basketball\/nba|leagues\/(nfl|nba)|in-play|live)/;

function parseNumericLine(raw: string | null | undefined): number | undefined {
  if (!raw) return undefined;
  const m = raw.match(/-?\d+(?:\.\d+)?/);
  if (!m) return undefined;
  const v = Number(m[0]);
  return Number.isFinite(v) ? v : undefined;
}

export class FanDuelAdapter implements VisibleMarketAdapter {
  readonly platform = "fanduel" as const;
  readonly adapterVersion = FANDUEL_ADAPTER_VERSION;
  private readonly now: () => Date;
  private unsubscribe: (() => void) | undefined;

  constructor(opts: { now?: () => Date } = {}) {
    this.now = opts.now ?? (() => new Date());
  }

  isSupportedPage(url: URL): boolean {
    return HOST_ALLOWLIST.test(url.origin) && SUPPORTED_PATHS.test(url.pathname);
  }

  async detectVisibleMarkets(root: Document): Promise<AdapterDetectionResult> {
    const group = root.querySelector(FANDUEL_SELECTORS.marketGroup);
    if (!group) {
      return {
        markets: [],
        status: "ADAPTER_DEGRADED",
        confidence: 0,
        notes: ["pregame market container not found — FD layout may have changed or this page has no props"],
      };
    }
    const rows = group.querySelectorAll(FANDUEL_SELECTORS.marketRow);
    const markets: ObservedMarket[] = [];
    for (const row of Array.from(rows)) {
      const m = this.parseRow(group, row);
      if (m) markets.push(m);
    }
    if (markets.length === 0) {
      return {
        markets: [],
        status: "ADAPTER_DEGRADED",
        confidence: 0,
        notes: ["no rows parsed end-to-end — failing closed"],
      };
    }
    const minConfidence = Math.min(...markets.map((m) => m.detectionConfidence));
    return {
      markets,
      status: "OK",
      confidence: minConfidence,
      notes: ["OBSERVED · RESEARCH pregame read-only parse (alerting disabled)"],
    };
  }

  private parseRow(group: Element | null, row: Element): ObservedMarket | undefined {
    const playerName = row.querySelector(FANDUEL_SELECTORS.playerName)?.textContent?.trim() ?? "";
    if (!playerName) return undefined;

    const oddsEl = row.querySelector(FANDUEL_SELECTORS.oddsButton);
    const americanOdds = parseAmericanOddsText(oddsEl?.textContent ?? "");
    if (americanOdds === undefined) return undefined;

    const title =
      row.querySelector(FANDUEL_SELECTORS.marketTitle)?.textContent?.trim()
      ?? group?.querySelector(FANDUEL_SELECTORS.marketTitle)?.textContent?.trim()
      ?? "";

    const line = parseNumericLine(row.querySelector(FANDUEL_SELECTORS.line)?.textContent);
    const classification = classifyMarketLabel(title);
    const isTdMarket = classification.code === "anytime_td" || classification.code === "first_td" || classification.code === "two_plus_td";
    const selection = row.querySelector(FANDUEL_SELECTORS.selection)?.textContent?.trim()
      ?? row.getAttribute("data-selection")
      ?? (isTdMarket ? "YES" : "OVER");

    const leagueAttr = (row.getAttribute("data-league") ?? group?.getAttribute("data-league") ?? "").toUpperCase();
    const isNba = leagueAttr === "NBA" || classification.code.startsWith("nba_");
    const league = isNba ? "NBA" : "NFL";
    const sport = isNba ? "basketball" : "football";
    const eventId = row.getAttribute("data-event-id") ?? group?.getAttribute("data-event-id") ?? undefined;
    const homeTeam = row.getAttribute("data-home-team") ?? group?.getAttribute("data-home-team") ?? undefined;
    const awayTeam = row.getAttribute("data-away-team") ?? group?.getAttribute("data-away-team") ?? undefined;
    const rawStatus = (row.getAttribute("data-market-status") ?? "OPEN").toUpperCase();
    const marketStatus = rawStatus === "SUSPENDED" ? "SUSPENDED" : rawStatus === "CLOSED" ? "CLOSED" : "OPEN";

    let confidence = 0.95;
    if (classification.code === "unknown") confidence = 0.55;
    else if (!isTdMarket && line === undefined) confidence = 0.72;
    else if (!eventId && !(homeTeam && awayTeam)) confidence = 0.82;

    return {
      source: "fanduel",
      ...(eventId ? { eventId } : {}),
      ...(homeTeam ? { homeTeam } : {}),
      ...(awayTeam ? { awayTeam } : {}),
      sport,
      league,
      playerName,
      marketType: title,
      marketLabel: title,
      selection,
      ...(line !== undefined ? { line } : {}),
      americanOdds,
      marketStatus,
      observedAt: this.now().toISOString(),
      adapterVersion: this.adapterVersion,
      detectionConfidence: confidence,
      provenance: "visible-page",
    };
  }

  observeChanges(callback: (event: MarketChangeEvent) => void): () => void {
    const hooks: DifferHooks = {
      selectCards: (root) => Array.from(root.querySelectorAll(FANDUEL_SELECTORS.marketRow)),
      parseCard: (el) => {
        const doc = el.ownerDocument ?? document;
        return this.parseRow(doc.querySelector(FANDUEL_SELECTORS.marketGroup), el);
      },
    };
    this.unsubscribe = diffMarkets(hooks, callback, { targetSelector: FANDUEL_SELECTORS.marketGroup });
    return () => this.unsubscribe?.();
  }
}
