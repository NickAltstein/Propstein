/**
 * PropStein Live Sniper — SignalCard component (OBSERVED · RESEARCH).
 * Scan-first side-panel card: Player · Market · FAIR vs BOOK · Δ · short hash
 * · Copy / Share to X. Never renders Kelly stake or bet-placement actions.
 */
import type { SignalCardViewModel } from "../view-models.ts";

export interface SignalCardProps {
  vm: SignalCardViewModel;
  onViewAnalysis?: (key: string) => void;
  onCopyReceipt?: (copyText: string) => void;
}

export function SignalCard({ vm, onViewAnalysis, onCopyReceipt }: SignalCardProps) {
  return (
    <article className={`signal-card signal-card--${vm.tone}${vm.withdrawn ? " signal-card--withdrawn" : ""}`}>
      <div className="signal-card__head">
        <span className="signal-card__league">{vm.leagueLine}</span>
        <span className={`signal-card__state signal-card__state--${vm.tone}`}>{vm.statusLabel}</span>
      </div>
      <h3 className="signal-card__title">{vm.playerName} · {vm.marketName}</h3>
      <div className="signal-card__who">
        <div>{vm.playerLine}</div>
        <div>{vm.selectionLine}</div>
      </div>
      <dl className="signal-card__grid">
        <div className="row">
          <dt>FAIR</dt>
          <dd className="num">{vm.fairDisplay}</dd>
        </div>
        <div className="row">
          <dt>BOOK</dt>
          <dd className="num">{vm.bookDisplay}</dd>
        </div>
        <div className="row">
          <dt>Δ</dt>
          <dd className={`num num--${vm.evTone}`}>{vm.deltaDisplay}</dd>
        </div>
        {!vm.isResearchGated && (
          <div className="row">
            <dt>ESTIMATED EV</dt>
            <dd className={`num num--${vm.evTone}`}>{vm.estimatedEv}</dd>
          </div>
        )}
      </dl>
      <div className="signal-card__foot">
        <span>#{vm.shortHash}</span>
        <span>SOURCE: {vm.source}</span>
        <span>MARKET STATUS: {vm.marketStatus}</span>
        <span>QUOTE AGE: {vm.quoteAge}</span>
      </div>
      {vm.reasons.length > 0 && (
        <ul className="signal-card__reasons">
          {vm.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <div className="signal-card__actions">
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => {
            if (onCopyReceipt) onCopyReceipt(vm.copyText);
            else if (typeof navigator !== "undefined" && navigator.clipboard) {
              void navigator.clipboard.writeText(vm.copyText);
            }
          }}
        >
          COPY
        </button>
        <a
          className="btn btn--ghost"
          href={vm.shareToXUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          SHARE TO X
        </a>
        <button type="button" className="btn btn--ghost" onClick={() => onViewAnalysis?.(vm.key)}>
          VIEW ANALYSIS
        </button>
      </div>
    </article>
  );
}
