import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSandbox, loadApp } from './helpers/sandbox.mjs';

test('card subtitle drops market when board is filtered to a single market', async () => {
  const { sandbox, registry } = buildSandbox();
  const P = loadApp(sandbox);
  await P.loadSlate('2026_w7');

  P.state.filters.market = 'receiving_yards';
  delete P.state.filters.mixedMarkets;
  P.renderBoard();
  P.renderHomeEdgePreview();

  const cardView = registry.get('#cardView');
  const boardSubtitles = [...cardView.innerHTML.matchAll(/<span class="card-team-matchup">([^<]+)<\/span>/g)].map((m) => m[1]);
  assert.ok(boardSubtitles.length > 0, 'Must have board cards rendered');

  for (const subtitle of boardSubtitles) {
    assert.match(subtitle, /^[A-Z]+\s*·\s*[A-Z]+\s*(?:vs|@)\s*[A-Z]+$/, `Board subtitle "${subtitle}" must match "POS · TEAM vs/ @ OPP"`);
    assert.ok(!subtitle.includes('Receiving'), `Board subtitle "${subtitle}" must drop market`);
  }

  const homeList = registry.get('#homeEdgeList');
  if (homeList.innerHTML) {
    const homeSubtitles = [...homeList.innerHTML.matchAll(/<div class="home-edge-player"><strong>[^<]+<\/strong><span>([^<]+)<\/span><\/div>/g)].map((m) => m[1]);
    for (const subtitle of homeSubtitles) {
      assert.ok(!subtitle.includes('Receiving'), `Home subtitle "${subtitle}" must drop market`);
      assert.match(subtitle, /^[A-Z]+\s*·\s*[A-Z]+\s*(?:vs|@)\s*[A-Z]+$/, `Home subtitle "${subtitle}" must match "POS · TEAM vs/ @ OPP"`);
    }
  }
});

test('card subtitle renders market when board view mixes markets', async () => {
  const { sandbox, registry } = buildSandbox();
  const P = loadApp(sandbox);
  await P.loadSlate('2026_w7');

  P.state.filters.market = 'all';
  P.state.filters.mixedMarkets = true;
  P.renderBoard();
  P.renderHomeEdgePreview();

  const cardView = registry.get('#cardView');
  const boardSubtitles = [...cardView.innerHTML.matchAll(/<span class="card-team-matchup">([^<]+)<\/span>/g)].map((m) => m[1]);
  assert.ok(boardSubtitles.length > 0, 'Must have board cards rendered');

  for (const subtitle of boardSubtitles) {
    assert.match(subtitle, /·\s*(?:Receiving|Receptions|Anytime|Player Prop)/, `Board subtitle "${subtitle}" must include market`);
  }

  const homeList = registry.get('#homeEdgeList');
  if (homeList.innerHTML) {
    const homeSubtitles = [...homeList.innerHTML.matchAll(/<div class="home-edge-player"><strong>[^<]+<\/strong><span>([^<]+)<\/span><\/div>/g)].map((m) => m[1]);
    for (const subtitle of homeSubtitles) {
      assert.match(subtitle, /·\s*(?:Receiving|Receptions|Anytime|Player Prop)/, `Home subtitle "${subtitle}" must include market`);
    }
  }
});
