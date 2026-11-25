import assert from 'node:assert/strict';
import test from 'node:test';
import { buildSandbox, loadApp } from './helpers/sandbox.mjs';

test('stale homepage shows fair-line research when observed lines are unavailable', async () => {
  // NFL: the sport with a published model. NBA has no model and never gets these cards (below).
  const { sandbox, registry } = buildSandbox({ seedLocalStorage: { propwire_active_sport: 'nfl' } });
  const app = loadApp(sandbox);
  await app.loadSlate(app.defaultBootSlate());
  app.state.livePicks = { picks: [] };
  app.renderHomeEdgePreview();

  const title = registry.get('#homeEdgeTitle').textContent;
  const status = registry.get('#homeEdgeStatus').textContent;
  const cards = registry.get('#homeEdgeList').innerHTML;
  assert.equal(title, 'Model fair lines');
  assert.match(status, /No qualifying prices|Snapshot only/);
  assert.equal((cards.match(/class="home-edge-card home-research-card"/g) || []).length, 8);
  assert.match(cards, /MODEL FAIR · NO QUOTE/);
  assert.doesNotMatch(cards, /No live price/);
  assert.match(cards, /no current edge or stake is available/);
  assert.doesNotMatch(cards, /Top 5 plays|verified edge/i);
  assert.equal(registry.get('#homeEdgeFallback').hidden, true);
});

test('NFL research preview ranks and limits the daily model signals to eight', async () => {
  const { sandbox, registry } = buildSandbox();
  const app = loadApp(sandbox);
  await app.loadSlate(app.defaultBootSlate());
  app.state.livePicks = {
    picks: Array.from({ length: 10 }, (_, index) => ({
      player: `Player ${index + 1}`,
      market: 'receiving_yards',
      side: 'over',
      line: 40.5 + index,
      odds: -110,
      book: 'draftkings',
      edge: (index + 1) / 100,
      ev_per_unit: (index + 1) / 10,
    })),
  };
  app.renderHomeEdgePreview();

  const cards = registry.get('#homeEdgeList').innerHTML;
  assert.equal(registry.get('#homeEdgeTitle').textContent, 'Top 8 model signals');
  assert.equal((cards.match(/class="home-edge-card home-research-card"/g) || []).length, 8);
  assert.ok(cards.indexOf('Player 10') < cards.indexOf('Player 9'));
  assert.doesNotMatch(cards, /Player 2<|Player 1</);
  assert.match(registry.get('#homeEdgeStatus').textContent, /No qualifying prices|Snapshot only/);
  assert.equal(registry.get('#homeEdgeFallback').hidden, true);
});

test('NBA has no model: no "MODEL FAIR" in the hero or the list, on archived and live nights', async () => {
  const { sandbox, registry } = buildSandbox({ seedLocalStorage: { propwire_active_sport: 'nba' } });
  const app = loadApp(sandbox);
  assert.equal(app.adapter.model, null);
  // The three archived nights whose rows carry synthetic fixture fair lines, plus a live
  // market-only night shaped like viewer/feeds/slate-index.json's current default.
  app.SLATES['2026-11-24'] = { label: 'Nov 24', date: '2026-11-24', slate: 'nba_slate_2026-11-24.json', games: 'nba_games_2026-11-24.json',
    odds: 'nba_odds_2026-11-24.json', picks: null, injuries: null, snapshots: null, historical: false, marketOnly: true, state: 'live' };
  const nights = ['2026-10-22', '2026-10-23', '2026-10-29', '2026-11-24'];
  for (const key of nights) assert.ok(app.SLATES[key], `${key} is registered`);
  for (const key of nights) {
    for (const historical of [false, true]) {
      const saved = { historical: app.SLATES[key].historical, marketOnly: app.SLATES[key].marketOnly };
      app.SLATES[key].historical = historical;
      if (key !== '2026-11-24') app.SLATES[key].marketOnly = false; // as in the slate index for 10-22/23/29
      await app.loadSlate(key);
      app.state.livePicks = { picks: [] };
      if (key !== '2026-11-24') assert.ok(app.state.slate.some((row) => Number(app.fairValue(row)) > 0), `${key} rows really carry fair lines`);
      for (const view of ['cards', 'table']) {
        app.setView(view);
        app.renderAll();
        const where = `${key} historical=${historical} ${view}`;
        const hero = registry.get('#homeEdgeList').innerHTML;
        assert.doesNotMatch(hero, /MODEL FAIR/, `hero: ${where}`);
        assert.doesNotMatch(hero, /data-rf-card/, `hero cards: ${where}`);
        assert.match(hero, /NBA shows market prices only\. There are no model fair lines for NBA yet\./);
        assert.notEqual(registry.get('#homeEdgeTitle').textContent, 'Model fair lines');
        for (const id of ['#cardView', '#slateRows', '#emptyState']) {
          assert.doesNotMatch(registry.get(id).innerHTML, /MODEL FAIR/, `list ${id}: ${where}`);
        }
        assert.equal(registry.get('#cardView').innerHTML, '', `no list cards: ${where}`);
      }
      Object.assign(app.SLATES[key], saved);
    }
  }
});
