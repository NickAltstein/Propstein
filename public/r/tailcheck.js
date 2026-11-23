/*
 * "Would you still tail?" — read-only comparison of a committed snapshot
 * against the current PropStein feed. Shared by /r/<hash> and /check.
 *
 * Rules:
 *  - Only committed snapshot fields and observed feed quotes are shown.
 *  - A current quote counts only if it is for the same player, market and
 *    game as the snapshot. Otherwise: "no current quote". Nothing is filled in.
 *  - Status compares line vs model fair only. No edge, no stake sizing,
 *    no recommendation, and nothing here claims a bet was placed.
 *  - Quotes are labeled by provenance: observed (snapshot / feed) vs
 *    user-reported (a pasted slip, never verified).
 */
(() => {
  const SNAPSHOT_FILE_RE = /^(?:[a-z0-9_]+_)?snapshots_[A-Za-z0-9_-]+\.json$/;
  const ODDS_FILE_RE = /^[A-Za-z0-9_.-]+\.json$/;
  const FALLBACK_SLATES = [{ key: '2026_w7', snapshots: 'snapshots_2026_w7.json', odds: null }];

  const STATUS = {
    STILL_ABOVE_FAIR: 'still above fair',
    PRICE_GONE: 'price gone',
    NO_CURRENT_QUOTE: 'no current quote',
    FAIR_UNAVAILABLE: 'fair unavailable',
    NO_SIDE: 'no side logged',
  };

  const MARKET_LABEL = { receiving_yards: 'Receiving Yards', receptions: 'Receptions' };

  function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function num(v) {
    if (v === null || v === undefined || v === '') return NaN;
    const n = Number(v);
    return Number.isFinite(n) ? n : NaN;
  }

  function fmtOdds(v) {
    const n = num(v);
    if (!Number.isFinite(n)) return '—';
    return n > 0 ? `+${Math.round(n)}` : `${Math.round(n)}`;
  }

  function fmtLine(v) {
    const n = num(v);
    return Number.isFinite(n) ? n.toFixed(1) : '—';
  }

  function fmtTs(v) {
    if (!v) return 'time not recorded';
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return 'time not recorded';
    return `${d.toISOString().replace('T', ' ').slice(0, 16)} UTC`;
  }

  async function fetchJson(url) {
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }

  /** Slates from the committed slate index: { key, snapshots, odds }. */
  async function loadSlates() {
    const manifest = await fetchJson('/feeds/slate-index.json');
    const out = [];
    const seen = new Set();
    const sports = manifest && manifest.sports && typeof manifest.sports === 'object' ? manifest.sports : {};
    Object.values(sports).forEach((sport) => {
      const slates = sport && sport.slates && typeof sport.slates === 'object' ? sport.slates : {};
      Object.entries(slates).forEach(([key, cfg]) => {
        const snapshots = cfg && typeof cfg.snapshots === 'string' && SNAPSHOT_FILE_RE.test(cfg.snapshots) ? cfg.snapshots : null;
        if (!snapshots || seen.has(snapshots)) return;
        seen.add(snapshots);
        const odds = cfg && typeof cfg.odds === 'string' && ODDS_FILE_RE.test(cfg.odds) ? cfg.odds : null;
        out.push({ key, snapshots, odds });
      });
    });
    FALLBACK_SLATES.forEach((s) => { if (!seen.has(s.snapshots)) out.push(s); });
    return out;
  }

  async function loadSnapshotRows(slate) {
    const rows = await fetchJson(`/${slate.snapshots}`);
    return Array.isArray(rows) ? rows.filter((r) => r && typeof r === 'object') : [];
  }

  /** Exactly one distinct committed snapshot must match the hash, else null. */
  async function findSnapshotByHash(hash) {
    const needle = String(hash || '').toLowerCase();
    if (!/^[0-9a-f]{6,64}$/.test(needle)) return null;
    const slates = await loadSlates();
    const hits = new Map();
    for (const slate of slates) {
      for (const r of await loadSnapshotRows(slate)) {
        const ch = String(r.content_hash || '').toLowerCase();
        const sid = String(r.snapshot_id || '').replace(/-/g, '').toLowerCase();
        if ((ch && ch.startsWith(needle)) || (sid && sid.startsWith(needle))) {
          const id = ch || sid;
          if (!hits.has(id)) hits.set(id, { row: r, slate });
        }
      }
    }
    return hits.size === 1 ? hits.values().next().value : null;
  }

  /** Observed quotes for the snapshot's own slate feed, same player/market/game only. */
  async function currentQuotesFor(snapshot, slate) {
    if (!slate || !slate.odds || !snapshot) return [];
    const feed = await fetchJson(`/${slate.odds}`);
    const quotes = Array.isArray(feed) ? feed : (feed && Array.isArray(feed.quotes) ? feed.quotes : []);
    return quotes.filter((q) => q && typeof q === 'object'
      && String(q.player_id || '') === String(snapshot.player_id || '')
      && String(q.market || '') === String(snapshot.market || '')
      && Boolean(snapshot.game_id) && String(q.game_id || '') === String(snapshot.game_id)
      && Number.isFinite(num(q.line))
      && Boolean(q.observed_at));
  }

  /** Best line for the side: Over = lowest line, Under = highest; ties go to newer. */
  function bestQuote(quotes, side) {
    if (!quotes.length) return null;
    const sorted = quotes.slice().sort((a, b) => {
      const la = num(a.line);
      const lb = num(b.line);
      if (side === 'OVER' && la !== lb) return la - lb;
      if (side === 'UNDER' && la !== lb) return lb - la;
      return String(b.observed_at).localeCompare(String(a.observed_at));
    });
    return sorted[0];
  }

  function sideOdds(quote, side) {
    if (!quote) return NaN;
    if (side === 'OVER') return num(quote.over_odds);
    if (side === 'UNDER') return num(quote.under_odds);
    return num(quote.odds);
  }

  /** Price logged inside the committed snapshot (observed), or null. */
  function loggedSnapshotPrice(snapshot) {
    const q = snapshot && snapshot.quote;
    if (!q || typeof q !== 'object' || !Number.isFinite(num(q.line))) return null;
    const side = String((q.evaluation && q.evaluation.best_side) || '').toUpperCase();
    const s = side === 'OVER' || side === 'UNDER' ? side : null;
    return {
      provenance: 'observed',
      book: q.book || q.source || 'Unknown book',
      side: s,
      line: num(q.line),
      odds: s ? sideOdds(q, s) : NaN,
      overOdds: num(q.over_odds),
      underOdds: num(q.under_odds),
      at: q.observed_at || null,
    };
  }

  /**
   * One-line status. Compares the current line against model fair for the
   * logged side. It does not price the odds and is not a recommendation.
   */
  function tailStatus({ fair, side, current }) {
    if (!Number.isFinite(fair)) {
      return { code: STATUS.FAIR_UNAVAILABLE, detail: 'This snapshot has no model fair line to compare against.' };
    }
    if (!current) {
      return { code: STATUS.NO_CURRENT_QUOTE, detail: 'PropStein feeds have no current quote for this player, market and game.' };
    }
    const line = num(current.line);
    if (!side) {
      return { code: STATUS.NO_SIDE, detail: `Current line ${fmtLine(line)} vs model fair ${fmtLine(fair)}. No Over/Under side was logged, so there is nothing to compare.` };
    }
    const better = side === 'OVER' ? line < fair : line > fair;
    if (better) {
      return { code: STATUS.STILL_ABOVE_FAIR, detail: `${side === 'OVER' ? 'Over' : 'Under'} ${fmtLine(line)} is still on the fair side of model fair ${fmtLine(fair)}.` };
    }
    return { code: STATUS.PRICE_GONE, detail: `${side === 'OVER' ? 'Over' : 'Under'} ${fmtLine(line)} is no longer on the fair side of model fair ${fmtLine(fair)}.` };
  }

  function priceRow(label, tag, tagClass, body) {
    return `<div class="t-row"><div class="t-row-head"><span class="t-label">${esc(label)}</span><span class="t-tag ${tagClass}">${esc(tag)}</span></div><div class="t-row-body">${body}</div></div>`;
  }

  function priceText(p) {
    const side = p.side ? `${p.side === 'OVER' ? 'Over' : 'Under'} ` : '';
    const odds = p.side ? fmtOdds(p.odds) : `O ${fmtOdds(p.overOdds)} / U ${fmtOdds(p.underOdds)}`;
    return `<strong>${esc(side)}${esc(fmtLine(p.line))}</strong> <span class="t-odds">${esc(odds)}</span> · ${esc(p.book)}<br><span class="t-ts">${esc(fmtTs(p.at))}</span>`;
  }

  /**
   * Render the comparison block.
   * snapshot: committed row. slate: its slate entry. slip: optional parsed
   * user-reported slip { side, line, odds, raw }.
   */
  async function renderTailCheck(container, { snapshot, slate, slip = null }) {
    const fair = num(snapshot && snapshot.projection && snapshot.projection.fair_line);
    const logged = loggedSnapshotPrice(snapshot);
    const side = (slip && slip.side) || (logged && logged.side) || null;
    const quotes = await currentQuotesFor(snapshot, slate);
    const current = bestQuote(quotes, side);
    const status = tailStatus({ fair, side, current });
    const market = MARKET_LABEL[snapshot.market] || snapshot.market || 'Prop';

    const rows = [];
    rows.push(`<p class="t-subject">${esc(snapshot.player_name || 'Player')} · ${esc(market)}${snapshot.team ? ` · ${esc(snapshot.team)}${snapshot.opponent ? ` vs ${esc(snapshot.opponent)}` : ''}` : ''}</p>`);

    if (slip) {
      rows.push(priceRow('Your slip', 'USER-REPORTED · NOT VERIFIED', 'is-user', priceText({
        side: slip.side, line: slip.line, odds: slip.odds, book: 'Your book', at: null,
      }).replace('time not recorded', 'pasted by you')));
    }
    rows.push(priceRow('Logged price', 'OBSERVED · COMMITTED SNAPSHOT', 'is-observed', logged
      ? priceText(logged)
      : `<span class="t-none">No price was logged in this snapshot.</span><br><span class="t-ts">Snapshot committed ${esc(fmtTs(snapshot.created_at))}</span>`));

    if (current) {
      const cs = side;
      rows.push(priceRow('Current best quote', 'OBSERVED · PROPSTEIN FEED', 'is-observed', priceText({
        side: cs, line: num(current.line), odds: sideOdds(current, cs), overOdds: num(current.over_odds), underOdds: num(current.under_odds),
        book: current.book || 'Unknown book', at: current.observed_at,
      })));
    } else {
      rows.push(priceRow('Current best quote', 'PROPSTEIN FEED', 'is-none', '<span class="t-none">No current quote in PropStein feeds for this game.</span>'));
    }

    rows.push(priceRow('Model fair', 'COMMITTED SNAPSHOT', 'is-observed', Number.isFinite(fair)
      ? `<strong>${esc(fmtLine(fair))}</strong> <span class="t-ts">${esc(snapshot.model && snapshot.model.version ? snapshot.model.version : '')}</span>`
      : '<span class="t-none">Fair unavailable.</span>'));

    rows.push(`<div class="t-status" data-tail-status="${esc(status.code)}"><strong>${esc(status.code)}</strong><span>${esc(status.detail)}</span></div>`);
    rows.push('<p class="t-note">Line vs model fair only. Odds are shown, not priced. Research only, not a recommendation. PropStein does not place or track bets.</p>');

    container.innerHTML = `<section class="t-card" data-tail-check="1" aria-label="Would you still tail?"><h2 class="t-title">Would you still tail?</h2>${rows.join('')}</section>`;
    return { status: status.code, current, logged, fair };
  }

  // ---------------------------------------------------------------------
  // Slip parsing: "Player Market [over|under|o|u] Line Odds"
  // ---------------------------------------------------------------------
  const MARKET_ALIASES = [
    { market: 'receiving_yards', words: ['receiving yards', 'receiving yds', 'rec yards', 'rec yds', 'rec yd', 'rec yard'] },
    { market: 'receptions', words: ['receptions', 'recs', 'rec', 'catches'] },
  ];

  function normName(s) {
    return String(s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9 ]+/g, '').replace(/\s+/g, ' ').trim();
  }

  /** Returns { ok:true, player, market, side, line, odds, raw } or { ok:false, reason }. */
  function parseSlip(raw) {
    const text = String(raw || '').replace(/[−–—]/g, '-').replace(/\s+/g, ' ').trim();
    if (!text) return { ok: false, reason: 'Paste a slip like “Mike Evans receiving yards over 46.5 -110”.' };
    if (text.length > 120) return { ok: false, reason: 'Slip text is too long. Paste one leg.' };
    const tokens = text.split(' ');
    const oddsTok = tokens.pop();
    if (!/^[+-]\d{3,5}$/.test(oddsTok || '') || Math.abs(Number(oddsTok)) < 100) {
      return { ok: false, reason: 'Could not read American odds at the end (e.g. -110 or +125).' };
    }
    const odds = Number(oddsTok);
    let lineTok = tokens.pop() || '';
    let side = null;
    const m = /^([ou])?(\d{1,3}(?:\.\d)?)$/i.exec(lineTok);
    if (!m) return { ok: false, reason: 'Could not read the line before the odds (e.g. 46.5 or o46.5).' };
    if (m[1]) side = m[1].toLowerCase() === 'o' ? 'OVER' : 'UNDER';
    const line = Number(m[2]);
    const sideTok = (tokens[tokens.length - 1] || '').toLowerCase();
    if (!side && (sideTok === 'over' || sideTok === 'under' || sideTok === 'o' || sideTok === 'u')) {
      side = sideTok.startsWith('o') ? 'OVER' : 'UNDER';
      tokens.pop();
    }
    const rest = tokens.join(' ').toLowerCase();
    let market = null;
    let playerPart = '';
    for (const alias of MARKET_ALIASES) {
      for (const w of alias.words) {
        if (rest === w || rest.endsWith(` ${w}`)) {
          if (!market || w.length > market.w.length) market = { market: alias.market, w };
        }
      }
    }
    if (!market) return { ok: false, reason: 'Market not recognized. Supported: receiving yards, receptions.' };
    playerPart = tokens.join(' ').slice(0, rest.length - market.w.length).trim();
    if (!normName(playerPart)) return { ok: false, reason: 'Could not read a player name.' };
    return { ok: true, player: playerPart, market: market.market, side, line, odds, raw: text };
  }

  /** Latest committed snapshot for player+market. Ambiguous names fail closed. */
  async function findSnapshotForSlip(slip) {
    const want = normName(slip.player);
    const slates = await loadSlates();
    const matches = [];
    for (const slate of slates) {
      for (const r of await loadSnapshotRows(slate)) {
        if (r.market !== slip.market || normName(r.player_name) !== want) continue;
        if (!/^[0-9a-f]{64}$/.test(String(r.content_hash || '').toLowerCase())) continue;
        matches.push({ row: r, slate });
      }
    }
    const ids = new Set(matches.map((m) => m.row.player_id));
    if (ids.size !== 1) return { ok: false, reason: ids.size ? 'That name matches more than one player. Add more of the name.' : 'No committed PropStein snapshot for that player and market.' };
    matches.sort((a, b) => String(b.row.created_at || '').localeCompare(String(a.row.created_at || '')));
    return { ok: true, ...matches[0] };
  }

  function encodeSlip(slip) {
    return encodeURIComponent(slip.raw);
  }

  window.PropsteinTail = {
    STATUS,
    esc,
    findSnapshotByHash,
    findSnapshotForSlip,
    parseSlip,
    encodeSlip,
    renderTailCheck,
    tailStatus,
  };
})();
