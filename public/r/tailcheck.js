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
  const MARKET_LABEL = { receiving_yards: 'Receiving Yards', receptions: 'Receptions', anytime_td: 'Anytime TD', threes: '3PM' };
  // NBA 3PM ladder rungs map to the existing 'threes' Over line: N+ == Over (N - 0.5). Exact line only.
  const THREES_RUNGS = { 3: 2.5, 4: 3.5 };

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

  const VALID_HEALTH_LEVELS = new Set(['ok', 'live', 'healthy', 'degraded']);
  const FORBIDDEN_PROVENANCE_RE = /fixture|synthetic|test|seed|replay|demo/i;

  function isFixtureOrTestQuote(q, feedMeta) {
    if (!q || typeof q !== 'object') return true;
    if (q._fixture === true || q.fixture === true || q.is_fixture === true) return true;
    if (q.synthetic === true || q.seeded === true || q.replayed === true || q.is_test === true) return true;
    if (q.provenance && FORBIDDEN_PROVENANCE_RE.test(String(q.provenance))) return true;
    if (q.source_env && FORBIDDEN_PROVENANCE_RE.test(String(q.source_env))) return true;
    if (feedMeta && typeof feedMeta === 'object') {
      if (feedMeta._fixture === true || feedMeta.is_fixture === true || feedMeta.synthetic === true) return true;
      if (feedMeta.feed_status && FORBIDDEN_PROVENANCE_RE.test(String(feedMeta.feed_status))) return true;
    }
    return false;
  }

  /**
   * Load observed quotes for the given feed files.
   * Fail-closed health gate (P0-1): missing, timed-out, malformed, unknown-status,
   * or stale feed_status.json suppresses quotes instead of failing open.
   * Returns { status: 'ok' | 'no_source' | 'source_failed' | 'fail_closed', quotes, checkedFiles }.
   */
  async function loadCurrentQuotes(sport, oddsFiles, { nowMs = null, maxHealthAgeSeconds = null } = {}) {
    const files = [...new Set((oddsFiles || []).filter((f) => f && DATA_FILE_RE.test(f)))];
    if (!files.length) return { status: 'no_source', quotes: [], checkedFiles: [] };
    let feedStatus;
    try {
      feedStatus = await fetchJson('/feeds/feed_status.json');
    } catch {
      return { status: 'fail_closed', quotes: [], checkedFiles: files };
    }
    if (!feedStatus || typeof feedStatus !== 'object' || !feedStatus.sports || typeof feedStatus.sports !== 'object') {
      return { status: 'fail_closed', quotes: [], checkedFiles: files };
    }
    const generatedAt = parseTs(feedStatus.generated_at);
    if (!Number.isFinite(generatedAt)) {
      return { status: 'fail_closed', quotes: [], checkedFiles: files };
    }
    if (Number.isFinite(nowMs) && Number.isFinite(maxHealthAgeSeconds) && maxHealthAgeSeconds > 0) {
      const healthAgeSec = (nowMs - generatedAt) / 1000;
      if (healthAgeSec > maxHealthAgeSeconds || healthAgeSec < -5) {
        return { status: 'fail_closed', quotes: [], checkedFiles: files };
      }
    }
    const sportStatus = feedStatus.sports[sport];
    if (!sportStatus || typeof sportStatus !== 'object') {
      return { status: 'fail_closed', quotes: [], checkedFiles: files };
    }
    if (sportStatus.fail_closed !== false) {
      return { status: 'fail_closed', quotes: [], checkedFiles: files };
    }
    const level = String(sportStatus.level || '').trim().toLowerCase();
    if (!VALID_HEALTH_LEVELS.has(level)) {
      return { status: 'fail_closed', quotes: [], checkedFiles: files };
    }
    const quotes = [];
    for (const f of files) {
      let feed;
      try {
        feed = await fetchJson(`/${f}`);
      } catch {
        return { status: 'source_failed', quotes: [], checkedFiles: files };
      }
      const feedMeta = (!Array.isArray(feed) && feed && typeof feed === 'object') ? feed : null;
      const list = Array.isArray(feed) ? feed : (feed && Array.isArray(feed.quotes) ? feed.quotes : null);
      if (!list) return { status: 'source_failed', quotes: [], checkedFiles: files };
      list.forEach((q) => {
        if (q && typeof q === 'object' && !isFixtureOrTestQuote(q, feedMeta)) quotes.push(q);
      });
    }
    return { status: 'ok', quotes, checkedFiles: files };
  }

  /**
   * Exact-selection filter. selection = { gameId, playerId, market, side, threshold }.
   * side: 'OVER' | 'UNDER' for line markets, 'YES' for anytime_td (threshold null).
   * Excludes fixture/seeded/replayed/test quotes (P0-3).
   * Returns { eligible: [{ book, odds, decimal, observedAt, receivedAt, line }], rejected: { stale, mismatch, fixture } }.
   */
  function eligibleQuotes(quotes, selection, { nowMs, maxAgeSeconds, books = null }) {
    const eligible = [];
    const rejected = { stale: 0, mismatch: 0, fixture: 0 };
    for (const q of quotes || []) {
      if (!q || typeof q !== 'object') continue;
      if (isFixtureOrTestQuote(q, null)) { rejected.fixture += 1; continue; }
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
      eligible.push({ book: String(q.book || 'Unknown book'), odds, decimal, observedAt: q.observed_at, receivedAt: q.received_at || null, line });
    }
    return { eligible, rejected };
  }

  /** Newest eligible observation for a single book (P0-2): never picks an older better price over a newer worse price. */
  function newestOf(eligible) {
    if (!eligible.length) return null;
    return eligible.slice().sort((a, b) => {
      const obsDiff = parseTs(b.observedAt) - parseTs(a.observedAt);
      if (obsDiff !== 0) return obsDiff;
      const recA = parseTs(a.receivedAt);
      const recB = parseTs(b.receivedAt);
      if (Number.isFinite(recA) && Number.isFinite(recB) && recB !== recA) return recB - recA;
      return a.decimal - b.decimal; // deterministic conservative tie-break
    })[0];
  }

  /** Best across distinct books (each book first reduced to its newest eligible observation). */
  function bestOf(eligible) {
    if (!eligible.length) return null;
    const byBook = new Map();
    for (const q of eligible) {
      const cur = byBook.get(q.book);
      byBook.set(q.book, cur ? newestOf([cur, q]) : q);
    }
    return [...byBook.values()].sort((a, b) => (b.decimal - a.decimal) || (parseTs(b.observedAt) - parseTs(a.observedAt)))[0];
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
    if (sel.market === 'threes' && sel.side === 'OVER') {
      const rung = Object.keys(THREES_RUNGS).find((k) => THREES_RUNGS[k] === sel.threshold);
      if (rung) return `3PM ${rung}+ (Over ${fmtLine(sel.threshold)})`;
    }
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
        const latest = newestOf(result.eligible.filter((q) => q.book === book));
        summary.books[book] = latest;
        parts.push(row(`${book} now`, tag('OBSERVED', 'is-observed'), latest
          ? quoteBody(latest)
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
    if (!m) {
      const nba = parseThreesSlip(text, read);
      if (nba) return nba;
      return { ok: false, reason: `Only NFL anytime TD and NBA 3PM (3+ or 4+) are supported. Examples: “${SLIP_EXAMPLE}”, “${SLIP_EXAMPLE_3PM}”.`, read };
    }
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
    return { ok: true, sport: 'nfl', market: 'anytime_td', player: playerPart, team, odds: o.odds, read };
  }

  // NBA 3PM: "Jalen Brunson 3PM 3+ +150" or "... 3PM over 2.5 +150". Only 3+ / 4+.
  const SLIP_EXAMPLE_3PM = 'Jalen Brunson 3PM 3+ +150';
  const THREES_RE = /^(.*?)\s+(?:3pm|3pt|3pts|threes|3-pointers made|3 pointers made|three pointers made|three-pointers made|3-pointers|three pointers)\s+(.*)$/i;

  function parseThreesSlip(text, read) {
    const m = THREES_RE.exec(text);
    if (!m) return null;
    read.market = 'NBA 3PM';
    let playerPart = m[1].trim();
    let team = null;
    const pt = playerPart.split(' ');
    if (pt.length > 1 && /^[A-Z]{2,4}$/.test(pt[pt.length - 1])) {
      team = pt.pop();
      playerPart = pt.join(' ');
    }
    if (team) read.team = team;
    if (!normName(playerPart)) return { ok: false, reason: 'Could not read a player name.', read };
    read.player = playerPart;
    const tokens = m[2].trim().split(' ');
    const oddsTok = tokens.length > 1 ? tokens.pop() : null;
    const rungText = tokens.join(' ').toLowerCase();
    let rung = null;
    let rm = /^(\d+)\+$/.exec(rungText);
    if (rm) rung = Number(rm[1]);
    rm = /^(?:over|o)\s?(\d+\.5)$/.exec(rungText);
    if (rm) rung = Number(rm[1]) + 0.5;
    if (/^(?:under|u)\s?\d/.test(rungText)) return { ok: false, reason: 'Only 3+ and 4+ (Over) are supported for NBA 3PM.', read };
    if (rung === null) {
      if (!oddsTok && /^[+-]?\d{3,5}$|^even$/i.test(m[2].trim())) return { ok: false, reason: 'Missing the rung. Add 3+ or 4+ before the odds.', read };
      return { ok: false, reason: 'Could not read the rung. Use 3+ or 4+ (or Over 2.5 / Over 3.5).', read };
    }
    read.rung = `${rung}+`;
    if (!THREES_RUNGS[rung]) return { ok: false, reason: `Only 3+ and 4+ are supported for NBA 3PM (read ${rung}+).`, read };
    if (!oddsTok) return { ok: false, reason: 'Missing odds. Add signed American odds such as +150 or -120.', read };
    const o = parseOddsToken(oddsTok);
    if (!o.ok) return { ok: false, reason: 'Odds need an explicit sign, such as +150 or -120 (or “even”).', read };
    read.odds = fmtOdds(o.odds);
    return { ok: true, sport: 'nba', market: 'nba_threes', rung, player: playerPart, team, odds: o.odds, read };
  }

  /**
   * Resolve a parsed slip against the current default NFL slate.
   * Exactly one player row must match (name, and team when given).
   */
  async function resolveSlipPlayer(parsed) {
    if (parsed.sport === 'nba') return resolveNbaPlayer(parsed);
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

  /**
   * NBA players for the current NBA slate: slate rows plus players observed in
   * that slate's own odds feed (the market-only board uses the same source).
   * Exactly one player+game must match.
   */
  async function nbaPlayersForSlate(slate) {
    const out = [];
    const rows = slate.slate ? await fetchJsonOrNull(`/${slate.slate}`) : null;
    (Array.isArray(rows) ? rows : []).forEach((r) => {
      if (r && r.player_id && r.game_id) out.push({ playerId: String(r.player_id), gameId: String(r.game_id), name: r.player_display_name || r.player_name || '', team: r.team || '', opponent: r.opponent_team || r.opponent || '' });
    });
    const feed = slate.odds ? await fetchJsonOrNull(`/${slate.odds}`) : null;
    const quotes = Array.isArray(feed) ? feed : (feed && Array.isArray(feed.quotes) ? feed.quotes : []);
    quotes.forEach((q) => {
      if (q && q.player_id && q.game_id && q._fixture !== true) out.push({ playerId: String(q.player_id), gameId: String(q.game_id), name: q.player_name || '', team: q.team || '', opponent: q.opponent || '' });
    });
    return out;
  }

  async function resolveNbaPlayer(parsed) {
    const { slates, defaults } = await loadSlateIndex();
    const key = defaults.nba;
    const slate = slates.find((s) => s.sport === 'nba' && s.key === key && !s.historical);
    if (!slate) return { ok: false, reason: 'No current NBA slate is published.' };
    const want = normName(parsed.player);
    let matches = (await nbaPlayersForSlate(slate)).filter((p) => normName(p.name) === want);
    if (parsed.team) matches = matches.filter((p) => String(p.team).toUpperCase() === parsed.team);
    const ids = new Map();
    matches.forEach((p) => { if (!ids.has(`${p.playerId}|${p.gameId}`)) ids.set(`${p.playerId}|${p.gameId}`, p); });
    if (ids.size === 0) return { ok: false, reason: `“${parsed.player}” is not on the current NBA slate (${key}).` };
    if (ids.size > 1) {
      const options = [...ids.values()].map((p) => `${p.name} (${p.team} vs ${p.opponent})`);
      return { ok: false, ambiguous: true, reason: `That matches more than one player or game: ${options.join('; ')}. Add the team, e.g. “${parsed.player} ${[...ids.values()][0].team} 3PM ${parsed.rung}+ ${fmtOdds(parsed.odds)}”.` };
    }
    const p = [...ids.values()][0];
    return { ok: true, row: { player_id: p.playerId, game_id: p.gameId, player_display_name: p.name, team: p.team, opponent_team: p.opponent }, slate };
  }

  async function nbaOddsFiles() {
    const { slates } = await loadSlateIndex();
    return slates.filter((s) => s.sport === 'nba' && !s.historical && s.odds).map((s) => s.odds);
  }

  async function findNbaPlayer(playerId, gameId) {
    const { slates } = await loadSlateIndex();
    for (const s of slates.filter((x) => x.sport === 'nba' && !x.historical)) {
      const hit = (await nbaPlayersForSlate(s)).find((p) => p.playerId === playerId && p.gameId === gameId);
      if (hit) return hit;
    }
    return null;
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
    THREES_RUNGS,
    SLIP_EXAMPLE,
    SLIP_EXAMPLE_3PM,
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
    nbaOddsFiles,
    findNbaPlayer,
  };
})();
