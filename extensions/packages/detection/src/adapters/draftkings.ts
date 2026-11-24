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

export const DRAFTKINGS_ADAPTER_VERSION = "dk-pregame-observed-v1.2";

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
  // v1.2 ladder ("N+") layout, from a manual capture of /event/<matchup>/<id>?subcategory=points
  ladderRow: "[data-testid='market-template']",
  ladderPlayer: "p.cb-market__label--truncate-strings, a[data-testid='player-page-link']",
  ladderPicker: "[data-testid='cb-selection-picker']",
  ladderButton: "button[data-testid^='cb-selection-picker__selection-']",
  ladderLabel: ".cb-selection-picker__selection-label",
  ladderOdds: ".cb-selection-picker__selection-odds",
} as const;

/** "20+" → 19.5 (Over). Anything else is not a ladder rung. */
function parseLadderRung(raw: string | null | undefined): number | undefined {
  const m = /^(\d{1,3})\+$/.exec((raw ?? "").replace(/[\s\u200B-\u200D\uFEFF]/g, ""));
  if (!m) return undefined;
  const n = Number(m[1]);
  return n >= 1 ? n - 0.5 : undefined;
}

/** Location of the document, when the runtime exposes one. */
function documentLocation(root: Document): { pathname?: string; search?: string } | undefined {
  const loc = (root as unknown as { location?: { pathname?: string; search?: string } }).location;
  return loc && typeof loc === "object" ? loc : undefined;
}

/** Market label from the URL's own subcategory (e.g. ?subcategory=points). */
function marketFromUrl(root: Document): string {
  const search = documentLocation(root)?.search;
  if (!search) return "";
  try {
    return (new URLSearchParams(search).get("subcategory") ?? "").replace(/[-_]+/g, " ").trim();
  } catch {
    return "";
  }
}

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

function isElementDisabled(el: Element | null | undefined): boolean {
  if (!el) return false;
  return (
    el.hasAttribute("disabled")
    || el.getAttribute("aria-disabled")?.toLowerCase() === "true"
    || /\b(disabled|locked|suspended)\b/i.test(el.getAttribute("class") ?? "")
  );
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
    if (!group && root.querySelector(DRAFTKINGS_SELECTORS.ladderRow)) {
      return this.detectLadderMarkets(root);
    }
    if (!group) {
      return this.degraded("pregame prop market container not found — DK layout may have changed or this page has no props");
    }
    const rows = group.querySelectorAll(DRAFTKINGS_SELECTORS.marketRow);
    if (rows.length === 0) {
      if (root.querySelector(DRAFTKINGS_SELECTORS.ladderRow)) {
        return this.detectLadderMarkets(root);
      }
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

  private parseRowMarkets(root: Document, group: Element | null, row: Element, onlyCell?: Element): ObservedMarket[] {
    if (row.closest("[class*='slip'], [data-testid*='slip']")) return [];
    const playerName = row.querySelector(DRAFTKINGS_SELECTORS.playerName)?.textContent?.trim() ?? "";
    if (!playerName) return [];

    const marketTitle =
      row.querySelector(DRAFTKINGS_SELECTORS.marketTitle)?.textContent?.trim()
      ?? group?.querySelector(DRAFTKINGS_SELECTORS.marketTitle)?.textContent?.trim()
      ?? (marketFromUrl(root) || root.querySelector("h1")?.textContent?.trim())
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
    const containers: Element[] = onlyCell ? [onlyCell] : outcomeCells.length > 0 ? outcomeCells : [row];
    const results: ObservedMarket[] = [];

    for (const cell of containers) {
      const parsed = this.parseOutcomeCell(
        cell,
        row,
        playerName,
        marketTitle,
        classification.code,
        isTdMarket,
        league,
        sport,
        homeTeam,
        awayTeam,
        eventId,
        marketStatus,
      );
      if (parsed) results.push(parsed);
    }
    return results;
  }

  private parseOutcomeCell(
    cell: Element,
    row: Element,
    playerName: string,
    marketTitle: string,
    classificationCode: string,
    isTdMarket: boolean,
    league: string,
    sport: string,
    homeTeam: string | undefined,
    awayTeam: string | undefined,
    eventId: string | undefined,
    rowStatus: "OPEN" | "SUSPENDED" | "CLOSED",
  ): ObservedMarket | undefined {
    const oddsEl = cell.querySelector(DRAFTKINGS_SELECTORS.oddsButton);
    const americanOdds = parseAmericanOddsText(oddsEl?.textContent ?? "");
    if (americanOdds === undefined) return undefined;

    const line = parseNumericLine(cell.querySelector(DRAFTKINGS_SELECTORS.line)?.textContent);
    const rawSelection = cell.querySelector(DRAFTKINGS_SELECTORS.selection)?.textContent?.trim()
      ?? row.getAttribute("data-selection")
      ?? "";
    const selection = normalizeDkSelection(rawSelection, isTdMarket);
    const marketStatus =
      rowStatus === "OPEN" && (isElementDisabled(cell) || isElementDisabled(oddsEl))
        ? "SUSPENDED"
        : rowStatus;

    let confidence = 0.95;
    if (classificationCode === "unknown") confidence = 0.55;
    else if (!isTdMarket && line === undefined) confidence = 0.72;
    else if (!eventId && !(homeTeam && awayTeam)) confidence = 0.82;
    else if (!eventId && homeTeam && awayTeam) confidence = 0.93;

    return {
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
    };
  }

  /** v1.2 ladder layout: one row per player, one button per "N+" rung. */
  private detectLadderMarkets(root: Document): AdapterDetectionResult {
    const markets: ObservedMarket[] = [];
    for (const row of Array.from(root.querySelectorAll(DRAFTKINGS_SELECTORS.ladderRow))) {
      markets.push(...this.parseLadderRow(root, row));
    }
    if (markets.length === 0) {
      return this.degraded("ladder rows found but none parsed end-to-end — failing closed");
    }
    return {
      markets,
      status: "OK",
      confidence: Math.min(...markets.map((m) => m.detectionConfidence)),
      notes: ["OBSERVED · RESEARCH pregame ladder read-only parse (alerting disabled)"],
    };
  }

  private parseLadderRow(root: Document, row: Element, onlyButton?: Element): ObservedMarket[] {
    if (row.closest("[class*='slip'], [data-testid*='slip']")) return [];
    const playerEl = row.querySelector(DRAFTKINGS_SELECTORS.ladderPlayer);
    const playerName = playerEl?.textContent?.replace(/\s+/g, " ").trim() ?? "";
    if (!playerName) return [];
    const picker = row.querySelector(DRAFTKINGS_SELECTORS.ladderPicker);
    if (!picker) return [];

    // Market identity: an explicit title element, else the page URL's own
    // subcategory. The event header (h1) names the game, not the market.
    const titleText = row.querySelector(DRAFTKINGS_SELECTORS.marketTitle)?.textContent?.trim() ?? "";
    const marketTitle = titleText || marketFromUrl(root);
    const classification = classifyMarketLabel(marketTitle);
    const { league, sport } = inferLeagueAndSport(root, row, marketTitle);
    const matchup = parseMatchupFromHeaderOrUrl(root, league);
    const eventId = row.getAttribute("data-event-id") ?? undefined;
    const rawStatus = (row.getAttribute("data-market-status") ?? "OPEN").toUpperCase();
    const marketStatus = rawStatus === "SUSPENDED" ? "SUSPENDED" : rawStatus === "CLOSED" ? "CLOSED" : "OPEN";

    const buttons = onlyButton ? [onlyButton] : Array.from(picker.querySelectorAll(DRAFTKINGS_SELECTORS.ladderButton));
    const results: ObservedMarket[] = [];
    for (const button of buttons) {
      const parsed = this.parseLadderButton(
        button,
        playerName,
        marketTitle,
        classification.code,
        league,
        sport,
        matchup.homeTeam,
        matchup.awayTeam,
        eventId,
        marketStatus,
      );
      if (parsed) results.push(parsed);
    }
    return results;
  }

  private parseLadderButton(
    button: Element,
    playerName: string,
    marketTitle: string,
    classificationCode: string,
    league: string,
    sport: string,
    homeTeam: string | undefined,
    awayTeam: string | undefined,
    eventId: string | undefined,
    rowStatus: "OPEN" | "SUSPENDED" | "CLOSED",
  ): ObservedMarket | undefined {
    const line = parseLadderRung(button.querySelector(DRAFTKINGS_SELECTORS.ladderLabel)?.textContent);
    const americanOdds = parseAmericanOddsText(button.querySelector(DRAFTKINGS_SELECTORS.ladderOdds)?.textContent ?? "");
    if (line === undefined || americanOdds === undefined) return undefined;

    const marketStatus =
      rowStatus === "OPEN" && isElementDisabled(button)
        ? "SUSPENDED"
        : rowStatus;

    // Same confidence ladder as the table path; nothing is promoted for parsing cleanly.
    let confidence = 0.95;
    if (classificationCode === "unknown") confidence = 0.55;
    else if (!eventId && !(homeTeam && awayTeam)) confidence = 0.82;
    else if (!eventId) confidence = 0.93;

    return {
      source: "draftkings",
      ...(eventId ? { eventId } : {}),
      ...(homeTeam ? { homeTeam } : {}),
      ...(awayTeam ? { awayTeam } : {}),
      sport,
      league,
      playerName,
      marketType: marketTitle,
      marketLabel: marketTitle,
      selection: "OVER",
      line,
      americanOdds,
      marketStatus,
      observedAt: this.now().toISOString(),
      adapterVersion: this.adapterVersion,
      detectionConfidence: confidence,
      provenance: "visible-page",
    };
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
      selectCards: (root) => {
        const rows = Array.from(root.querySelectorAll(DRAFTKINGS_SELECTORS.marketRow));
        if (rows.length > 0) {
          const targets: Element[] = [];
          for (const row of rows) {
            const cells = Array.from(row.querySelectorAll(DRAFTKINGS_SELECTORS.outcomeCell));
            targets.push(...(cells.length > 0 ? cells : [row]));
          }
          return targets;
        }
        if (root.querySelector(DRAFTKINGS_SELECTORS.ladderRow)) {
          return Array.from(root.querySelectorAll(DRAFTKINGS_SELECTORS.ladderButton));
        }
        return [];
      },
      parseCard: (el) => {
        const doc = el.ownerDocument ?? document;
        if (el.matches(DRAFTKINGS_SELECTORS.ladderButton)) {
          const row = el.closest(DRAFTKINGS_SELECTORS.ladderRow);
          return row ? this.parseLadderRow(doc, row, el)[0] : undefined;
        }
        const row = el.matches(DRAFTKINGS_SELECTORS.marketRow)
          ? el
          : el.closest(DRAFTKINGS_SELECTORS.marketRow);
        if (!row) return undefined;
        const onlyCell = el.matches(DRAFTKINGS_SELECTORS.outcomeCell) ? el : undefined;
        return this.parseRowMarkets(doc, doc.querySelector(DRAFTKINGS_SELECTORS.marketGroup), row, onlyCell)[0];
      },
    };
    this.unsubscribe = diffMarkets(hooks, callback, {
      targetSelector: `${DRAFTKINGS_SELECTORS.marketGroup}, ${DRAFTKINGS_SELECTORS.ladderRow}`,
    });
    return () => this.unsubscribe?.();
  }
}
