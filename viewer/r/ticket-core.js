/*
 * Portable single-leg ticket (NFL anytime TD) + local "Watch this price" stub.
 * A ticket is a user-entered ask carried entirely in the URL fragment
 * (/t/#<payload>). It is not a receipt, is never verified, and is never
 * proof of a wager. Nothing is sent to a server when a ticket is made.
 */
(() => {
  const Tail = window.PropsteinTail;
  const WATCH_KEY = 'propstein.watch.v1';
  const PLAYER_ID_RE = /^\d{2}-\d{7}$/;
  const GAME_ID_RE = /^\d{4}_\d{2}_[A-Z]{2,3}_[A-Z]{2,3}$/;

  function b64urlEncode(str) {
    const bytes = new TextEncoder().encode(str);
    let bin = '';
    bytes.forEach((b) => { bin += String.fromCharCode(b); });
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function b64urlDecode(s) {
    if (!/^[A-Za-z0-9_-]{8,1200}$/.test(s)) return null;
    try {
      const bin = atob(s.replace(/-/g, '+').replace(/_/g, '/'));
      const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
      return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch {
      return null;
    }
  }

  /** Strict schema. Anything else is "ticket not found". */
  function validateTicket(t) {
    if (!t || typeof t !== 'object' || Array.isArray(t)) return null;
    const keys = Object.keys(t).sort().join(',');
    const allowed = ['at', 'g', 'm', 'n', 'o', 'p', 'take', 'v'];
    if (Object.keys(t).some((k) => !allowed.includes(k))) return null;
    if (t.v !== 1 || t.m !== 'anytime_td') return null;
    if (typeof t.p !== 'string' || !PLAYER_ID_RE.test(t.p)) return null;
    if (typeof t.g !== 'string' || !GAME_ID_RE.test(t.g)) return null;
    if (typeof t.n !== 'string' || !t.n.trim() || t.n.length > 60) return null;
    if (!Number.isInteger(t.o) || Math.abs(t.o) < 100 || Math.abs(t.o) > 100000) return null;
    if (typeof t.at !== 'string' || !Number.isFinite(Date.parse(t.at))) return null;
    if (t.take !== undefined && (typeof t.take !== 'string' || t.take.length > 100)) return null;
    void keys;
    return { v: 1, m: 'anytime_td', p: t.p, g: t.g, n: t.n.trim(), o: t.o, at: new Date(Date.parse(t.at)).toISOString(), take: (t.take || '').trim() };
  }

  function encodeTicket(ticket) {
    const clean = validateTicket(ticket);
    if (!clean) return null;
    const payload = { v: 1, m: clean.m, p: clean.p, g: clean.g, n: clean.n, o: clean.o, at: clean.at };
    if (clean.take) payload.take = clean.take;
    return b64urlEncode(JSON.stringify(payload));
  }

  function decodeTicket(fragment) {
    const raw = b64urlDecode(String(fragment || '').replace(/^#/, ''));
    if (raw === null) return null;
    try {
      return validateTicket(JSON.parse(raw));
    } catch {
      return null;
    }
  }

  function ticketKey(t) {
    return `${t.m}|${t.p}|${t.g}|${t.o}|${t.at}`;
  }

  // ---------------------------------------------------- watch (local only)
  function readWatches() {
    try {
      const v = JSON.parse(window.localStorage.getItem(WATCH_KEY) || '[]');
      return Array.isArray(v) ? v.filter((w) => w && typeof w.key === 'string') : [];
    } catch {
      return [];
    }
  }

  function writeWatches(list) {
    try {
      window.localStorage.setItem(WATCH_KEY, JSON.stringify(list.slice(-50)));
      return true;
    } catch {
      return false;
    }
  }

  function isWatching(t) {
    return readWatches().some((w) => w.key === ticketKey(t));
  }

  function toggleWatch(t) {
    const key = ticketKey(t);
    const list = readWatches();
    if (list.some((w) => w.key === key)) {
      return { watching: false, saved: writeWatches(list.filter((w) => w.key !== key)) };
    }
    list.push({ key, market: t.m, player: t.n, playerId: t.p, gameId: t.g, cutoff: t.o, savedAt: new Date(Date.now()).toISOString() });
    return { watching: true, saved: writeWatches(list) };
  }

  /** Describe the cutoff against current per-book quotes (no alerts). */
  function watchLine(t, bookSummary) {
    const hits = Object.entries(bookSummary || {}).filter(([, q]) => q && Tail.toDecimal(q.odds) >= Tail.toDecimal(t.o));
    if (!hits.length) return `No DraftKings or FanDuel quote observed at or above ${Tail.fmtOdds(t.o)} right now.`;
    return `Observed at or above ${Tail.fmtOdds(t.o)}: ${hits.map(([book, q]) => `${book} ${Tail.fmtOdds(q.odds)}`).join(', ')}.`;
  }

  /** Render the ticket comparison (DK + FD rows, $20 diff, fair if gated in). */
  async function renderTicketComparison(container, t, { title, scopeNote, postedLabel, postedTag, postedTagClass, postedAtText }) {
    const [oddsFiles, maxAgeSeconds, slateRow] = await Promise.all([
      Tail.nflOddsFiles(),
      Tail.oddsStaleSeconds('nfl'),
      Tail.findSlateRow(t.p, t.g),
    ]);
    const source = await Tail.loadCurrentQuotes('nfl', oddsFiles);
    const fairOdds = Tail.tdFairFromRow(slateRow);
    const matchup = slateRow ? ` · ${slateRow.team || ''} vs ${slateRow.opponent_team || ''}` : '';
    return Tail.renderComparison(container, {
      title,
      scopeNote,
      subject: `${t.n}${matchup}`,
      selection: { gameId: t.g, playerId: t.p, market: 'anytime_td', side: 'YES', threshold: null },
      posted: { label: postedLabel, tagText: postedTag, tagClass: postedTagClass, odds: t.o, at: t.at, atText: postedAtText },
      fair: fairOdds !== null ? { odds: fairOdds, label: 'Model fair now' } : null,
      fairNote: slateRow ? 'The current slate has no gated model fair for this selection.' : 'This player and game are not on a current slate.',
      source,
      nowMs: Date.now(),
      maxAgeSeconds,
      perBook: Tail.TICKET_BOOKS,
      dollarDiff: true,
    });
  }

  window.PropsteinTicket = {
    encodeTicket,
    decodeTicket,
    validateTicket,
    ticketKey,
    isWatching,
    toggleWatch,
    watchLine,
    renderTicketComparison,
  };
})();
