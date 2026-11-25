import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSandbox, loadApp, attachSyntheticQuote } from './helpers/sandbox.mjs';

/**
 * UNCALIBRATED BADGE (audit H9) — spec item 5 requires a VISIBLE uncalibrated
 * badge on rows of uncalibrated markets; a config flag alone is cosmetic.
 *
 * Badge truth = breaker truth: circuit_breaker_config.json sports[n].calibrated
 * (adapter.breakerFallback as the fetch-failure fallback). NBA v1 ships
 * calibrated:false, but NBA has no model (#270 Option A) and renders no model rows.
 * NFL ships calibrated:true → no badge element may exist anywhere on NFL rows (the
 * badge must be a real signal, not chrome); flipped to calibrated:false, every NFL
 * model row must carry it.
 */

async function renderedRows(seed = {}, { uncalibrated = false } = {}) {
  const { sandbox, registry } = buildSandbox({ seedLocalStorage: seed });
  const P = loadApp(sandbox);
  await P.loadSlate(P.defaultBootSlate());
  // Badge truth = breaker config: mark the sport uncalibrated to see the badge on model rows.
  if (uncalibrated) {
    P.state.breakerConfig.sports[P.SPORT_ID || 'nfl'].calibrated = false;
    P.setPlayMode('all');
  }
  // Shipped demo quotes intentionally fail slate matching (provenance hotfix);
  // NFL render paths need the harness-only attached quote. NBA fixture feeds
  // still attach natively, so they are left untouched.
  if ((P.SPORT_ID || 'nfl') === 'nfl') attachSyntheticQuote(P);
  P.setView('table');
  P.renderAll();
  const tableHtml = registry.get('#slateRows')?.innerHTML || '';
  P.setView('cards');
  P.renderAll();
  const cardsHtml = registry.get('#cardView')?.innerHTML || '';
  return { P, tableHtml, cardsHtml };
}

test('model rows of an uncalibrated sport carry the UNCALIBRATED badge in table and card views', async () => {
  // NFL is the sport with a model; with its breaker config set to calibrated:false every
  // rendered model row must show the badge. (NBA has no model and renders no model rows.)
  const { P, tableHtml, cardsHtml } = await renderedRows({}, { uncalibrated: true });
  assert.ok(P.filteredRows().length > 0, 'the view must have rows');
  const renderedCount = (tableHtml.match(/<tr[^>]*class="[^"]*slate-row/g) || []).length;
  assert.ok(renderedCount > 0, 'table rendered rows');
  const badgeCount = (tableHtml.match(/class="uncal-tag"/g) || []).length;
  assert.equal(badgeCount, renderedCount, 'every rendered model row carries the badge');
  assert.equal((cardsHtml.match(/class="uncal-tag"/g) || []).length, (cardsHtml.match(/data-scan-card="1"/g) || []).length, 'every card carries it');
  assert.match(tableHtml, /UNCALIBRATED/);
  assert.match(cardsHtml, /class="uncal-tag"/, 'cards view carries the badge too');
});

test('NBA config stays uncalibrated, and NBA renders no model rows to badge', async () => {
  const { P, tableHtml, cardsHtml } = await renderedRows({ propwire_active_sport: 'nba' });
  const cfg = P.state.breakerConfig;
  assert.equal(cfg.sports.nba.calibrated, false, 'badge reflects breaker config truth');
  assert.equal(P.adapter.model, null);
  assert.doesNotMatch(tableHtml + cardsHtml, /slate-row|data-scan-card|MODEL FAIR/, 'no NBA model rows');
});

test('NFL rows render NO uncalibrated badge (calibrated sport, no chrome)', async () => {
  const { tableHtml, cardsHtml } = await renderedRows();
  assert.ok(tableHtml.includes('slate-row'), 'NFL table rendered');
  assert.doesNotMatch(tableHtml, /uncal-tag|UNCALIBRATED/, 'no badge on NFL table rows');
  assert.doesNotMatch(cardsHtml, /uncal-tag|UNCALIBRATED/, 'no badge on NFL cards');
});
