import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { buildSandbox, loadApp, FROZEN_MS, VIEWER } from './helpers/sandbox.mjs';

function makeResponsiveApp({ isMobile = false, sport = 'nfl' } = {}) {
  const { sandbox, registry } = buildSandbox({
    seedLocalStorage: sport === 'nfl' ? {} : { propwire_active_sport: sport },
  });
  sandbox.window.matchMedia = (query) => ({
    matches: isMobile && String(query).includes('768px'),
    media: String(query || ''),
    addEventListener() {},
    addListener() {},
  });
  const app = loadApp(sandbox);
  return { app, sandbox, registry };
}

async function seedEvaluatedNflSlate(app, { sandbox = null, quoteOverrides = {}, freshFeed = true, newsFresh = true, featureLocked = false } = {}) {
  await app.loadSlate('2026_w7');
  app.state.featureContextLocked = featureLocked;
  app.state.userTier = 'free';
  app.state.filters.market = 'receiving_yards';
  app.state.filters.playMode = 'all';
  const row = app.filteredRows()[0] || app.state.slate[0];
  app.state.openSnapshots = {};
  const game = app.state.games.find((g) => g.game_id === row.game_id);
  const nowIso = new Date(FROZEN_MS).toISOString();
  const staleIso = new Date(FROZEN_MS - 4 * 3600_000).toISOString();
  const quotedIso = new Date(FROZEN_MS - 10 * 60_000).toISOString();
  const startIso = new Date(FROZEN_MS + 3 * 3600_000).toISOString();
  if (game) {
    game.gameday = '2026-10-22';
    game.tipoff_utc = startIso;
    game.startAt = startIso;
    if (sandbox) {
      sandbox.window.PropSteinScheduleEvents = [{
        date: startIso,
        competitions: [{
          competitors: [
            { homeAway: 'away', team: { abbreviation: game.away_team } },
            { homeAway: 'home', team: { abbreviation: game.home_team } },
          ],
          status: { type: { state: 'pre', name: 'STATUS_SCHEDULED', detail: 'Pre' } },
        }],
      }];
    }
  }
  const quote = {
    quote_id: 'pm_test_quote_1',
    provider_quote_id: 'pm_test_quote_1',
    book: 'Polymarket',
    source: 'https://clob.polymarket.com',
    source_url: 'https://polymarket.com/market/test-quote-1',
    receipt_id: 'pm_test_quote_1',
    verification_receipt_id: 'pmr_test_1',
    content_hash: 'a'.repeat(64),
    verified: true,
    verification_status: 'VERIFIED',
    player_id: row.player_id,
    player_name: row.player_display_name,
    team: row.team,
    opponent: row.opponent_team,
    game_id: row.game_id,
    position: row.position,
    market: 'receiving_yards',
    line: 49.5,
    over_odds: -120,
    under_odds: 100,
    quoted_at: quotedIso,
    observed_at: nowIso,
    scheduled_start: startIso,
    pre_start: true,
    evaluation: {
      is_qualified: true,
      status: 'QUALIFIED',
      best_side: 'OVER',
      prob_edge: 0.12,
      break_even_adv: 0.08,
      expected_return: 0.14,
      actionable_stake: 0.02,
    },
    ...quoteOverrides,
  };
  app.state.feedHealth = {
    dataKind: 'live',
    odds: 'ok',
    news: 'ok',
    oddsUpdatedAt: freshFeed ? nowIso : staleIso,
    newsLastPolledAt: newsFresh ? nowIso : staleIso,
    thresholds: app.state.feedHealth?.thresholds || { oddsStaleSeconds: 21600, newsStaleSeconds: 300 },
  };
  app.state.newsWatcherState = {
    is_alive: newsFresh,
    last_polled_at: newsFresh ? nowIso : staleIso,
    teams_locked: {},
    players_pending: {},
  };
  app.state.odds = {
    updated_at: freshFeed ? nowIso : staleIso,
    feed_status: quote.verified ? 'VERIFIED' : 'OBSERVED',
    quotes: [quote],
  };
  app.state.filters.market = 'receiving_yards';
  app.state.filters.playMode = 'all';
  app.renderAll();
  app.updateFeedHealthUI();
  return { row, quote, game };
}

for (const widthCase of [
  { label: 'desktop', isMobile: false, selector: '#slateRows' },
  { label: 'mobile 375px', isMobile: true, selector: '#cardView' },
]) {
  test(`${widthCase.label}: qualified fixture shows live headline, single-venue terms, strict green, quote details, and Event started override`, async () => {
    const { app, sandbox, registry } = makeResponsiveApp({ isMobile: widthCase.isMobile });
    if (!widthCase.isMobile) app.setView('table');
    const { row, quote } = await seedEvaluatedNflSlate(app, { sandbox });

    const headline = registry.get('.terminal-snapshot-status').textContent;
    assert.match(headline, /^Live · Polymarket · Quoted 10m ago · 1 qualified/);

    const boardHtml = registry.get(widthCase.selector).innerHTML;
    assert.match(boardHtml, /offer-qualified-green/);
    assert.match(boardHtml, /49\.5/);
    assert.match(boardHtml, /-120/);
    assert.match(boardHtml, /Polymarket/);
    assert.match(boardHtml, /10m ago/);
    assert.match(boardHtml, /View quote details/);
    if (widthCase.isMobile) {
      assert.match(boardHtml, /offer-market-pill">(?:Yards|Receiving [Yy]ards)</);
    }

    sandbox.window.PropSteinTest.openDetail(row.player_id);
    const detailHtml = registry.get('#detailContent').innerHTML;
    assert.match(detailHtml, /Quote source/);
    assert.match(detailHtml, /Polymarket/);
    assert.match(detailHtml, /Provider timestamp/);
    assert.match(detailHtml, /10m ago/);
    assert.match(detailHtml, /Qualification result/);
    assert.match(detailHtml, /Qualified/);
    assert.match(detailHtml, /Model probability/);
    assert.match(detailHtml, /Qualifying line/);
    assert.match(detailHtml, /<details class="quote-deep-details">\s*<summary>Receipt &amp; methodology<\/summary>/);

    // Event started override: once kickoff passes, the green styling must disappear immediately.
    sandbox.window.PropSteinScheduleEvents = [{
      date: new Date(FROZEN_MS - 30 * 60_000).toISOString(),
      competitions: [{
        competitors: [
          { homeAway: 'away', team: { abbreviation: row.is_home ? row.opponent_team : row.team } },
          { homeAway: 'home', team: { abbreviation: row.is_home ? row.team : row.opponent_team } },
        ],
        status: { type: { state: 'in', name: 'STATUS_IN_PROGRESS', detail: 'Q1' } },
      }],
    }];
    quote.pre_start = false;
    app.renderAll();
    app.updateFeedHealthUI();

    const postStartHeadline = registry.get('.terminal-snapshot-status').textContent;
    assert.match(postStartHeadline, /^Event started · Polymarket · Quoted 10m ago/);
    assert.doesNotMatch(postStartHeadline, /qualified|No qualifying offers/);
    const postStartHtml = registry.get(widthCase.selector).innerHTML;
    assert.doesNotMatch(postStartHtml, /offer-qualified-green/);
    assert.doesNotMatch(postStartHtml, /row-state-actionable/);
    assert.match(postStartHtml, /CLOSED|EVENT STARTED/);

    // Open detail panel must refresh in place without closing & reopening.
    assert.equal(registry.get('#detailDialog').open, true);
    const postStartDetailHtml = registry.get('#detailContent').innerHTML;
    assert.doesNotMatch(postStartDetailHtml, /offer-qualified-green/);
    assert.doesNotMatch(postStartDetailHtml, /pd-badge-verified/);
    assert.match(postStartDetailHtml, /Event started/);
    assert.match(registry.get('#detailSummary').textContent, /Game started/);
  });

  test(`${widthCase.label}: fresh fixture with zero qualifying offers shows No qualifying offers and zero green`, async () => {
    const { app, sandbox, registry } = makeResponsiveApp({ isMobile: widthCase.isMobile });
    if (!widthCase.isMobile) app.setView('table');
    await seedEvaluatedNflSlate(app, {
      sandbox,
      quoteOverrides: {
        quoted_at: new Date(FROZEN_MS - 5 * 60_000).toISOString(),
        evaluation: { is_qualified: false, status: 'PASS', best_side: 'OVER', prob_edge: 0.02, expected_return: 0.01 },
      },
    });

    const headline = registry.get('.terminal-snapshot-status').textContent;
    assert.match(headline, /^Live · Polymarket · Quoted 5m ago · No qualifying offers/);
    const boardHtml = registry.get(widthCase.selector).innerHTML;
    assert.doesNotMatch(boardHtml, /offer-qualified-green/);
    assert.doesNotMatch(boardHtml, /row-state-actionable/);
  });

  test(`${widthCase.label}: snapshot-only fixture explains why without tapping and never substitutes ingestion time`, async () => {
    const { app, registry } = makeResponsiveApp({ isMobile: widthCase.isMobile });
    if (!widthCase.isMobile) app.setView('table');
    await seedEvaluatedNflSlate(app, {
      freshFeed: false,
      featureLocked: true,
      quoteOverrides: {
        quoted_at: undefined,
        observed_at: new Date(FROZEN_MS).toISOString(),
        verified: false,
        verification_status: 'OBSERVED',
        evaluation: { is_qualified: false, status: 'OBSERVED', best_side: 'OVER', prob_edge: 0.14, expected_return: 0.16 },
      },
    });

    const headline = registry.get('.terminal-snapshot-status').textContent;
    assert.match(headline, /Snapshot only · Polymarket · Provider quote time unknown\./);
    assert.doesNotMatch(headline, /qualified|No qualifying offers/);
    assert.match(headline, /Model context has no pre-pricing capture timestamps/);
    assert.match(registry.get('#terminalFeedDetailsContent').innerHTML, /Ingested:/);

    const boardHtml = registry.get(widthCase.selector).innerHTML;
    assert.doesNotMatch(boardHtml, /offer-qualified-green/);
    assert.doesNotMatch(boardHtml, /row-state-actionable/);
  });
}

test('cents units are preserved verbatim and missing prices display Price unavailable', async () => {
  const { app, sandbox, registry } = makeResponsiveApp({ isMobile: true });
  const { row, quote } = await seedEvaluatedNflSlate(app, {
    sandbox,
    quoteOverrides: {
      // NFL boards show Polymarket only (other venues are hidden); cents still render verbatim.
      book: 'Polymarket',
      over_odds: null,
      under_odds: null,
      price_unit: 'cents',
      over_cents: 54,
      under_cents: 46,
    },
  });
  let cardsHtml = registry.get('#cardView').innerHTML;
  assert.match(cardsHtml, /Over 49\.5 · 54¢/);
  assert.doesNotMatch(cardsHtml, /-110/);

  sandbox.window.PropSteinTest.openDetail(row.player_id);
  assert.match(registry.get('#detailContent').innerHTML, /Over 49\.5 · 54¢/);

  delete quote.over_cents;
  delete quote.under_cents;
  delete quote.price_unit;
  app.renderBoard();
  cardsHtml = registry.get('#cardView').innerHTML;
  assert.match(cardsHtml, /Price unavailable/);
  assert.doesNotMatch(cardsHtml, /-110/);
  assert.doesNotMatch(cardsHtml, /offer-qualified-green/);

  // The same quote from Kalshi is not the NFL board's venue: hidden, not re-labelled.
  Object.assign(quote, { book: 'Kalshi', price_unit: 'cents', over_cents: 54, under_cents: 46 });
  app.renderBoard();
  cardsHtml = registry.get('#cardView').innerHTML;
  assert.doesNotMatch(cardsHtml, /49\.5 · 54¢|Kalshi/);
});

test('model-less NBA row shows Model unavailable in headline and quote details drawer', () => {
  const { app, sandbox, registry } = makeResponsiveApp({ isMobile: true, sport: 'nba' });
  app.SLATES['2026-11-17'] = { marketOnly: true, historical: false, date: '2026-11-17' };
  app.state.slateKey = '2026-11-17';
  app.state.slate = [];
  app.state.verificationWindows = JSON.parse(fs.readFileSync(path.join(VIEWER, 'verification_windows.json'), 'utf8'));
  app.state.feedHealth = { dataKind: 'live', odds: 'ok', news: 'ok' };
  app.state.games = [{ game_id: 'nba-20261117-CHA-LAL', away_team: 'CHA', home_team: 'LAL', tipoff_utc: new Date(FROZEN_MS + 3600_000).toISOString() }];
  app.state.odds = {
    feed_status: 'live-unverified',
    updated_at: new Date(FROZEN_MS).toISOString(),
    quotes: [{
      quote_id: 'nba_kalshi_test_1',
      player_name: 'Brandon Miller',
      game_id: 'nba-20261117-CHA-LAL',
      book: 'Kalshi',
      source: 'Kalshi exchange (unverified market quote)',
      market: 'points',
      line: 19.5,
      over_odds: 122,
      under_odds: -127,
      quoted_at: new Date(FROZEN_MS - 5 * 60_000).toISOString(),
      observed_at: new Date(FROZEN_MS).toISOString(),
      scheduled_start: new Date(FROZEN_MS + 3600_000).toISOString(),
      pre_start: true,
      _fixture: false,
      verified: false,
    }],
  };
  app.state.filters.market = 'points';
  app.setPlayMode('all');
  app.renderAll();
  app.updateFeedHealthUI();

  const headline = registry.get('.terminal-snapshot-status').textContent;
  assert.match(headline, /Model unavailable/);
  assert.doesNotMatch(headline, /No qualifying offers/);
  assert.doesNotMatch(headline, /qualified/);

  const emptyHtml = registry.get('#emptyState').innerHTML;
  assert.match(emptyHtml, /View quote details/);
  assert.match(emptyHtml, /Kalshi · 5m ago/);

  // Verify the actual DOM click path inside #emptyState opens #detailDialog (regression guard).
  registry.get('#emptyState').dispatchEvent({
    type: 'click',
    preventDefault() {},
    stopPropagation() {},
    target: {
      closest(selector) {
        if (selector === '[data-inspect-quote]') {
          return { dataset: { inspectQuote: 'nba_kalshi_test_1' } };
        }
        return null;
      },
    },
  });
  assert.equal(registry.get('#detailDialog').open, true);
  const detailHtml = registry.get('#detailContent').innerHTML;
  assert.match(detailHtml, /Kalshi/);
  assert.match(detailHtml, /Provider timestamp/);
  assert.match(detailHtml, /Model unavailable/);
  assert.doesNotMatch(detailHtml, /Model probability/);
  assert.doesNotMatch(detailHtml, /Qualifying line/);
  assert.match(detailHtml, /<details class="quote-deep-details">/);
});

test('CSS reserves side panel on desktop, bottom sheet and stacked non-scrolling rows on mobile, and green strictly on qualified offers', () => {
  const css = fs.readFileSync(path.join(VIEWER, 'terminal-ui.css'), 'utf8');
  assert.match(css, /@media \(min-width: 769px\)[\s\S]*dialog\.detail-dialog[\s\S]*justify-content:\s*flex-end/);
  assert.match(css, /@media \(max-width: 768px\)[\s\S]*\.mobile-quant-row \.card-grid-row:not\(\.v176-research\)[\s\S]*flex-direction:\s*column/);
  assert.match(css, /\.offer-qualified-green/);
  assert.match(css, /:not\(\.offer-qualified-green\)/);
});
