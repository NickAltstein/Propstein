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

test('NBA has no model: no fair-line hero cards and no "MODEL FAIR" on any night', async () => {
  const { sandbox, registry } = buildSandbox({ seedLocalStorage: { propwire_active_sport: 'nba' } });
  const app = loadApp(sandbox);
  assert.equal(app.adapter.model, null);
  const nights = Object.keys(app.SLATES);
  assert.ok(nights.length >= 1, 'NBA nights are registered');
  // Include an archived night shaped like 2026-10-22/23/29 in the slate index: marketOnly false,
  // historical, rows carrying (synthetic fixture) fair_line_pts.
  const archived = nights.find((key) => !app.SLATES[key].marketOnly) || nights[0];
  for (const key of nights) {
    for (const historical of [false, true]) {
      const saved = { historical: app.SLATES[key].historical, marketOnly: app.SLATES[key].marketOnly };
      app.SLATES[key].historical = historical;
      if (key === archived) app.SLATES[key].marketOnly = false;
      await app.loadSlate(key);
      app.state.livePicks = { picks: [] };
      app.renderHomeEdgePreview();
      const cards = registry.get('#homeEdgeList').innerHTML;
      assert.ok(app.state.slate.some((row) => Number(app.fairValue(row)) > 0) || key !== archived, 'the archived night really has fair lines in its rows');
      assert.doesNotMatch(cards, /MODEL FAIR/, `${key} historical=${historical}`);
      assert.doesNotMatch(cards, /data-rf-card/, `${key}: no fair-line hero cards`);
      assert.match(cards, /NBA shows market prices only\. There are no model fair lines for NBA yet\./);
      assert.notEqual(registry.get('#homeEdgeTitle').textContent, 'Model fair lines');
      Object.assign(app.SLATES[key], saved);
    }
  }
});
