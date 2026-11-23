/**
 * PropStein Live Sniper — PrizePicks Read-Only Pregame Adapter (OBSERVED mode)
 * ============================================================================
 * Intelligence only: reads visible pregame projection cards for NFL/NBA props.
 * Never interacts with entry slips or submission controls.
 */

import type { MarketChangeEvent, ObservedMarket } from "@propstein/core";
import { classifyMarketLabel } from "@propstein/core";
import type { AdapterDetectionResult, VisibleMarketAdapter } from "./adapter.ts";
import { diffMarkets, type DifferHooks } from "../dom-extractor.ts";

export const PRIZEPICKS_ADAPTER_VERSION = "pp-pregame-observed-v1.0";

export const PRIZEPICKS_SELECTORS = {
  board: "div[data-testid='projections-board'], section[data-testid='pp-board']",
  card: "div[data-testid='projection-card'], li.projection",
  playerName: "[data-testid='player-name'], .name",
  statType: "[data-testid='stat-type'], .stat-type",
  line: "[data-testid='projection-line'], .score",
} as const;

const HOST_ALLOWLIST = /^https:\/\/app\.prizepicks\.com$/;

export class PrizePicksAdapter implements VisibleMarketAdapter {
  readonly platform = "prizepicks" as const;
  readonly adapterVersion = PRIZEPICKS_ADAPTER_VERSION;
  private readonly now: () => Date;
  private unsubscribe: (() => void) | undefined;

  constructor(opts: { now?: () => Date } = {}) {
    this.now = opts.now ?? (() => new Date());
  }

  isSupportedPage(url: URL): boolean {
    return HOST_ALLOWLIST.test(url.origin);
  }

  async detectVisibleMarkets(root: Document): Promise<AdapterDetectionResult> {
    const board = root.querySelector(PRIZEPICKS_SELECTORS.board);
    if (!board) {
      return {
        markets: [],
        status: "ADAPTER_DEGRADED",
        confidence: 0,
        notes: ["PrizePicks projections board not found — failing closed"],
      };
    }
    const cards = board.querySelectorAll(PRIZEPICKS_SELECTORS.card);
    const markets: ObservedMarket[] = [];
    for (const card of Array.from(cards)) {
      const m = this.parseCard(board, card);
      if (m) markets.push(m);
    }
    if (markets.length === 0) {
      return {
        markets: [],
        status: "ADAPTER_DEGRADED",
        confidence: 0,
        notes: ["no PrizePicks cards parsed end-to-end — failing closed"],
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

  private parseCard(board: Element | null, card: Element): ObservedMarket | undefined {
    const playerName = card.querySelector(PRIZEPICKS_SELECTORS.playerName)?.textContent?.trim() ?? "";
    const statType = card.querySelector(PRIZEPICKS_SELECTORS.statType)?.textContent?.trim() ?? "";
    const lineRaw = card.querySelector(PRIZEPICKS_SELECTORS.line)?.textContent?.trim() ?? "";
    const lineMatch = lineRaw.match(/-?\d+(?:\.\d+)?/);
    const line = lineMatch ? Number(lineMatch[0]) : undefined;
    if (!playerName || !statType || line === undefined || !Number.isFinite(line)) return undefined;

    const classification = classifyMarketLabel(statType);
    const leagueAttr = (card.getAttribute("data-league") ?? board?.getAttribute("data-league") ?? "").toUpperCase();
    const isNba = leagueAttr === "NBA" || classification.code.startsWith("nba_");
    const eventId = card.getAttribute("data-event-id") ?? board?.getAttribute("data-event-id") ?? undefined;
    const homeTeam = card.getAttribute("data-home-team") ?? undefined;
    const awayTeam = card.getAttribute("data-away-team") ?? undefined;

    let confidence = 0.94;
    if (classification.code === "unknown") confidence = 0.55;
    else if (!eventId && !(homeTeam && awayTeam)) confidence = 0.80;

    return {
      source: "prizepicks",
      ...(eventId ? { eventId } : {}),
      ...(homeTeam ? { homeTeam } : {}),
      ...(awayTeam ? { awayTeam } : {}),
      sport: isNba ? "basketball" : "football",
      league: isNba ? "NBA" : "NFL",
      playerName,
      marketType: statType,
      marketLabel: statType,
      selection: "OVER",
      line,
      // Standard 2-pick power-play implied single-leg price (-119 / 1.84) for reference only.
      americanOdds: -119,
      marketStatus: "OPEN",
      observedAt: this.now().toISOString(),
      adapterVersion: this.adapterVersion,
      detectionConfidence: confidence,
      provenance: "visible-page",
    };
  }

  observeChanges(callback: (event: MarketChangeEvent) => void): () => void {
    const hooks: DifferHooks = {
      selectCards: (root) => Array.from(root.querySelectorAll(PRIZEPICKS_SELECTORS.card)),
      parseCard: (el) => {
        const doc = el.ownerDocument ?? document;
        return this.parseCard(doc.querySelector(PRIZEPICKS_SELECTORS.board), el);
      },
    };
    this.unsubscribe = diffMarkets(hooks, callback, { targetSelector: PRIZEPICKS_SELECTORS.board });
    return () => this.unsubscribe?.();
  }
}
