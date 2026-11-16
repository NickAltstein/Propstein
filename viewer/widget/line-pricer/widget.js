(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const model = window.PropSteinMath.createQuantMath({ alpha: 0.1097, k: 1.555 });
  const eventEndpoint = document.querySelector('meta[name="propstein-events-endpoint"]')?.content;
  if (eventEndpoint?.startsWith('/')) {
    try { navigator.sendBeacon?.(eventEndpoint, new Blob([JSON.stringify({ event: 'widget_load', tier: 'research' })], { type: 'application/json' })); } catch (_) {}
  }
  let rows = [];
  let touched = false;
  const out = $('result');
  const pct = (n) => `${(n * 100).toFixed(1)}%`;
  function render() {
    const row = rows.find((r) => r.player_id === $('player').value);
    const market = $('market').value;
    if (!row) return;
    const fair = Number(market === 'receptions' ? row.fair_line_rec : row.fair_line_yards);
    if (!touched) $('line').value = Number.isFinite(fair) ? fair.toFixed(1) : '';
    const line = Number($('line').value);
    const rec = Number(row.mu_rec);
    const ypr = Number(row.mu_ypr);
    const breakEven = Number($('break-even').value) / 100;
    if ($('line').value.trim() === '' || $('break-even').value.trim() === '' ||
        !Number.isFinite(line) || line < 0 || !Number.isFinite(rec) ||
        (market === 'receiving_yards' && !Number.isFinite(ypr)) ||
        !Number.isFinite(breakEven) || breakEven < 0 || breakEven > 1) {
      out.textContent = 'Published model inputs or entered values are unavailable.';
      return;
    }
    const over = market === 'receptions' ? model.pRecOver(rec, line) : model.pYardsOver(rec, ypr, line);
    out.innerHTML = `<dl><div><dt>Over</dt><dd><strong>${pct(over)}</strong></dd></div><div><dt>Under</dt><dd><strong>${pct(1 - over)}</strong></dd></div><div><dt>Model fair line</dt><dd>${Number.isFinite(fair) ? fair.toFixed(1) : '—'}</dd></div><div><dt>Entered break-even</dt><dd>${pct(breakEven)}</dd></div></dl><p>Over is ${over >= breakEven ? 'above' : 'below'} this break-even. No live odds or recommendation is supplied.</p>`;
  }
  $('player').addEventListener('change', () => { touched = false; render(); });
  $('market').addEventListener('change', () => { touched = false; render(); });
  $('line').addEventListener('input', () => { touched = true; render(); });
  $('break-even').addEventListener('input', render);
  (async () => {
    try {
      const indexResponse = await fetch('/feeds/slate-index.json', { cache: 'no-store' });
      if (!indexResponse.ok) throw new Error('No slate index');
      const index = await indexResponse.json();
      const nfl = index.sports.nfl;
      const key = nfl.default_slate;
      const path = nfl.slates[key].slate;
      if (!/^slate_[a-zA-Z0-9_-]+\.json$/.test(path)) throw new Error('Invalid slate path');
      const slateResponse = await fetch('/' + path, { cache: 'no-cache' });
      if (!slateResponse.ok) throw new Error('No slate');
      rows = (await slateResponse.json()).filter((r) => r && Number.isFinite(Number(r.mu_rec)));
      $('player').replaceChildren(...rows.map((r) => {
        const option = document.createElement('option');
        option.value = r.player_id;
        option.textContent = r.player_display_name;
        return option;
      }));
      render();
    } catch (_) {
      out.textContent = 'Published model slate unavailable. Open the terminal for the public research record.';
    }
  })();
})();
