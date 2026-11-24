/*
 * Read-only price comparison shared by /r/<hash>, /check and /t/.
 *
 * Rules (fail closed):
 *  - "Current" = observed quotes from the slate's existing odds feed, only
 *    when the feed is not fail-closed (feeds/feed_status.json) and the quote
 *    is no older than the existing odds staleness gate
 *    (circuit_breaker_config.json → odds_stale_max_age_seconds).
 *  - Exact selection only: same game, player, market, side and threshold.
 *    No nearest-line substitution, no fuzzy player matching.
 *  - Model fair is shown only when the committed slate carries it for that
 *    exact selection. It is never derived from book odds or a logged price.
 *  - Descriptive text only. No edge, no sizing, no recommendation, and
 *    nothing here says a bet was placed.
 *  - Nothing here changes a committed receipt. The live comparison is
 *    rendered separately and may fail without touching verification.
 */
(() => {
  const SNAPSHOT_FILE_RE = /^(?:[a-z0-9_]+_)?snapshots_[A-Za-z0-9_-]+\.json$/;
  const DATA_FILE_RE = /^[A-Za-z0-9_.-]+\.json$/;
  const FALLBACK_SLATES = [{ sport: 'nfl', key: '2026_w7', snapshots: 'snapshots_2026_w7.json', odds: null, slate: 'slate_2026_w7.json', historical: true }];
  const DEFAULT_ODDS_STALE_SECONDS = 21600; // same default as viewer/app.js feedThresholds()
  const TICKET_BOOKS = ['DraftKings', 'FanDuel'];
  const MARKET_LABEL = { receiving_yards: 'Receiving Yards', receptions: 'Receptions', anytime_td: 'Anytime TD' };

  // ---------------------------------------------------------------- utils
  function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function num(v) {
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return NaN;
    const n = Number(v);
    return Number.isFinite(n) ? n : NaN;
  }

  /** Existing odds normalization (viewer/js/quant-math.js). Missing → NaN (fail closed). */
  function toDecimal(odds) {
    const math = window.PropSteinMath;
    if (!math || typeof math.americanToDecimal !== 'function') return NaN;
    const dec = math.americanToDecimal(odds);
    return Number.isFinite(dec) && dec > 1 ? dec : NaN;
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

  function parseTs(v) {
    if (!v) return NaN;
    const s = String(v).trim();
    const iso = (!s.endsWith('Z') && !/[+-]\d{2}(:?\d{2})?$/.test(s)) ? `${s}Z` : s;
    const t = Date.parse(iso);
    return Number.isFinite(t) ? t : NaN;
  }

  function fmtTs(v) {
    const t = parseTs(v);
    if (!Number.isFinite(t)) return 'time not recorded';
    return `${new Date(t).toISOString().replace('T', ' ').slice(0, 16)} UTC`;
  }

  function fmtMoney(v) {
    const n = Math.round(v * 100) / 100;
    if (Math.abs(n) < 0.005) return '$0.00';
    return `${n > 0 ? '+' : '−'}$${Math.abs(n).toFixed(2)}`;
  }

  function normName(s) {
    return String(s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9 ]+/g, '').replace(/\s+/g, ' ').trim();
  }

  async function fetchJson(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async function fetchJsonOrNull(url) {
    try {
      return await fetchJson(url);
    } catch {
      return null;
    }
  }

  // ---------------------------------------------------------- slate index
  async function loadSlateIndex() {
    const manifest = await fetchJsonOrNull('/feeds/slate-index.json');
    const out = [];
    const defaults = {};
    const sports = manifest && manifest.sports && typeof manifest.sports === 'object' ? manifest.sports : {};
    Object.entries(sports).forEach(([sport, cfgSport]) => {
      if (cfgSport && typeof cfgSport.default_slate === 'string') defaults[sport] = cfgSport.default_slate;
      const slates = cfgSport && cfgSport.slates && typeof cfgSport.slates === 'object' ? cfgSport.slates : {};
      Object.entries(slates).forEach(([key, cfg]) => {
        if (!cfg || typeof cfg !== 'object') return;
        out.push({
          sport,
          key,
          snapshots: typeof cfg.snapshots === 'string' && SNAPSHOT_FILE_RE.test(cfg.snapshots) ? cfg.snapshots : null,
          odds: typeof cfg.odds === 'string' && DATA_FILE_RE.test(cfg.odds) ? cfg.odds : null,
          slate: typeof cfg.slate === 'string' && DATA_FILE_RE.test(cfg.slate) ? cfg.slate : null,
          historical: cfg.historical === true,
        });
      });
    });
    const seen = new Set(out.map((s) => s.snapshots));
    FALLBACK_SLATES.forEach((s) => { if (!seen.has(s.snapshots)) out.push(s); });
    return { slates: out, defaults };
  }

  /** Exactly one distinct committed snapshot must match the hash, else null. */
  async function findSnapshotByHash(hash) {
    const needle = String(hash || '').toLowerCase();
    if (!/^[0-9a-f]{6,64}$/.test(needle)) return null;
    const { slates } = await loadSlateIndex();
    const hits = new Map();
    const files = new Set();
    for (const slate of slates) {
      if (!slate.snapshots || files.has(slate.snapshots)) continue;
      files.add(slate.snapshots);
      const rows = await fetchJsonOrNull(`/${slate.snapshots}`);
      if (!Array.isArray(rows)) continue;
      for (const r of rows) {
        if (!r || typeof r !== 'object') continue;
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

  // ------------------------------------------------------ current quotes
  async function oddsStaleSeconds(sport) {
    const cfg = await fetchJsonOrNull('/circuit_breaker_config.json');
    const v = num(cfg && cfg.sports && cfg.sports[sport] && cfg.sports[sport].thresholds && cfg.sports[sport].thresholds.odds_stale_max_age_seconds);
    return Number.isFinite(v) && v > 0 ? v : DEFAULT_ODDS_STALE_SECONDS;
  }

  /**
   * Load observed quotes for the given feed files.
   * Returns { status: 'ok' | 'no_source' | 'source_failed' | 'fail_closed', quotes, checkedFiles }.
   */
  async function loadCurrentQuotes(sport, oddsFiles) {
    const files = [...new Set((oddsFiles || []).filter((f) => f && DATA_FILE_RE.test(f)))];
    if (!files.length) return { status: 'no_source', quotes: [], checkedFiles: [] };
    const feedStatus = await fetchJsonOrNull('/feeds/feed_status.json');
    const sportStatus = feedStatus && feedStatus.sports && feedStatus.sports[sport];
    if (sportStatus && sportStatus.fail_closed === true) return { status: 'fail_closed', quotes: [], checkedFiles: files };
    const quotes = [];
    for (const f of files) {
      let feed;
      try {
        feed = await fetchJson(`/${f}`);
      } catch {
        return { status: 'source_failed', quotes: [], checkedFiles: files };
      }
      const list = Array.isArray(feed) ? feed : (feed && Array.isArray(feed.quotes) ? feed.quotes : null);
      if (!list) return { status: 'source_failed', quotes: [], checkedFiles: files };
      list.forEach((q) => { if (q && typeof q === 'object') quotes.push(q); });
    }
    return { status: 'ok', quotes, checkedFiles: files };
  }

  /**
   * Exact-selection filter. selection = { gameId, playerId, market, side, threshold }.
   * side: 'OVER' | 'UNDER' for line markets, 'YES' for anytime_td (threshold null).
   * Returns { eligible: [{ book, odds, decimal, observedAt, line }], rejected: { stale, mismatch } }.
   */
  function eligibleQuotes(quotes, selection, { nowMs, maxAgeSeconds, books = null }) {
    const eligible = [];
    const rejected = { stale: 0, mismatch: 0 };
    for (const q of quotes || []) {
      if (!q || typeof q !== 'object') continue;
      const sameEvent = selection.gameId && String(q.game_id || '') === String(selection.gameId);
      const samePlayer = selection.playerId && String(q.player_id || '') === String(selection.playerId);
      if (!sameEvent || !samePlayer) continue;
      if (String(q.market || '') !== selection.market) { rejected.mismatch += 1; continue; }
      if (books && !books.includes(String(q.book || ''))) continue;
      let odds = NaN;
      let line = NaN;
      if (selection.market === 'anytime_td') {
        if (String(q.selection || '').toUpperCase() !== 'YES') { rejected.mismatch += 1; continue; }
        if (q.line !== undefined && q.line !== null && q.line !== '') { rejected.mismatch += 1; continue; }
        odds = num(q.odds);
      } else {
        line = num(q.line);
        if (!Number.isFinite(line) || line !== selection.threshold) { rejected.mismatch += 1; continue; }
        odds = selection.side === 'OVER' ? num(q.over_odds) : selection.side === 'UNDER' ? num(q.under_odds) : NaN;
      }
      const decimal = toDecimal(odds);
      if (!Number.isFinite(decimal)) { rejected.mismatch += 1; continue; }
      const observed = parseTs(q.observed_at);
      if (!Number.isFinite(observed)) { rejected.stale += 1; continue; }
      const ageSeconds = (nowMs - observed) / 1000;
      if (ageSeconds > maxAgeSeconds || ageSeconds < -60) { rejected.stale += 1; continue; }
      eligible.push({ book: String(q.book || 'Unknown book'), odds, decimal, observedAt: q.observed_at, line });
    }
    return { eligible, rejected };
  }

  /** Best = highest payout among the eligible quotes actually checked; ties → newer. */
  function bestOf(eligible) {
    if (!eligible.length) return null;
    return eligible.slice().sort((a, b) => (b.decimal - a.decimal) || (parseTs(b.observedAt) - parseTs(a.observedAt)))[0];
  }

  // ---------------------------------------------------------- comparisons
  function vsPosted(currentOdds, postedOdds) {
    const c = toDecimal(currentOdds);
    const p = toDecimal(postedOdds);
    if (!Number.isFinite(c) || !Number.isFinite(p)) return null;
    if (Math.abs(c - p) < 1e-9) return 'Matches posted price';
    return c > p ? 'Better than posted' : 'Worse than posted';
  }

  function vsFair(currentOdds, fairOdds) {
    const c = toDecimal(currentOdds);
    const f = toDecimal(fairOdds);
    if (!Number.isFinite(c) || !Number.isFinite(f)) return null;
    if (Math.abs(c - f) < 1e-9) return 'At fair';
    return c > f ? 'Above fair' : 'Below fair';
  }

  /** Profit difference on a $20 ticket: current price minus posted price. */
  function dollarDiff20(currentOdds, postedOdds) {
    const c = toDecimal(currentOdds);
    const p = toDecimal(postedOdds);
    if (!Number.isFinite(c) || !Number.isFinite(p)) return NaN;
    return 20 * (c - 1) - 20 * (p - 1);
  }

  // -------------------------------------------------------------- render
  function tag(text, cls) {
    return `<span class="t-tag ${cls}">${esc(text)}</span>`;
  }

  function row(label, tagHtml, body, attrs = '') {
    return `<div class="t-row" ${attrs}><div class="t-row-head"><span class="t-label">${esc(label)}</span>${tagHtml}</div><div class="t-row-body">${body}</div></div>`;
  }

  function selectionText(sel) {
    if (sel.market === 'anytime_td') return 'Anytime TD · Yes';
    const side = sel.side === 'OVER' ? 'Over' : sel.side === 'UNDER' ? 'Under' : '';
    return `${MARKET_LABEL[sel.market] || sel.market} · ${side} ${fmtLine(sel.threshold)}`;
  }

  const SOURCE_NOTE = {
    no_source: 'No enabled quote source covers this game.',
    source_failed: 'Quote source could not be loaded.',
    fail_closed: 'Quote source is fail-closed right now.',
  };

  function rejectedNote(rejected) {
    const parts = [];
    if (rejected.stale) parts.push(`${rejected.stale} stale`);
    if (rejected.mismatch) parts.push(`${rejected.mismatch} for a different selection`);
    return parts.length ? `Skipped: ${parts.join(', ')}.` : '';
  }

  /**
   * Comparison block.
   * opts: { title, scopeNote, selection, posted: { odds, label, tagText, tagClass, at, book }, fair: { odds, label } | null,
   *         fairNote, source: { status, quotes }, nowMs, maxAgeSeconds, perBook: string[] | null, dollarDiff: boolean }
   */
  function renderComparison(container, opts) {
    const sel = opts.selection;
    const posted = opts.posted;
    const parts = [];
    parts.push(`<p class="t-subject">${esc(opts.subject || '')}</p>`);
    parts.push(row(posted.label, tag(posted.tagText, posted.tagClass), `<strong>${esc(selectionText(sel))}</strong> <span class="t-odds">${esc(fmtOdds(posted.odds))}</span>${posted.book ? ` · ${esc(posted.book)}` : ''}<br><span class="t-ts">${esc(posted.atText || fmtTs(posted.at))}</span>`, 'data-posted="1"'));

    const sourceOk = opts.source.status === 'ok';
    const result = sourceOk
      ? eligibleQuotes(opts.source.quotes, sel, { nowMs: opts.nowMs, maxAgeSeconds: opts.maxAgeSeconds, books: opts.perBook })
      : { eligible: [], rejected: { stale: 0, mismatch: 0 } };
    const fairOdds = opts.fair && Number.isFinite(toDecimal(opts.fair.odds)) ? opts.fair.odds : null;
    const summary = { currentCount: result.eligible.length, books: {} };

    const quoteBody = (q) => {
      const lines = [`<strong>${esc(fmtOdds(q.odds))}</strong> · ${esc(q.book)} <span class="t-ts">observed ${esc(fmtTs(q.observedAt))}</span>`];
      const p = vsPosted(q.odds, posted.odds);
      if (p) lines.push(`<span class="t-cmp" data-vs-posted="${esc(p)}">${esc(p)}</span>`);
      if (fairOdds !== null) {
        const f = vsFair(q.odds, fairOdds);
        if (f) lines.push(`<span class="t-cmp" data-vs-fair="${esc(f)}">${esc(f)}</span>`);
      }
      if (opts.dollarDiff) {
        const d = dollarDiff20(q.odds, posted.odds);
        if (Number.isFinite(d)) lines.push(`<span class="t-cmp" data-dollar-diff="${esc(fmtMoney(d))}">${esc(fmtMoney(d))} profit on $20 vs posted</span>`);
      }
      return lines.join('<br>');
    };

    if (opts.perBook) {
      opts.perBook.forEach((book) => {
        const best = bestOf(result.eligible.filter((q) => q.book === book));
        summary.books[book] = best;
        parts.push(row(`${book} now`, tag('OBSERVED', 'is-observed'), best
          ? quoteBody(best)
          : '<span class="t-none" data-no-current-quote="1">No current quote</span>', `data-book-row="${esc(book)}"`));
      });
    } else {
      const best = bestOf(result.eligible);
      summary.best = best;
      parts.push(row(best ? 'Best observed now' : 'Current quote', tag('OBSERVED', 'is-observed'), best
        ? quoteBody(best)
        : '<span class="t-none" data-no-current-quote="1">No current quote</span>', 'data-current-row="1"'));
    }

    const notes = [];
    if (!sourceOk) notes.push(SOURCE_NOTE[opts.source.status] || 'Quote source unavailable.');
    else {
      notes.push(`Checked ${result.eligible.length} current exact-match quote${result.eligible.length === 1 ? '' : 's'}.`);
      const r = rejectedNote(result.rejected);
      if (r) notes.push(r);
    }
    notes.push('An observed quote is not a promise the price is still available at checkout.');
    parts.push(`<p class="t-note" data-source-status="${esc(opts.source.status)}">${esc(notes.join(' '))}</p>`);

    parts.push(row(opts.fair ? opts.fair.label : 'Model fair', tag('MODEL · RESEARCH ONLY', 'is-model'), fairOdds !== null
      ? `<strong>${esc(fmtOdds(fairOdds))}</strong>`
      : `<span class="t-none" data-fair-unavailable="1">Model fair unavailable</span>${opts.fairNote ? `<br><span class="t-ts">${esc(opts.fairNote)}</span>` : ''}`));

    parts.push('<p class="t-note">Descriptive comparison only. Not a recommendation. PropStein does not place or track bets.</p>');
    container.innerHTML = `<section class="t-card" data-tail-check="1" aria-label="${esc(opts.title)}"><h2 class="t-title">${esc(opts.title)}</h2>${opts.scopeNote ? `<p class="t-scope">${esc(opts.scopeNote)}</p>` : ''}${parts.join('')}</section>`;
    return summary;
  }

  // ------------------------------------------------------ slip parsing
  // One supported market: NFL anytime TD. Example: "Ja'Marr Chase anytime TD +150".
  const SLIP_EXAMPLE = "Ja'Marr Chase anytime TD +150";
  const TD_RE = /^(.*?)\s+(?:anytime\s+(?:td|touchdown)|atd|anytime\s+td\s+scorer|to\s+score\s+(?:a\s+)?(?:td|touchdown))(?:\s+(yes))?(?:\s+(.*))?$/i;

  function parseOddsToken(tok) {
    const t = String(tok || '').trim().replace(/[−–—]/g, '-');
    if (/^even$/i.test(t)) return { ok: true, odds: 100 };
    const m = /^([+-])(\d{3,5})$/.exec(t);
    if (!m) return { ok: false };
    const v = Number(`${m[1]}${m[2]}`);
    if (Math.abs(v) < 100) return { ok: false };
    return { ok: true, odds: v };
  }

  /** { ok, player, team, odds, read } or { ok:false, reason, read }. Never guesses. */
  function parseSlip(raw) {
    const text = String(raw || '').replace(/\s+/g, ' ').trim();
    const read = {};
    if (!text) return { ok: false, reason: `Paste one leg, for example “${SLIP_EXAMPLE}”.`, read };
    if (text.length > 120) return { ok: false, reason: 'Paste one leg only. Multi-leg slips are not supported.', read };
    if (/[,;|]|\bparlay\b|\bsgp\b|\n/i.test(text)) return { ok: false, reason: 'Multi-leg slips are not supported. Paste one leg.', read };
    const m = TD_RE.exec(text);
    if (!m) return { ok: false, reason: `Only NFL anytime TD is supported here. Example: “${SLIP_EXAMPLE}”.`, read };
    let playerPart = m[1].trim();
    read.market = 'Anytime TD';
    let team = null;
    const pt = playerPart.split(' ');
    if (pt.length > 1 && /^[A-Z]{2,3}$/.test(pt[pt.length - 1])) {
      team = pt.pop();
      playerPart = pt.join(' ');
    }
    if (team) read.team = team;
    if (!normName(playerPart)) return { ok: false, reason: 'Could not read a player name.', read };
    read.player = playerPart;
    const rest = String(m[3] || '').trim();
    if (!rest) return { ok: false, reason: 'Missing odds. Add signed American odds such as +150 or -120.', read };
    const restTokens = rest.split(' ');
    if (restTokens.length !== 1) return { ok: false, reason: 'Could not read the odds. Use one signed price such as +150.', read };
    const o = parseOddsToken(restTokens[0]);
    if (!o.ok) return { ok: false, reason: 'Odds need an explicit sign, such as +150 or -120 (or “even”).', read };
    read.odds = fmtOdds(o.odds);
    return { ok: true, player: playerPart, team, odds: o.odds, read };
  }

  /**
   * Resolve a parsed slip against the current default NFL slate.
   * Exactly one player row must match (name, and team when given).
   */
  async function resolveSlipPlayer(parsed) {
    const { slates, defaults } = await loadSlateIndex();
    const key = defaults.nfl;
    const slate = slates.find((s) => s.sport === 'nfl' && s.key === key && !s.historical);
    if (!slate || !slate.slate) return { ok: false, reason: 'No current NFL slate is published.' };
    const rows = await fetchJsonOrNull(`/${slate.slate}`);
    if (!Array.isArray(rows)) return { ok: false, reason: 'The current NFL slate could not be loaded.' };
    const want = normName(parsed.player);
    let matches = rows.filter((r) => r && normName(r.player_display_name || r.player_name) === want && r.player_id && r.game_id);
    if (parsed.team) matches = matches.filter((r) => String(r.team || '').toUpperCase() === parsed.team);
    const ids = new Map();
    matches.forEach((r) => ids.set(`${r.player_id}|${r.game_id}`, r));
    if (ids.size === 0) return { ok: false, reason: `“${parsed.player}” is not on the current NFL slate (${key}).` };
    if (ids.size > 1) {
      const options = [...ids.values()].map((r) => `${r.player_display_name || r.player_name} (${r.team} vs ${r.opponent_team})`);
      return { ok: false, ambiguous: true, reason: `That matches more than one player or game: ${options.join('; ')}. Add the team, e.g. “${parsed.player} ${[...ids.values()][0].team} anytime TD ${fmtOdds(parsed.odds)}”.` };
    }
    const r = [...ids.values()][0];
    return { ok: true, row: r, slate };
  }

  /** Model fair for an exact anytime TD selection from a committed slate row, if present. */
  function tdFairFromRow(r) {
    const f = num(r && r.fair_odds_anytime);
    return Number.isFinite(f) && Math.abs(f) >= 100 ? f : null;
  }

  async function nflOddsFiles() {
    const { slates } = await loadSlateIndex();
    return slates.filter((s) => s.sport === 'nfl' && !s.historical && s.odds).map((s) => s.odds);
  }

  async function findSlateRow(playerId, gameId) {
    const { slates } = await loadSlateIndex();
    for (const s of slates.filter((x) => x.sport === 'nfl' && x.slate && !x.historical)) {
      const rows = await fetchJsonOrNull(`/${s.slate}`);
      if (!Array.isArray(rows)) continue;
      const r = rows.find((x) => x && x.player_id === playerId && x.game_id === gameId);
      if (r) return r;
    }
    return null;
  }

  window.PropsteinTail = {
    TICKET_BOOKS,
    SLIP_EXAMPLE,
    esc,
    fmtOdds,
    fmtTs,
    toDecimal,
    normName,
    loadSlateIndex,
    findSnapshotByHash,
    oddsStaleSeconds,
    loadCurrentQuotes,
    eligibleQuotes,
    vsPosted,
    vsFair,
    dollarDiff20,
    renderComparison,
    parseSlip,
    parseOddsToken,
    resolveSlipPlayer,
    tdFairFromRow,
    nflOddsFiles,
    findSlateRow,
  };
})();
