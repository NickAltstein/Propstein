/**
 * PropStein Live Sniper — 1200x675 Receipt Card & X Share Helpers
 * ===============================================================
 * Generates a deterministic short receipt hash, clipboard text, X intent URL,
 * and a 1200x675 SVG receipt card for OBSERVED · RESEARCH signals.
 * Never includes Kelly stake or wager recommendations.
 */

export const RECEIPT_CARD_WIDTH = 1200;
export const RECEIPT_CARD_HEIGHT = 675;

export interface ReceiptCardInput {
  signalKey: string;
  playerName: string;
  marketName: string;
  leagueLine: string;
  fairDisplay: string;
  bookDisplay: string;
  deltaDisplay: string;
  statusLabel: string;
  source: string;
  evaluatedAt: string;
}

/** Deterministic 8-char hex short hash (FNV-1a 32-bit, browser + Node safe). */
export function shortReceiptHash(parts: string[]): string {
  const input = parts.join("|");
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

function escapeXml(raw: string): string {
  return raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export function buildReceiptVerifyUrl(hash: string): string {
  return `https://propstein.com/r/${encodeURIComponent(hash)}`;
}

export function buildObservedCopyText(card: ReceiptCardInput, hash: string): string {
  return [
    `${card.playerName} · ${card.marketName}`,
    `FAIR ${card.fairDisplay} vs BOOK ${card.bookDisplay} (Δ ${card.deltaDisplay})`,
    `${card.statusLabel} · #${hash}`,
    `Verify: ${buildReceiptVerifyUrl(hash)}`,
  ].join(" | ");
}

export function buildXShareIntentUrl(card: ReceiptCardInput, hash: string): string {
  const text = buildObservedCopyText(card, hash);
  return `https://x.com/intent/post?text=${encodeURIComponent(text)}`;
}

/**
 * Render the 1200x675 PropStein receipt card SVG (reusable for PNG export or
 * X/social card preview). Strictly intelligence-only: zero Kelly or stakes.
 */
export function renderReceiptCardSvg(card: ReceiptCardInput, hash: string): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${RECEIPT_CARD_WIDTH}" height="${RECEIPT_CARD_HEIGHT}" viewBox="0 0 ${RECEIPT_CARD_WIDTH} ${RECEIPT_CARD_HEIGHT}" role="img" aria-label="${escapeXml(card.playerName)} ${escapeXml(card.marketName)} receipt">
  <defs>
    <linearGradient id="psBg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#121316"/>
      <stop offset="100%" stop-color="#0c0d0f"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="675" rx="28" fill="url(#psBg)"/>
  <rect x="44" y="44" width="1112" height="587" rx="20" fill="#17181b" stroke="#2c3036" stroke-width="2"/>
  <text x="92" y="114" fill="#9da3ab" font-family="IBM Plex Mono, monospace" font-size="22" font-weight="600">${escapeXml(card.leagueLine)}</text>
  <rect x="820" y="76" width="288" height="46" rx="23" fill="#232933" stroke="#8fb5ea" stroke-width="1.5"/>
  <text x="964" y="106" text-anchor="middle" fill="#bfdbff" font-family="IBM Plex Mono, monospace" font-size="18" font-weight="700">${escapeXml(card.statusLabel)}</text>
  <text x="92" y="210" fill="#f8fafc" font-family="Space Grotesk, sans-serif" font-size="56" font-weight="700">${escapeXml(card.playerName)}</text>
  <text x="92" y="264" fill="#9da3ab" font-family="Inter, sans-serif" font-size="30" font-weight="500">${escapeXml(card.marketName)}</text>
  <rect x="92" y="316" width="1016" height="172" rx="16" fill="#0f1114" stroke="#272b30" stroke-width="1.5"/>
  <text x="136" y="366" fill="#9da3ab" font-family="IBM Plex Mono, monospace" font-size="20">MODEL FAIR</text>
  <text x="136" y="444" fill="#ffffff" font-family="Space Grotesk, monospace" font-size="64" font-weight="700">${escapeXml(card.fairDisplay)}</text>
  <text x="520" y="366" fill="#9da3ab" font-family="IBM Plex Mono, monospace" font-size="20">BOOK</text>
  <text x="520" y="444" fill="#8fb5ea" font-family="Space Grotesk, monospace" font-size="64" font-weight="700">${escapeXml(card.bookDisplay)}</text>
  <text x="890" y="366" fill="#9da3ab" font-family="IBM Plex Mono, monospace" font-size="20">DELTA</text>
  <text x="890" y="444" fill="#34d399" font-family="Space Grotesk, monospace" font-size="56" font-weight="700">${escapeXml(card.deltaDisplay)}</text>
  <text x="92" y="576" fill="#6b7077" font-family="IBM Plex Mono, monospace" font-size="20">RECEIPT #${escapeXml(hash)} · ${escapeXml(card.source)}</text>
  <text x="1108" y="576" text-anchor="end" fill="#9da3ab" font-family="IBM Plex Mono, monospace" font-size="20">propstein.com/r/${escapeXml(hash)} · RESEARCH ONLY</text>
</svg>`;
}
