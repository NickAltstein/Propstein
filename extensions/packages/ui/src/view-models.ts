/**
 * PropStein Live Sniper — Panel View Models
 * =======================================
 * Pure mapping from SignalSnapshot → display rows. All formatting and
 * display-state decisions live here so they are unit-testable without React.
 */

import {
  formatAmerican,
  formatPercent,
  formatEvPerUnit,
  formatQuoteAge,
  signalStatusLabel,
  fairDecimalOdds,
  type SignalSnapshot,
  type ModelConfidence,
} from "@propstein/core";
import {
  shortReceiptHash,
  buildObservedCopyText,
  buildXShareIntentUrl,
} from "./receipt-card.ts";

export type SignalTone = "positive" | "caution" | "negative" | "neutral" | "muted";

export interface SignalCardViewModel {
  key: string;
  leagueLine: string;
  marketTitle: string;
  playerName: string;
  marketName: string;
  playerLine: string;
  selectionLine: string;
  fairDisplay: string;
  bookDisplay: string;
  deltaDisplay: string;
  shortHash: string;
  copyText: string;
  shareToXUrl: string;
  isResearchGated: boolean;
  sportsbookPrice: string;
  breakEven: string;
  modelProbability: string;
  estimatedEv: string;
  evTone: SignalTone;
  source: string;
  marketStatus: string;
  model: string;
  quoteAge: string;
  statusLabel: string;
  tone: SignalTone;
  withdrawn: boolean;
  reasons: string[];
  confidenceLabel: string;
  fairOdds: string | null;
  probabilityEdge: string | null;
}

function toneForState(s: SignalSnapshot): SignalTone {
  if (s.withdrawn) return "muted";
  switch (s.state) {
    case "ALERTABLE": return "positive";
    case "BELOW_THRESHOLD": return (s.evaluation?.evPerUnit ?? 0) >= 0 ? "neutral" : "negative";
    case "UNVERIFIED": return "caution";
    case "STALE":
    case "SUSPENDED":
    case "MODEL_UNAVAILABLE": return "caution";
    default: return "neutral";
  }
}

function confidenceLabel(c: ModelConfidence | undefined): string {
  switch (c) {
    case "calibrated": return "CALIBRATED";
    case "insufficient_evidence": return "INSUFFICIENT EVIDENCE";
    case "uncalibrated": return "UNCALIBRATED";
    default: return "UNKNOWN";
  }
}

export function toSignalCardViewModel(s: SignalSnapshot): SignalCardViewModel {
  const m = s.observed;
  const isSynthetic = m.source === "demobook" || m.provenance === "synthetic-fixture";
  const ev = s.evaluation;
  const isObservedMode = s.state === "OBSERVED";
  const isResearchGated = isObservedMode || (!isSynthetic && s.model?.confidence !== "calibrated");

  const evValue = ev?.evPerUnit ?? 0;
  const evTone: SignalTone = !ev || isResearchGated ? "muted" : evValue > 0 ? "positive" : evValue < 0 ? "negative" : "neutral";
  const driveTag = m.driveId ? " · LIVE DRIVE" : "";
  const provenanceTag = m.provenance === "synthetic-fixture" ? " · DEMO FIXTURE" : "";

  const marketName = s.normalized?.observed.marketLabel ?? m.marketLabel;
  const sportsbookPrice = m.americanOdds !== undefined
    ? formatAmerican(m.americanOdds)
    : (m.decimalOdds !== undefined ? `${m.decimalOdds.toFixed(2)}` : "—");
  const bookDisplay = m.line !== undefined ? `${m.line} (${sportsbookPrice})` : sportsbookPrice;
  const fairDecimal = ev ? fairDecimalOdds(Math.min(0.9999, Math.max(0.0001, ev.modelProbability))) : null;
  const fairDisplay = fairDecimal !== null ? `${fairDecimal.toFixed(2)} dec` : (s.model ? formatPercent(s.model.probability, 1) : "—");
  const deltaDisplay = ev
    ? `${ev.probabilityEdge >= 0 ? "+" : ""}${(ev.probabilityEdge * 100).toFixed(1)} pp`
    : "—";

  const statusLabel = isObservedMode
    ? "OBSERVED · RESEARCH"
    : isResearchGated
      ? "OBSERVED · RESEARCH"
      : signalStatusLabel(s);
  const leagueLine = `${m.league}${driveTag}${provenanceTag}`;
  const sourceLabel = isSynthetic ? "SYNTHETIC DEMO PAGE" : `${m.source.toUpperCase()} · OBSERVED`;
  const shortHash = shortReceiptHash([s.signalKey, m.playerName, marketName, fairDisplay, bookDisplay, s.evaluatedAt]);
  const receiptInput = {
    signalKey: s.signalKey,
    playerName: m.playerName,
    marketName,
    leagueLine,
    fairDisplay,
    bookDisplay,
    deltaDisplay,
    statusLabel,
    source: sourceLabel,
    evaluatedAt: s.evaluatedAt,
  };

  return {
    key: s.signalKey,
    leagueLine,
    marketTitle: marketName.toUpperCase(),
    playerName: m.playerName,
    marketName,
    playerLine: `Player: ${m.playerName}`,
    selectionLine: `Market: ${m.selection}`,
    fairDisplay,
    bookDisplay,
    deltaDisplay,
    shortHash,
    copyText: buildObservedCopyText(receiptInput, shortHash),
    shareToXUrl: buildXShareIntentUrl(receiptInput, shortHash),
    isResearchGated,
    sportsbookPrice,
    breakEven: ev ? formatPercent(ev.impliedProbability) : "—",
    modelProbability: isResearchGated ? "—" : (ev ? formatPercent(ev.modelProbability, 1) : "—"),
    estimatedEv: isResearchGated ? "OBSERVED · RESEARCH" : (ev ? formatEvPerUnit(ev.evPerUnit) : "—"),
    evTone,
    source: sourceLabel,
    marketStatus:
      m.marketStatus === "OPEN"
        ? `OPEN (OBSERVED${provenanceTag ? ", DEMO" : ""})`
        : m.marketStatus === "SUSPENDED" ? "SUSPENDED (OBSERVED)" : m.marketStatus,
    model: s.model ? `${s.model.modelVersion} · ${confidenceLabel(s.model.confidence)}` : "NO MODEL COVERAGE",
    quoteAge: formatQuoteAge(s.quoteAgeMs),
    statusLabel,
    tone: isResearchGated ? "caution" : toneForState(s),
    withdrawn: s.withdrawn,
    reasons: isResearchGated
      ? ["OBSERVED · RESEARCH mode — intelligence only, no Kelly or wager recommendation", ...s.reasons].slice(0, 3)
      : s.reasons.slice(0, 3),
    confidenceLabel: confidenceLabel(s.model?.confidence),
    fairOdds: !isResearchGated && fairDecimal !== null ? `${fairDecimal.toFixed(2)} dec` : null,
    probabilityEdge: !isResearchGated && ev ? `${((ev.modelProbability - ev.impliedProbability) * 100).toFixed(1)} pts` : null,
  };
}
