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

  function slipFromQuery(snapshot) {
    const raw = new URLSearchParams(window.location.search).get('slip');
    if (!raw) return { slip: null, notice: '' };
    const parsed = Tail.parseSlip(raw);
    if (!parsed.ok) return { slip: null, notice: `Slip ignored: ${parsed.reason}` };
    const sameMarket = parsed.market === snapshot.market;
    const norm = (v) => String(v || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
    if (!sameMarket || norm(parsed.player) !== norm(snapshot.player_name)) {
      return { slip: null, notice: 'Slip ignored: it does not match this receipt’s player and market.' };
    }
    return { slip: parsed, notice: '' };
  }

  async function renderTail(found) {
    if (!tailEl || !Tail) return;
    if (!found) {
      tailEl.innerHTML = '<p class="t-note" data-tail-status="unavailable">Tail check unavailable: the committed snapshot file could not be loaded.</p>';
      return;
    }
    const { slip, notice } = slipFromQuery(found.row);
    await Tail.renderTailCheck(tailEl, { snapshot: found.row, slate: found.slate, slip });
    if (notice) tailEl.insertAdjacentHTML('afterbegin', `<p class="t-notice" data-slip-notice="1">${esc(notice)}</p>`);
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
