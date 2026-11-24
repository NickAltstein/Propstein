(() => {
  const titleEl = document.getElementById('receiptTitle');
  const subEl = document.getElementById('receiptSub');
  const bodyEl = document.getElementById('receiptBody');

  function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function renderNotFound(hash) {
    titleEl.textContent = 'Receipt not found';
    subEl.textContent = 'Fail-closed verification: unknown or uncommitted receipt hash.';
    bodyEl.innerHTML = `<div class="r-fail" data-receipt-status="NOT_FOUND">Hash <strong>#${esc(hash || 'missing')}</strong> does not match any committed PropStein snapshot.</div>`;
  }

  function renderVerified(rec) {
    titleEl.textContent = `${rec.player || 'Snapshot'} · ${rec.market || 'Prop'}`;
    subEl.textContent = 'Verified against committed read-only PropStein snapshot.';
    bodyEl.innerHTML = `<dl class="r-dl" data-receipt-status="VERIFIED">
      <div class="r-row"><dt>Short Hash</dt><dd>#${esc(rec.shortHash)}</dd></div>
      <div class="r-row"><dt>Snapshot SHA-256</dt><dd>${esc(rec.snapshotHash)}</dd></div>
      <div class="r-row"><dt>UTC Timestamp</dt><dd>${esc(rec.timestampUtc || '—')}</dd></div>
      ${rec.market ? `<div class="r-row"><dt>Market</dt><dd>${esc(rec.market)}</dd></div>` : ''}
      ${Number.isFinite(Number(rec.fairLine)) && rec.fairLine !== null ? `<div class="r-row"><dt>Committed Fair Line</dt><dd>${esc(Number(rec.fairLine).toFixed(1))}</dd></div>` : ''}
      <div class="r-row"><dt>Matchup</dt><dd>${esc(rec.team || '')}${rec.opponent ? ` vs ${esc(rec.opponent)}` : ''}</dd></div>
      <div class="r-row"><dt>Source Artifact</dt><dd>${esc(rec.source || 'committed snapshot')}</dd></div>
    </dl>`;
  }

  const Tail = window.PropsteinTail;
  const tailEl = document.getElementById('tailCheck');

  /**
   * Live comparison, rendered separately from the verified receipt. Any
   * failure here leaves the receipt (and its hash and verify result) intact.
   */
  async function renderTail(found) {
    if (!tailEl || !Tail) return;
    try {
      if (!found) {
        tailEl.innerHTML = '<p class="t-note" data-tail-status="unavailable">Live comparison unavailable: the committed snapshot file could not be loaded.</p>';
        return;
      }
      const snap = found.row;
      const q = snap.quote && typeof snap.quote === 'object' ? snap.quote : null;
      const side = String((q && q.evaluation && q.evaluation.best_side) || '').toUpperCase();
      const line = Number(q && q.line);
      const fairLine = Number(snap.projection && snap.projection.fair_line);
      const fairNote = Number.isFinite(fairLine) ? `Fair when logged: ${fairLine.toFixed(1)} (a line, not a price at this threshold).` : '';
      const subject = `${snap.player_name || 'Player'} · ${snap.team || ''}${snap.opponent ? ` vs ${snap.opponent}` : ''}`;
      if (!q || !Number.isFinite(line) || (side !== 'OVER' && side !== 'UNDER')) {
        tailEl.innerHTML = `<section class="t-card" data-tail-check="1" aria-label="Would you still tail?">
          <h2 class="t-title">Would you still tail?</h2>
          <p class="t-scope">Live comparison · read-only · not part of the verified receipt</p>
          <p class="t-subject">${esc(subject)}</p>
          <div class="t-row" data-posted="1"><div class="t-row-head"><span class="t-label">Posted price</span><span class="t-tag is-observed">OBSERVED · COMMITTED SNAPSHOT</span></div><div class="t-row-body"><span class="t-none">No price was logged in this snapshot.</span></div></div>
          <div class="t-row" data-current-row="1"><div class="t-row-head"><span class="t-label">Current quote</span><span class="t-tag is-observed">OBSERVED</span></div><div class="t-row-body"><span class="t-none" data-no-current-quote="1">No current quote</span><br><span class="t-ts">No logged side and threshold to match against.</span></div></div>
          <div class="t-row"><div class="t-row-head"><span class="t-label">Model fair</span><span class="t-tag is-model">MODEL · RESEARCH ONLY</span></div><div class="t-row-body"><span class="t-none" data-fair-unavailable="1">Model fair unavailable</span>${fairNote ? `<br><span class="t-ts">${esc(fairNote)}</span>` : ''}</div></div>
        </section>`;
        return;
      }
      const odds = side === 'OVER' ? q.over_odds : q.under_odds;
      const sport = found.slate.sport || 'nfl';
      const [source, maxAgeSeconds] = await Promise.all([
        Tail.loadCurrentQuotes(sport, found.slate.odds ? [found.slate.odds] : []),
        Tail.oddsStaleSeconds(sport),
      ]);
      Tail.renderComparison(tailEl, {
        title: 'Would you still tail?',
        scopeNote: 'Live comparison · read-only · not part of the verified receipt',
        subject,
        selection: { gameId: snap.game_id, playerId: snap.player_id, market: snap.market, side, threshold: line },
        posted: { label: 'Posted price', tagText: 'OBSERVED · COMMITTED SNAPSHOT', tagClass: 'is-observed', odds, at: q.observed_at, book: q.book || q.source || '' },
        // Line markets carry a fair LINE, not a fair price at this threshold.
        fair: null,
        fairNote,
        source,
        nowMs: Date.now(),
        maxAgeSeconds,
        perBook: null,
        dollarDiff: false,
      });
    } catch {
      tailEl.innerHTML = '<p class="t-note" data-tail-status="unavailable"><span data-no-current-quote="1">No current quote</span>. Live comparison unavailable; the receipt above is unaffected.</p>';
    }
  }

  async function verify() {
    const segs = window.location.pathname.split('/').filter(Boolean);
    const hash = (segs[segs.length - 1] || '').toLowerCase();
    if (!/^[0-9a-f]{6,64}$/.test(hash)) {
      renderNotFound(hash);
      return;
    }

    // Static lookup against every committed snapshot file in the slate index.
    // Exactly one distinct committed hash must match; zero or an ambiguous
    // prefix fails closed to NOT_FOUND.
    let found = null;
    try {
      found = Tail ? await Tail.findSnapshotByHash(hash) : null;
    } catch {
      found = null;
    }

    // 1. FastAPI receipt endpoint first (when deployed)
    try {
      const res = await fetch(`/api/v1/receipts/${encodeURIComponent(hash)}`, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (data && data.verified) {
          renderVerified(data);
          const same = found && String(found.row.content_hash || '').toLowerCase() === String(data.snapshotHash || '').toLowerCase();
          await renderTail(same ? found : null);
          return;
        }
      }
    } catch {
      // Static-only hosting fallback below
    }

    // 2. Static fallback
    if (found) {
      const hit = found.row;
      const fair = Number(hit?.projection?.fair_line);
      renderVerified({
        shortHash: hash.slice(0, 12),
        snapshotHash: hit.content_hash || hit.snapshot_id,
        timestampUtc: hit.created_at,
        player: hit.player_name,
        team: hit.team,
        opponent: hit.opponent,
        market: hit.market,
        fairLine: Number.isFinite(fair) ? fair : null,
        source: found.slate.snapshots,
      });
      await renderTail(found);
      return;
    }

    renderNotFound(hash);
  }

  void verify();
})();
