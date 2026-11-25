/* One price state per player row, shared by every surface that shows a price.
 *
 * The hero cards, the Player props list and the Line Pricer used to each decide
 * on their own whether a price existed, so one page could say "No live price",
 * "No verified Polymarket price yet" and show a 3h-old quote for the same player.
 * They now all read derivePriceState(), which takes the page-level presentation
 * from market-status.js, so the whole page agrees.
 *
 * MAIN-LINE RULE (selectMainQuote)
 * A venue such as Polymarket lists a ladder of lines for one player and market
 * (e.g. receiving yards 29.5 and 49.5). Only one of them is the market's centre.
 * Showing an alt line beside the model fair line reads as an edge that is not
 * there (fair 96.0 next to "Under 49.5" looks like a 46-yard disagreement).
 *
 *   1. Take every quote for the player and market.
 *   2. For each quote with both an over and an under price, remove the vig:
 *      p = P(over) normalised so P(over) + P(under) = 1.
 *   3. The main line is the quote with p closest to 0.50, and only if
 *      |p - 0.50| <= MAIN_LINE_MAX_SKEW (0.10, i.e. a 40%-60% no-vig price).
 *      Ties go to the quote listed first in the feed.
 *   4. A quote without both an over and an under price cannot be checked, so it
 *      is never the main line, even when it is the only line listed.
 *   5. Every other quote is an alt line. Alt lines are never shown beside the
 *      fair line as "Book"; they are labelled "Alt line <line>". When there is
 *      no main line, the alt nearest the market centre is the one displayed.
 *
 * The rule uses only market prices, never the model's fair line, so it cannot
 * pick whichever line makes the model look best.
 */
((root) => {
  'use strict';

  const MAIN_LINE_MAX_SKEW = 0.10;

  const finite = (value) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const implied = (odds) => {
    if (!finite(odds) || Number(odds) === 0) return null;
    const o = Number(odds);
    return o > 0 ? 100 / (o + 100) : -o / (-o + 100);
  };

  /** No-vig P(over) for a two-sided quote, or null when one side is missing. */
  function noVigOver(quote) {
    if (!quote) return null;
    const cents = (v) => (finite(v) && Number(v) > 0 && Number(v) < 100 ? Number(v) / 100 : null);
    let over = cents(quote.over_cents ?? quote.yes_cents);
    let under = cents(quote.under_cents ?? quote.no_cents);
    if (over === null || under === null) {
      over = implied(quote.over_odds);
      under = implied(quote.under_odds);
    }
    if (over === null || under === null || over + under <= 0) return null;
    return over / (over + under);
  }

  /** Split one player's quotes for one market into the main line and alt lines. */
  function selectMainQuote(quotes) {
    const list = (Array.isArray(quotes) ? quotes : []).filter((q) => q && typeof q === 'object' && finite(q.line));
    if (!list.length) return { main: null, alts: [], rule: 'none' };
    let best = null;
    let bestSkew = Infinity;
    for (const quote of list) {
      const p = noVigOver(quote);
      if (p === null) continue;
      const skew = Math.abs(p - 0.5);
      if (skew <= MAIN_LINE_MAX_SKEW + 1e-9 && skew < bestSkew) { best = quote; bestSkew = skew; }
    }
    // Alt lines nearest the market centre first (unpriced last); feed order breaks ties.
    const skewOf = (q) => { const p = noVigOver(q); return p === null ? Infinity : Math.abs(p - 0.5); };
    const byCentre = (items) => items.map((q, i) => [q, skewOf(q), i])
      .sort((a, b) => a[1] - b[1] || a[2] - b[2]).map(([q]) => q);
    if (best) return { main: best, alts: byCentre(list.filter((q) => q !== best)), rule: 'no_vig_center' };
    return { main: null, alts: byCentre(list), rule: 'no_main_line' };
  }

  /* Only prediction-market venues are named in price copy. A sportsbook's name is
   * never rendered: any other venue shows no source name at all. */
  function venueName(book) {
    const slug = String(book || '').toLowerCase();
    if (slug.includes('polymarket')) return 'Polymarket';
    if (slug.includes('kalshi')) return 'Kalshi';
    return null;
  }

  const ageText = (quotedAt, now) => {
    if (typeof quotedAt !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(quotedAt)) return null;
    const ms = Date.parse(quotedAt);
    const at = now instanceof Date ? now.getTime() : Number(now);
    if (!Number.isFinite(ms) || !Number.isFinite(at) || ms > at) return null;
    const sec = (at - ms) / 1000;
    if (sec < 60) return 'just now';
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    return `${Math.floor(sec / 3600)}h ago`;
  };

  /* Page presentation (market-status.js) → what any quote on the page may claim.
   * There is deliberately no "verified" label: the public feed is a single
   * publisher whose best status is OBSERVED with a receipt, and the feed contract
   * rejects verified counts. No surface may present a quote as verified or live. */
  const LABELS = Object.freeze({
    none: 'No quote',
    unavailable: 'Prices unavailable',
    started: 'Event started',
    stale: 'Stale quote',
    undated: 'Quote time unknown',
    unverified: 'Unverified quote',
  });

  /**
   * @param {object[]} quotes  every quote for this player and market
   * @param {object} ctx
   *   presentation  page-level state from deriveMarketStatus()
   *   offerFor(q)   → { freshness, verification } from deriveMarketStatus().offers
   *   now           Date or ms
   *   source        the board's venue when no quote names one (only Polymarket or
 *                 Kalshi is ever returned as `source`; see venueName)
   */
  function derivePriceState(quotes, ctx = {}) {
    const { main, alts, rule } = selectMainQuote(quotes);
    const presentation = ctx.presentation || 'none';
    const shown = main || alts[0] || null;
    const base = { main, alts, rule, quote: shown, isMain: Boolean(main), live: false };
    if (presentation === 'unavailable' || presentation === 'checking') {
      return { ...base, quote: null, kind: 'unavailable', label: LABELS.unavailable, age: null, source: null };
    }
    if (!shown) {
      return { ...base, kind: 'none', label: LABELS.none, age: null, source: venueName(ctx.source) };
    }
    const offer = typeof ctx.offerFor === 'function' ? (ctx.offerFor(shown) || {}) : {};
    const age = ageText(shown.quoted_at, ctx.now ?? Date.now());
    const source = shown.book ? venueName(shown.book) : venueName(ctx.source);
    let kind;
    if (presentation === 'started') kind = 'started';
    else if (age === null) kind = 'undated';
    else if (presentation === 'snapshot' || offer.freshness === 'stale') kind = 'stale';
    else kind = 'unverified';
    return { ...base, kind, label: LABELS[kind], age, source, live: false };
  }

  const api = Object.freeze({ MAIN_LINE_MAX_SKEW, LABELS, noVigOver, selectMainQuote, derivePriceState, venueName });
  root.PropSteinPriceState = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
