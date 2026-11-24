// PR 1 correctness: alt lines are never shown as the book line beside fair, and the
// hero card, Player props list and Line Pricer read ONE price state per player.
// Pinned to fixtures (the w7 sandbox slate + quotes built here), never to live data.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { buildSandbox, loadApp, FROZEN_MS } from './helpers/sandbox.mjs';

const require = createRequire(import.meta.url);
const { selectMainQuote, derivePriceState, noVigOver, LABELS, MAIN_LINE_MAX_SKEW } = require('../viewer/js/price-state.js');

const NOW = Date.parse('2026-11-24T21:00:00Z');
const minutesAgo = (m) => new Date(NOW - m * 60000).toISOString();
const q = (line, over, under, extra = {}) => ({
  player_id: 'p1', player_name: 'Test Player', market: 'receiving_yards', book: 'Polymarket',
  line, over_odds: over, under_odds: under, quoted_at: minutesAgo(14), ...extra,
});

test('no-vig P(over) removes the vig from both sides', () => {
  assert.equal(noVigOver(q(50.5, -110, -110)), 0.5);
  assert.ok(Math.abs(noVigOver(q(49.5, -300, 245)) - 0.7212) < 0.001);
  assert.equal(noVigOver(q(49.5, -110, null)), null);
});

test('CeeDee Lamb case: a lone 49.5 at Under +245 is an alt line, not the book line', () => {
  // Under +245 means the market puts ~72% on Over 49.5: 49.5 is far below its centre.
  const lone = q(49.5, -300, 245);
  const pick = selectMainQuote([lone]);
  assert.equal(pick.main, null);
  assert.deepEqual(pick.alts, [lone]);
  assert.equal(pick.rule, 'no_main_line');
});

test('ladder: the line nearest a 50/50 no-vig price is the main line', () => {
  const alt = q(79.5, 134, -164); // ~42% over
  const main = q(69.5, -122, 100); // ~52% over
  const pick = selectMainQuote([alt, main]);
  assert.equal(pick.main, main);
  assert.deepEqual(pick.alts, [alt]);
  assert.equal(pick.rule, 'no_vig_center');
});

test('ladder with every line outside 40-60% has no main line', () => {
  const pick = selectMainQuote([q(29.5, -614, 245), q(49.5, -245, 113)]);
  assert.equal(pick.main, null);
  assert.equal(pick.alts.length, 2);
  assert.equal(pick.alts[0].line, 49.5, 'the alt nearest the market centre is listed first');
});

test('the 40-60% window is inclusive and ties go to the first quote in the feed', () => {
  assert.equal(MAIN_LINE_MAX_SKEW, 0.10);
  const edge = q(30.5, -150, 150); // 60% over after vig removal
  assert.ok(Math.abs(noVigOver(edge) - 0.6) < 1e-9);
  assert.equal(selectMainQuote([edge]).main, edge);
  const a = q(40.5, -110, -110);
  const b = q(41.5, -110, -110);
  assert.equal(selectMainQuote([a, b]).main, a);
});

test('a lone one-sided quote cannot be checked, so it is an alt line, never main', () => {
  const only = q(55.5, -115, null);
  const pick = selectMainQuote([only]);
  assert.equal(pick.main, null);
  assert.deepEqual(pick.alts, [only]);
  assert.equal(pick.rule, 'no_main_line');
  assert.equal(selectMainQuote([only, q(65.5, 120, null)]).main, null);
  const s = derivePriceState([only], { now: NOW, presentation: 'none' });
  assert.equal(s.isMain, false);
  assert.equal(s.quote, only, 'still shown, as an alt line');
});

test('the main-line rule never looks at the model fair line', () => {
  const quotes = [q(29.5, -614, 245, { fair_line: 29.5 }), q(49.5, -110, -110, { fair_line: 29.5 })];
  assert.equal(selectMainQuote(quotes).main.line, 49.5);
});

test('price state: no state is ever "verified" or live, even for a VERIFIED-status quote', () => {
  assert.deepEqual(Object.keys(LABELS).sort(), ['none', 'stale', 'started', 'unavailable', 'undated', 'unverified']);
  assert.ok(!Object.values(LABELS).some((label) => /verified quote$/i.test(label) && !/unverified/i.test(label)));
  const quote = q(69.5, -110, -110);
  for (const presentation of ['opportunities', 'none', 'snapshot', 'started']) {
    const s = derivePriceState([quote], { now: NOW, presentation, offerFor: () => ({ verification: 'verified', freshness: 'current' }) });
    assert.equal(s.live, false, presentation);
    assert.notEqual(s.kind, 'verified', presentation);
  }
  const unverified = derivePriceState([quote], { now: NOW, presentation: 'none', offerFor: () => ({ verification: 'unverified', freshness: 'current' }) });
  assert.equal(unverified.kind, 'unverified');
  assert.equal(unverified.label, 'Unverified quote');
  assert.equal(unverified.age, '14m ago');
});

test('price state: a 3h-old quote on a snapshot page is stale, never live', () => {
  const old = q(69.5, -110, -110, { quoted_at: minutesAgo(180) });
  const s = derivePriceState([old], { now: NOW, presentation: 'snapshot', offerFor: () => ({ verification: 'unverified', freshness: 'stale' }) });
  assert.equal(s.kind, 'stale');
  assert.equal(s.label, 'Stale quote');
  assert.equal(s.age, '3h ago');
  assert.equal(s.live, false);
});

test('price state: missing quotes, missing times and an unavailable feed', () => {
  assert.equal(derivePriceState([], { now: NOW, presentation: 'none' }).kind, 'none');
  assert.equal(derivePriceState([], { now: NOW, presentation: 'none' }).label, 'No quote');
  const undated = derivePriceState([q(69.5, -110, -110, { quoted_at: null })], { now: NOW, presentation: 'none' });
  assert.equal(undated.kind, 'undated');
  const down = derivePriceState([q(69.5, -110, -110)], { now: NOW, presentation: 'unavailable' });
  assert.equal(down.kind, 'unavailable');
  assert.equal(down.quote, null, 'an unavailable feed shows no price at all');
});

// ── viewer integration (sandbox, w7 fixture slate) ─────────────────────────

async function bootWithQuotes(makeQuotes) {
  const { sandbox, registry } = buildSandbox();
  const app = loadApp(sandbox);
  await app.loadSlate(app.defaultBootSlate());
  app.state.livePicks = { picks: [] };
  app.state.filters.market = 'receiving_yards';
  const featured = app.state.slate.slice()
    .sort((a, b) => Number(b.proj_yards || b.proj_rec || 0) - Number(a.proj_yards || a.proj_rec || 0)
      || String(a.player_display_name).localeCompare(String(b.player_display_name)))[0];
  const quotes = makeQuotes(featured);
  app.state.odds = { ...app.state.odds, quotes: [...quotes, ...app.state.odds.quotes] };
  app.setView('cards');
  app.renderAll();
  app.renderHomeEdgePreview();
  app.renderPickFlow();
  return { app, registry, featured, quotes };
}

const ladderQuote = (row, line, over, under, minutes = 14) => ({
  player_id: row.player_id, player_name: row.player_display_name, market: 'receiving_yards',
  book: 'Polymarket', line, over_odds: over, under_odds: under,
  quoted_at: new Date(FROZEN_MS - minutes * 60000).toISOString(),
  observed_at: new Date(FROZEN_MS - minutes * 60000).toISOString(),
  evaluation: { best_side: over < 0 && Math.abs(over) > 200 ? 'UNDER' : 'OVER', prob_edge: 0.4, expected_return: 0.5 },
});

function cardFor(registry, playerId) {
  const html = registry.get('#cardView').innerHTML;
  const start = html.indexOf(`data-player-id="${playerId}"`);
  assert.ok(start > 0, 'featured player card is rendered');
  const end = html.indexOf('<article', start + 10);
  return html.slice(start, end === -1 ? undefined : end);
}

test('list: an alt line is labelled "Alt line" and never shown as the book line beside fair', async () => {
  const { registry, featured } = await bootWithQuotes((row) => [ladderQuote(row, 49.5, -300, 245)]);
  const card = cardFor(registry, featured.player_id);
  assert.match(card, /data-alt-line="1">Alt line 49\.5</);
  assert.doesNotMatch(card, /ps-scan-value-book">(Over|Under) 49\.5/);
  // No book marker for an alt line on the distribution strip.
  assert.doesNotMatch(card, /class="rf-dist-book"/);
});

test('list: a lone one-sided quote is an alt line with no book marker', async () => {
  const { registry, featured } = await bootWithQuotes((row) => [{ ...ladderQuote(row, 99.5, -115, -115), under_odds: null }]);
  const card = cardFor(registry, featured.player_id);
  assert.match(card, /data-alt-line="1">Alt line 99\.5</);
  assert.doesNotMatch(card, /ps-scan-value-book">/);
  assert.doesNotMatch(card, /class="rf-dist-book"/);
});

test('list: the main line of a ladder is the one shown beside fair', async () => {
  const { registry, featured, app } = await bootWithQuotes((row) => [
    ladderQuote(row, 29.5, -614, 245),
    ladderQuote(row, 104.5, -110, -110),
  ]);
  assert.equal(app.findQuote(featured, 'receiving_yards').line, 104.5);
  const card = cardFor(registry, featured.player_id);
  assert.match(card, /ps-scan-value-book">(Over|Under) 104\.5 · /);
  assert.match(card, /class="rf-dist-book"/, 'the main line gets the book marker on the range');
  assert.doesNotMatch(card, /29\.5/);
});

test('hero, list and Line Pricer show the same price state for the same player', async () => {
  for (const [name, quotes, wantKind] of [
    ['no quote', () => [], 'none'],
    ['3h-old alt line', (row) => [ladderQuote(row, 49.5, -300, 245, 180)], 'stale'],
    // The w7 fixture page is a snapshot (its other quotes are old), so even a 5-minute-old
    // quote reads as stale: one price state for the whole page, never "live".
    ['fresh main line on a snapshot page', (row) => [ladderQuote(row, 104.5, -110, -110, 5)], 'stale'],
  ]) {
    const { app, registry, featured } = await bootWithQuotes(quotes);
    const expected = app.rowPriceState(featured, 'receiving_yards');
    assert.equal(expected.kind, wantKind, `${name}: price state`);
    assert.equal(expected.live, false, `${name}: fixture page is never live`);

    const hero = registry.get('#homeEdgeList').innerHTML;
    const heroIdx = hero.indexOf(featured.player_display_name);
    assert.ok(heroIdx > 0, `${name}: featured player is in the hero`);
    const heroBadge = hero.slice(heroIdx).match(/class="rf-badge" data-price-state="([a-z]+)">([^<]+)</);
    assert.deepEqual([heroBadge[1], heroBadge[2]], [expected.kind, expected.label], `${name}: hero`);

    const pricer = registry.get('#pickResult').innerHTML.match(/class="pick-book" data-price-state="([a-z]+)"><strong>([^<]+)</);
    assert.deepEqual([pricer[1], pricer[2]], [expected.kind, expected.label], `${name}: Line Pricer`);

    const card = cardFor(registry, featured.player_id);
    if (expected.kind === 'none') {
      assert.match(card, /<span class="ps-scan-sub">No quote<\/span>/, `${name}: list`);
    } else {
      const listState = card.match(/class="ps-scan-sub ps-scan-state" data-price-state="([a-z]+)">([^<]+)<\/span>/);
      assert.deepEqual([listState[1], listState[2]], [expected.kind, expected.label], `${name}: list`);
    }
    assert.doesNotMatch(hero + card, /No live price/);
    assert.doesNotMatch(hero + card + registry.get('#pickResult').innerHTML, />Verified quote</);
  }
});
