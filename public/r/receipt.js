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

  const SNAPSHOT_FILE_RE = /^(?:[a-z0-9_]+_)?snapshots_[A-Za-z0-9_-]+\.json$/;
  const FALLBACK_SNAPSHOT_FILES = ['snapshots_2026_w7.json'];

  async function committedSnapshotFiles() {
    const files = new Set();
    try {
      const res = await fetch('/feeds/slate-index.json', { cache: 'no-store' });
      if (res.ok) {
        const manifest = await res.json();
        const sports = manifest && manifest.sports && typeof manifest.sports === 'object' ? manifest.sports : {};
        Object.values(sports).forEach((sport) => {
          const slates = sport && sport.slates && typeof sport.slates === 'object' ? sport.slates : {};
          Object.values(slates).forEach((cfg) => {
            const f = cfg && typeof cfg.snapshots === 'string' ? cfg.snapshots : '';
            if (SNAPSHOT_FILE_RE.test(f)) files.add(f);
          });
        });
      }
    } catch {
      // Manifest unavailable: fall back to the known committed file list.
    }
    FALLBACK_SNAPSHOT_FILES.forEach((f) => files.add(f));
    return [...files];
  }

  async function verify() {
    const segs = window.location.pathname.split('/').filter(Boolean);
    const hash = (segs[segs.length - 1] || '').toLowerCase();
    if (!/^[0-9a-f]{6,64}$/.test(hash)) {
      renderNotFound(hash);
      return;
    }

    // 1. Try FastAPI receipt endpoint first
    try {
      const res = await fetch(`/api/v1/receipts/${encodeURIComponent(hash)}`, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (data && data.verified) {
          renderVerified(data);
          return;
        }
      }
    } catch {
      // Static-only hosting fallback below
    }

    // 2. Static fallback against every committed snapshot file listed in the
    //    slate index. Exactly one distinct committed hash must match; zero or
    //    an ambiguous prefix fails closed to NOT_FOUND.
    try {
      const files = await committedSnapshotFiles();
      const hits = new Map();
      for (const file of files) {
        let rows = null;
        try {
          const res = await fetch(`/${file}`, { cache: 'no-store' });
          if (!res.ok) continue;
          rows = await res.json();
        } catch {
          continue;
        }
        if (!Array.isArray(rows)) continue;
        for (const r of rows) {
          if (!r || typeof r !== 'object') continue;
          const ch = String(r.content_hash || '').toLowerCase();
          const sid = String(r.snapshot_id || '').replace(/-/g, '').toLowerCase();
          if ((ch && ch.startsWith(hash)) || (sid && sid.startsWith(hash))) {
            const id = ch || sid;
            if (!hits.has(id)) hits.set(id, { row: r, file });
          }
        }
      }
      if (hits.size === 1) {
        const { row: hit, file } = hits.values().next().value;
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
          source: file,
        });
        return;
      }
    } catch {
      // Fall through to fail-closed NOT_FOUND
    }

    renderNotFound(hash);
  }

  void verify();
})();
