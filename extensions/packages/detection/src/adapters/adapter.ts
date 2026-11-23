/**
 * PropStein Live Sniper — Site Adapters
 * ===================================
 * The common adapter interface (spec §4.2) plus the registry with
 * feature-flag gating. Every adapter fails SAFE: unrecognized structure
 * yields zero markets and a degraded status — never invented matches.
 */

import type { ObservedMarket, BookSource } from "@propstein/core";

export interface AdapterDetectionResult {
  markets: ObservedMarket[];
  /** Healthy adapters report "OK". */
  status: "OK" | "ADAPTER_DEGRADED" | "PAGE_UNSUPPORTED";
  /** 0..1 confidence in the IDENTITY of parsed markets. */
  confidence: number;
  notes: string[];
}

export interface VisibleMarketAdapter {
  platform: BookSource;
  /** Version of the selector set — bumped when layouts change. */
  adapterVersion: string;
  isSupportedPage(url: URL): boolean;
  detectVisibleMarkets(root: Document): Promise<AdapterDetectionResult>;
  /**
   * Subscribe to market changes. Returns an unsubscribe function.
   * Implementations typically delegate to a shared DomMarketExtractor with
   * adapter-specific selector scoping.
   */
  observeChanges(callback: (event: import("@propstein/core").MarketChangeEvent) => void): () => void;
}

export const MIN_PREGAME_PARSE_CONFIDENCE = 0.90;

export interface AdapterFeatureFlags {
  /** Real sportsbook adapters ship DISABLED pending authorization. */
  draftkings: boolean;
  fanduel: boolean;
  prizepicks: boolean;
  polymarket: boolean;
  /** The synthetic demo adapter is the default-enabled test surface. */
  demobook: boolean;
}

export const DEFAULT_FEATURE_FLAGS: AdapterFeatureFlags = {
  draftkings: false,
  fanduel: false,
  prizepicks: false,
  polymarket: false,
  demobook: true,
};

/** Exact capability-flag shape returned by api/extension_router.py `/api/v1/extension/health`. */
export interface RouterCapabilities {
  mode: "OBSERVED_RESEARCH" | "HALTED";
  alertingEnabled: false;
  liveDriveFeedEnabled: false;
  betPlacementEnabled: false;
  minParseConfidence: number;
  adapters: Partial<Record<BookSource, boolean>>;
  markets: string[];
}

export interface RouterHealthGate {
  ok: boolean;
  killSwitch: boolean;
  capabilities?: RouterCapabilities;
}

export interface ConfidenceGatedResult {
  matched: ObservedMarket[];
  unmatched: Array<{ market: ObservedMarket; status: "UNMATCHED"; reason: string }>;
}

/**
 * Enforce the >= 0.90 parse confidence gate. Any observation below threshold
 * is routed to `unmatched` with status UNMATCHED — never silently promoted.
 */
export function enforceParseConfidence(
  markets: ObservedMarket[],
  minConfidence: number = MIN_PREGAME_PARSE_CONFIDENCE,
): ConfidenceGatedResult {
  const matched: ObservedMarket[] = [];
  const unmatched: Array<{ market: ObservedMarket; status: "UNMATCHED"; reason: string }> = [];
  for (const market of markets) {
    if (!Number.isFinite(market.detectionConfidence) || market.detectionConfidence < minConfidence) {
      unmatched.push({
        market,
        status: "UNMATCHED",
        reason: `parse confidence ${Number(market.detectionConfidence).toFixed(2)} < ${minConfidence.toFixed(2)} threshold`,
      });
    } else {
      matched.push(market);
    }
  }
  return { matched, unmatched };
}

export class AdapterRegistry {
  private readonly adapters = new Map<string, VisibleMarketAdapter>();
  private readonly flags: AdapterFeatureFlags;

  constructor(flags: Partial<AdapterFeatureFlags> = {}) {
    this.flags = { ...DEFAULT_FEATURE_FLAGS, ...flags };
  }

  register(adapter: VisibleMarketAdapter): void {
    this.adapters.set(adapter.platform, adapter);
  }

  isAdapterEnabled(platform: string): boolean {
    return (this.flags as unknown as Record<string, boolean | undefined>)[platform] === true;
  }

  /** Resolve the adapter for a URL, honoring local feature flags. */
  resolve(url: URL): { adapter?: VisibleMarketAdapter; reason: string } {
    for (const adapter of this.adapters.values()) {
      if (adapter.isSupportedPage(url)) {
        if (!this.isAdapterEnabled(adapter.platform)) {
          return {
            reason: `${adapter.platform} detection is disabled by feature flag pending authorization — the companion runs in research-only mode on this site`,
          };
        }
        return { adapter, reason: "ok" };
      }
    }
    return { reason: "no adapter supports this page" };
  }

  /**
   * Resolve an adapter gated on api/extension_router.py health + capability flags.
   * Fails CLOSED on killSwitch=true, missing capabilities, HALTED mode, or
   * missing/false per-adapter capability.
   */
  resolveWithGate(
    url: URL,
    gate: RouterHealthGate | null | undefined,
  ): { adapter?: VisibleMarketAdapter; reason: string; mode: "OBSERVED_RESEARCH" | "HALTED" } {
    if (!gate || gate.killSwitch || !gate.ok) {
      return {
        reason: "extension kill switch active or health check failed — all adapters halted",
        mode: "HALTED",
      };
    }
    if (!gate.capabilities || gate.capabilities.mode === "HALTED") {
      return {
        reason: "router capability document missing or halted — fail closed",
        mode: "HALTED",
      };
    }
    for (const adapter of this.adapters.values()) {
      if (adapter.isSupportedPage(url)) {
        const capEnabled = gate.capabilities.adapters?.[adapter.platform] === true;
        if (!capEnabled) {
          return {
            reason: `${adapter.platform} capability disabled by router gate — halted`,
            mode: gate.capabilities.mode,
          };
        }
        return { adapter, reason: "ok", mode: gate.capabilities.mode };
      }
    }
    return { reason: "no adapter supports this page", mode: gate.capabilities.mode };
  }

  list(): VisibleMarketAdapter[] {
    return [...this.adapters.values()];
  }
}

/**
 * Fail-safe wrapper for adapter parsing. Any throw degrades to an empty
 * result — the pipeline NEVER surfaces invented markets.
 */
export async function safeDetect(
  adapter: VisibleMarketAdapter,
  root: Document,
): Promise<AdapterDetectionResult> {
  try {
    const result = await adapter.detectVisibleMarkets(root);
    // Post-condition hardening: markets without a parseable identity or
    // odds are still allowed (they show as unpriced) but must carry a
    // source and observation time.
    const markets = result.markets.filter((m) => m.source === adapter.platform && Boolean(m.observedAt));
    return { ...result, markets };
  } catch (err) {
    return {
      markets: [],
      status: "ADAPTER_DEGRADED",
      confidence: 0,
      notes: [
        `adapter ${adapter.platform}@${adapter.adapterVersion} threw during detection: ${String(err)}`,
        "site layout may have changed — no markets will be reported until the adapter is updated",
      ],
    };
  }
}
