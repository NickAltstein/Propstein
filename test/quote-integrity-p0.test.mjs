// P0 Quote-Integrity Regression Tests for viewer/r/tailcheck.js
// Exercises the exact three P0 failures:
//   1. Unavailable / unknown / malformed / timed-out / stale health failing to suppress verdicts
//   2. Older better same-book price beating a newer worse same-book price (and out-of-order delivery)
//   3. Fixture / seeded / replayed / test quotes entering observed output
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const VIEWER = path.resolve('viewer');

function loadTailCheck(fetchStub) {
  const win = {
    PropSteinMath: null,
    PropsteinTail: null,
    fetch: fetchStub,
  };
  const ctx = vm.createContext({
    window: win,
    fetch: fetchStub,
    Date,
    Math,
    Number,
    String,
    Array,
    Object,
    Set,
    Map,
    Promise,
    Error,
    RegExp,
    JSON,
    console,
  });
  vm.runInContext(fs.readFileSync(path.join(VIEWER, 'js/quant-math.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(VIEWER, 'r/tailcheck.js'), 'utf8'), ctx);
  return win.PropsteinTail;
}

function makeResponse(ok, body, status = ok ? 200 : 500) {
  return {
    ok,
    status,
    async json() {
      if (body instanceof Error) throw body;
      return body;
    },
  };
}

const SEL_3PM_4PLUS = {
  gameId: 'nba-20261124-NYK-BOS',
  playerId: 'nba-jaylen-brown',
  market: 'threes',
  side: 'OVER',
  threshold: 3.5,
};

const NOW_MS = Date.parse('2026-11-24T18:00:00Z');

// ---------------------------------------------------------------------------
// P0 #1: Health unavailable / unknown / malformed / timeout must fail closed
// ---------------------------------------------------------------------------

test('P0-1: missing / timed-out / malformed / unknown health suppresses quotes (fail closed)', async () => {
  const validFeed = {
    quotes: [
      { game_id: SEL_3PM_4PLUS.gameId, player_id: SEL_3PM_4PLUS.playerId, market: 'threes', line: 3.5, over_odds: 195, book: 'DraftKings', observed_at: '2026-11-24T17:59:50Z' },
    ],
  };

  const healthFailures = [
    ['transport_error', async () => { throw new Error('network timeout'); }],
    ['http_503', async () => makeResponse(false, { error: 'unavailable' }, 503)],
    ['malformed_json', async () => makeResponse(true, new SyntaxError('bad json'), 200)],
    ['missing_sport', async () => makeResponse(true, { schema_version: 'feed-status.v1', generated_at: '2026-11-24T17:59:55Z', sports: {} }, 200)],
    ['unknown_level', async () => makeResponse(true, { schema_version: 'feed-status.v1', generated_at: '2026-11-24T17:59:55Z', sports: { nba: { level: 'mystery_state', fail_closed: false } } }, 200)],
    ['level_live_not_written', async () => makeResponse(true, { schema_version: 'feed-status.v1', generated_at: '2026-11-24T17:59:55Z', sports: { nba: { level: 'live', fail_closed: false } } }, 200)],
    ['level_healthy_not_written', async () => makeResponse(true, { schema_version: 'feed-status.v1', generated_at: '2026-11-24T17:59:55Z', sports: { nba: { level: 'healthy', fail_closed: false } } }, 200)],
    ['missing_generated_at', async () => makeResponse(true, { schema_version: 'feed-status.v1', sports: { nba: { level: 'ok', fail_closed: false } } }, 200)],
  ];

  for (const [label, healthHandler] of healthFailures) {
    const Tail = loadTailCheck(async (url) => {
      if (url === '/feeds/feed_status.json') return healthHandler();
      if (url === '/nba_odds.json') return makeResponse(true, validFeed, 200);
      return makeResponse(false, {}, 404);
    });
    const res = await Tail.loadCurrentQuotes('nba', ['nba_odds.json']);
    assert.equal(res.status, 'fail_closed', `Expected fail_closed on health failure "${label}", got "${res.status}"`);
    assert.equal(res.quotes.length, 0, `Expected zero quotes on health failure "${label}"`);
  }
});

test('P0-1: suppressed and degraded are intended pauses: quotes stay hidden, with the reason, not an error', async () => {
  const validFeed = {
    quotes: [
      { game_id: SEL_3PM_4PLUS.gameId, player_id: SEL_3PM_4PLUS.playerId, market: 'threes', line: 3.5, over_odds: 195, book: 'DraftKings', observed_at: '2026-11-24T17:59:50Z' },
    ],
  };
  const statusWith = (row) => ({ schema_version: 'feed-status.v1', generated_at: '2026-11-24T17:59:55Z', sports: { nba: row } });
  for (const [label, row, reason] of [
    ['slate blocked', { level: 'suppressed', fail_closed: false, slate_state: { state: 'SLATE_BLOCKED' } }, 'slate blocked'],
    ['between weeks', { level: 'suppressed', fail_closed: false, slate_state: { state: 'NO_CURRENT_SLATE' } }, 'between weeks'],
    ['no game in window', { level: 'suppressed', fail_closed: false }, 'no game in the current window'],
    ['degraded', { level: 'degraded', fail_closed: false, age_hours: 2.6, stale_since: '2026-11-24T15:24:00Z' }, 'feed is 2h behind (last update 2026-11-24 15:24 UTC)'],
    ['degraded, no age recorded', { level: 'degraded', fail_closed: false }, 'feed is behind'],
  ]) {
    const Tail = loadTailCheck(async (url) => {
      if (url === '/feeds/feed_status.json') return makeResponse(true, statusWith(row), 200);
      if (url === '/nba_odds.json') return makeResponse(true, validFeed, 200);
      return makeResponse(false, {}, 404);
    });
    const res = await Tail.loadCurrentQuotes('nba', ['nba_odds.json']);
    assert.equal(res.status, 'paused', `${label}: suppressed must report a pause`);
    assert.equal(res.pauseReason, reason, `${label}: pause reason`);
    assert.equal(res.quotes.length, 0, `${label}: no quotes while paused`);
  }

  // Only explicitly healthy levels serve quotes; suppressed with fail_closed true is still an error.
  const Tail = loadTailCheck(async (url) => {
    if (url === '/feeds/feed_status.json') return makeResponse(true, statusWith({ level: 'suppressed', fail_closed: true }), 200);
    if (url === '/nba_odds.json') return makeResponse(true, validFeed, 200);
    return makeResponse(false, {}, 404);
  });
  const res = await Tail.loadCurrentQuotes('nba', ['nba_odds.json']);
  assert.equal(res.status, 'fail_closed');
  assert.equal(res.quotes.length, 0);
});

// ---------------------------------------------------------------------------
// P0 #2: Newer worse same-book price must replace older better price
// ---------------------------------------------------------------------------

test('P0-2: newer worse same-book price replaces older better price regardless of arrival order', () => {
  const Tail = loadTailCheck(async () => makeResponse(false, {}, 404));
  const olderBetter = {
    game_id: SEL_3PM_4PLUS.gameId,
    player_id: SEL_3PM_4PLUS.playerId,
    market: 'threes',
    line: 3.5,
    over_odds: 210, // better price, OLDER
    book: 'DraftKings',
    observed_at: '2026-11-24T17:58:00Z',
  };
  const newerWorse = {
    game_id: SEL_3PM_4PLUS.gameId,
    player_id: SEL_3PM_4PLUS.playerId,
    market: 'threes',
    line: 3.5,
    over_odds: 170, // worse price, NEWER
    book: 'DraftKings',
    observed_at: '2026-11-24T17:59:50Z',
  };

  for (const order of [[olderBetter, newerWorse], [newerWorse, olderBetter]]) {
    const container = { innerHTML: '' };
    const summary = Tail.renderComparison(container, {
      title: 'Check this number',
      selection: SEL_3PM_4PLUS,
      posted: { label: 'Your slip', tagText: 'USER-ENTERED', tagClass: 'is-user', odds: 185, at: '2026-11-24T17:59:00Z' },
      fair: null,
      source: { status: 'ok', quotes: order },
      nowMs: NOW_MS,
      maxAgeSeconds: 300,
      perBook: ['DraftKings', 'FanDuel'],
      dollarDiff: true,
    });
    assert.equal(summary.books.DraftKings?.odds, 170, 'Newer worse DK price (+170) must win over older better price (+210)');
  }
});

// ---------------------------------------------------------------------------
// P0 #3: Fixture / seeded / replayed / test quotes must be excluded
// ---------------------------------------------------------------------------

test('P0-3: fixture, seeded, replayed, and test quotes cannot enter eligibleQuotes', () => {
  const Tail = loadTailCheck(async () => makeResponse(false, {}, 404));
  const base = {
    game_id: SEL_3PM_4PLUS.gameId,
    player_id: SEL_3PM_4PLUS.playerId,
    market: 'threes',
    line: 3.5,
    over_odds: 205,
    book: 'DraftKings',
    observed_at: '2026-11-24T17:59:50Z',
  };
  const contaminated = [
    { ...base, _fixture: true },
    { ...base, fixture: true },
    { ...base, is_fixture: true },
    { ...base, synthetic: true },
    { ...base, seeded: true },
    { ...base, replayed: true },
    { ...base, is_test: true },
    { ...base, provenance: 'synthetic-fixture' },
    { ...base, source_env: 'test' },
  ];
  const res = Tail.eligibleQuotes(contaminated, SEL_3PM_4PLUS, {
    nowMs: NOW_MS,
    maxAgeSeconds: 300,
    books: ['DraftKings', 'FanDuel'],
  });
  assert.equal(res.eligible.length, 0, `Expected 0 eligible quotes from fixture/test inputs, got ${res.eligible.length}`);
});
