// NBA 3PM (3+ / 4+) portable ticket, side by side with the NFL anytime TD ticket.
// 390px dark mode, fixed clock. Empty state runs on the real committed feeds;
// the both-books comparison runs on local fixtures (not evidence of a live feed).
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';

const VIEWER_DIR = path.resolve('viewer');
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml' };
const NOW = '2026-11-24T18:00:00Z';
const FRESH = '2026-11-24T17:30:00Z';
const STALE = '2026-11-24T08:00:00Z';
const FORBIDDEN_RE = /\bkelly\b|\bedge\b|\bEV\b|expected return|\d+(\.\d+)?\s*%|you should bet|bet (?:was )?placed|we placed|take it|\bpass\b|still playable|bet now|place bet|tail this|hammer/i;

// Real committed data for the empty state.
const REAL_NBA_FEED = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'nba_odds_2026-11-23.json'), 'utf8'));
const REAL_NBA_PLAYER = REAL_NBA_FEED.quotes.find((q) => q && q.player_id && q.game_id && q._fixture !== true);
const REAL_NFL_ROW = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'slate_2026_w11.json'), 'utf8')).find((r) => r && r.player_id && r.game_id);

const FX_GAME = 'nba-20261124-NYK-BOS';
const shooter = (o) => ({ player_id: 'odds-nba-test-shooter', player_name: 'Test Shooter', team: 'NYK', opponent: 'BOS', game_id: FX_GAME, market: 'threes', side: undefined, under_odds: -200, observed_at: FRESH, ...o });

function b64url(obj) {
  return Buffer.from(JSON.stringify(obj), 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function findChrome() {
  const candidates = [process.env.CHROME_PATH, '/opt/pw-browsers/chromium', '/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium'].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p) && fs.statSync(p).isFile()) || null;
}

test('NBA 3PM ticket: real empty state, fixture comparison, side by side with NFL anytime TD', async (t) => {
  const executablePath = findChrome();
  if (!executablePath) {
    t.skip('No local Chrome/Chromium executable available in this CI environment');
    return;
  }
  assert.ok(REAL_NBA_PLAYER, 'real NBA feed has a player');
  assert.ok(REAL_NFL_ROW, 'real NFL slate has a player');
  // Precondition for the honest empty state: no DK/FD quotes in the real feeds.
  assert.equal(REAL_NBA_FEED.quotes.filter((q) => ['DraftKings', 'FanDuel'].includes(q.book) && q.market === 'threes').length, 0);

  const fx = { on: false, feed: { quotes: [] }, status: 200 };
  const server = http.createServer((req, res) => {
    const urlPath = (req.url || '/').split('?')[0];
    const json = (status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
    if (fx.on && urlPath === '/feeds/slate-index.json') {
      const idx = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'feeds/slate-index.json'), 'utf8'));
      Object.values(idx.sports.nba.slates).forEach((s) => { s.odds = null; });
      idx.sports.nba.default_slate = 'fx-nba';
      idx.sports.nba.slates['fx-nba'] = { season: 2026, date: '2026-11-24', slate: 'fx_nba_slate.json', games: 'nba_games_2026-11-23.json', odds: 'fx_nba_odds.json', snapshots: null, historical: false, marketOnly: true };
      return json(200, idx);
    }
    if (fx.on && urlPath === '/fx_nba_slate.json') return json(200, []);
    if (fx.on && urlPath === '/fx_nba_odds.json') return fx.status === 200 ? json(200, fx.feed) : json(fx.status, { error: 'down' });
    let rel = urlPath;
    if (urlPath === '/check/' || urlPath === '/check') rel = '/check/index.html';
    else if (urlPath === '/t/') rel = '/t/index.html';
    const filePath = path.join(VIEWER_DIR, rel);
    if (!filePath.startsWith(VIEWER_DIR) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.writeHead(404);
      res.end('not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream' });
    fs.createReadStream(filePath).pipe(res);
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true, executablePath });

  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
    const page = await context.newPage();
    await page.clock.setFixedTime(new Date(NOW));
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));

    const read = () => page.evaluate(() => {
      const bookRow = (book) => {
        const el = document.querySelector(`[data-book-row="${book}"]`);
        return el ? {
          text: el.innerText,
          noQuote: Boolean(el.querySelector('[data-no-current-quote]')),
          vsPosted: el.querySelector('[data-vs-posted]')?.getAttribute('data-vs-posted') || null,
          vsFair: el.querySelector('[data-vs-fair]')?.getAttribute('data-vs-fair') || null,
          diff: el.querySelector('[data-dollar-diff]')?.getAttribute('data-dollar-diff') || null,
        } : null;
      };
      return {
        all: document.body.innerText,
        title: document.querySelector('#ticketTitle')?.innerText || '',
        ticketStatus: document.querySelector('[data-ticket-status]')?.getAttribute('data-ticket-status') || null,
        slipStatus: document.querySelector('[data-slip-status]')?.getAttribute('data-slip-status') || null,
        sourceStatus: document.querySelector('[data-source-status]')?.getAttribute('data-source-status') || null,
        fairUnavailable: Boolean(document.querySelector('[data-fair-unavailable]')),
        dk: bookRow('DraftKings'),
        fd: bookRow('FanDuel'),
        shareButtons: document.querySelectorAll('[data-share-receipt]').length,
        receiptLinks: [...document.querySelectorAll('a[href]')].filter((a) => /\/r\/[0-9a-f]/.test(a.getAttribute('href'))).length,
        fits: document.documentElement.scrollWidth <= window.innerWidth + 2,
      };
    });
    const clean = (s, where) => {
      assert.doesNotMatch(s.all, FORBIDDEN_RE, `forbidden copy on ${where}`);
      assert.ok(s.fits, `no horizontal overflow on ${where}`);
      assert.equal(s.shareButtons, 0, `no receipt share on ${where}`);
      assert.equal(s.receiptLinks, 0, `no /r/ link on ${where}`);
    };
    const openTicket = async (p, ticket) => {
      await p.goto('about:blank');
      await p.goto(`${base}/t/#${typeof ticket === 'string' ? ticket : b64url(ticket)}`, { waitUntil: 'networkidle' });
      await p.waitForSelector('[data-ticket-status]', { timeout: 10000 });
      if (await p.locator('[data-ticket-status="OK"]').count()) await p.waitForSelector('[data-book-row="FanDuel"], [data-source-status="source_failed"]', { timeout: 10000 });
    };

    // ---------------- Side by side on REAL data: both markets show "No current quote".
    const nbaReal = { v: 1, m: 'nba_threes', r: 3, p: REAL_NBA_PLAYER.player_id, g: REAL_NBA_PLAYER.game_id, n: REAL_NBA_PLAYER.player_name, o: 200, at: '2026-11-24T17:00:00.000Z' };
    const nflReal = { v: 1, m: 'anytime_td', p: REAL_NFL_ROW.player_id, g: REAL_NFL_ROW.game_id, n: REAL_NFL_ROW.player_display_name, o: 200, at: '2026-11-24T17:00:00.000Z' };
    const results = {};
    for (const [label, ticket] of [['nba', nbaReal], ['nfl', nflReal]]) {
      await openTicket(page, ticket);
      const s = await read();
      results[label] = s;
      assert.equal(s.ticketStatus, 'OK', `${label} ticket renders`);
      assert.equal(s.dk.noQuote, true, `${label}: DraftKings has no current quote`);
      assert.equal(s.fd.noQuote, true, `${label}: FanDuel has no current quote`);
      assert.equal(s.dk.diff, null);
      assert.equal(s.fairUnavailable, true);
      assert.match(s.all, /Minimum odds \+200/);
      assert.match(s.all, /USER-ENTERED · NOT VERIFIED/);
      clean(s, `real ${label} ticket`);
    }
    assert.match(results.nba.title, /· 3PM 3\+$/);
    assert.match(results.nba.all, /3PM 3\+ \(Over 2\.5\)/);
    assert.match(results.nba.all, /no model fair price for this rung/i);
    assert.match(results.nfl.title, /· Anytime TD$/);
    assert.match(results.nfl.all, /Anytime TD · Yes/);
    assert.doesNotMatch(results.nfl.all, /3PM/);

    // Real /check paste for NBA → unverified preview with honest empty state; never navigates.
    await page.goto(`${base}/check/`, { waitUntil: 'networkidle' });
    const submit = async (text) => {
      await page.evaluate(() => { document.getElementById('slipResult').innerHTML = ''; });
      await page.fill('#slipInput', text);
      await page.click('#slipSubmit');
      await page.waitForSelector('[data-slip-status]:not([data-slip-status="LOOKING_UP"])', { timeout: 10000 });
      return read();
    };
    let s = await submit(`${REAL_NBA_PLAYER.player_name} 3PM 3+ +200`);
    assert.equal(s.slipStatus, 'PREVIEW');
    assert.equal(s.dk.noQuote, true);
    assert.equal(s.fd.noQuote, true);
    assert.match(s.all, /USER-ENTERED ODDS/);
    assert.match(page.url(), /\/check\/$/);
    clean(s, '/check real NBA preview');

    // ---------------- Fixture comparison: both books + $20 diff, exact rung only.
    fx.on = true;
    fx.feed = { quotes: [
      shooter({ book: 'DraftKings', line: 2.5, over_odds: 150 }),
      shooter({ book: 'FanDuel', line: 2.5, over_odds: 170 }),
      shooter({ book: 'DraftKings', line: 3.5, over_odds: 320 }),
      shooter({ book: 'FanDuel', line: 3.5, over_odds: 300 }),
      shooter({ book: 'FanDuel', line: 2.0, over_odds: 130 }),            // not a rung
      shooter({ book: 'DraftKings', line: 2.5, over_odds: 400, observed_at: STALE }),
      shooter({ book: 'DraftKings', market: 'points', line: 2.5, over_odds: 450 }),
      shooter({ book: 'FanDuel', line: 2.5, over_odds: 999, player_id: 'odds-nba-someone-else' }),
      shooter({ book: 'FanDuel', line: 2.5, over_odds: 888, game_id: 'nba-20261125-NYK-MIA' }),
      { player_id: 'odds-nba-twin-a', player_name: 'Twin Name', team: 'LAL', opponent: 'DEN', game_id: 'nba-20261124-LAL-DEN', market: 'points', line: 20.5, over_odds: -110, under_odds: -110, book: 'Kalshi', observed_at: FRESH },
      { player_id: 'odds-nba-twin-b', player_name: 'Twin Name', team: 'PHX', opponent: 'UTA', game_id: 'nba-20261124-PHX-UTA', market: 'points', line: 18.5, over_odds: -110, under_odds: -110, book: 'Kalshi', observed_at: FRESH },
    ] };
    const three = { v: 1, m: 'nba_threes', r: 3, p: 'odds-nba-test-shooter', g: FX_GAME, n: 'Test Shooter', o: 160, at: '2026-11-24T17:00:00.000Z', take: 'Volume is back' };
    await openTicket(page, three);
    s = await read();
    assert.equal(s.ticketStatus, 'OK');
    assert.match(s.title, /Test Shooter · 3PM 3\+/);
    assert.match(s.all, /Volume is back/);
    assert.match(s.dk.text, /\+150/);
    assert.match(s.dk.text, /observed 2026-11-24 17:30 UTC/);
    assert.equal(s.dk.vsPosted, 'Worse than posted');
    assert.equal(s.dk.diff, '−$2.00');
    assert.match(s.fd.text, /\+170/);
    assert.equal(s.fd.vsPosted, 'Better than posted');
    assert.equal(s.fd.diff, '+$2.00');
    assert.equal(s.dk.vsFair, null);
    assert.equal(s.fairUnavailable, true);
    assert.doesNotMatch(s.all, /\+320|\+130|\+400|\+450|\+999|\+888/);
    clean(s, 'fixture 3+ ticket');

    // 4+ uses Over 3.5 only.
    await openTicket(page, { ...three, r: 4, o: 300, take: undefined });
    s = await read();
    assert.match(s.all, /3PM 4\+ \(Over 3\.5\)/);
    assert.equal(s.dk.vsPosted, 'Better than posted');
    assert.equal(s.dk.diff, '+$4.00');
    assert.equal(s.fd.vsPosted, 'Matches posted price');
    assert.equal(s.fd.diff, '$0.00');
    assert.doesNotMatch(s.all, /\+150|\+170/);

    // Watch stub, same local-only behavior.
    await openTicket(page, three);
    await page.click('[data-watch-btn]');
    const note = await page.locator('[data-watch-note]').innerText();
    assert.match(note, /Watching \+160 on this device only\. No alerts yet\./);
    assert.match(note, /FanDuel \+170/);
    assert.doesNotMatch(note, /DraftKings/);
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('propstein.watch.v1')));
    assert.equal(stored.length, 1);
    assert.equal(stored[0].market, 'nba_threes');
    assert.equal(stored[0].rung, 3);
    assert.equal(stored[0].cutoff, 160);
    await page.click('[data-watch-btn]');

    // Stale / wrong-rung only, and a source failure → "No current quote".
    fx.feed = { quotes: [shooter({ book: 'DraftKings', line: 2.5, over_odds: 400, observed_at: STALE }), shooter({ book: 'FanDuel', line: 2.0, over_odds: 130 })] };
    await openTicket(page, three);
    s = await read();
    assert.equal(s.dk.noQuote, true);
    assert.equal(s.fd.noQuote, true);
    assert.match(s.all, /Minimum odds \+160/);
    fx.status = 500;
    await openTicket(page, three);
    s = await read();
    assert.equal(s.ticketStatus, 'OK');
    assert.equal(s.dk.noQuote, true);
    assert.equal(s.sourceStatus, 'source_failed');
    fx.status = 200;

    // Unknown / malformed NBA tickets.
    for (const bad of [{ ...three, r: 5 }, { ...three, r: undefined }, { ...three, g: 'nba-bad' }, { ...three, m: 'threes' }, { ...three, extra: 1 }]) {
      await openTicket(page, bad);
      assert.equal((await read()).ticketStatus, 'NOT_FOUND', JSON.stringify(bad).slice(0, 60));
    }

    // /check with fixtures: rejects, ambiguity, valid preview → ticket for a recipient.
    fx.feed = { quotes: [shooter({ book: 'DraftKings', line: 2.5, over_odds: 150 }), shooter({ book: 'FanDuel', line: 2.5, over_odds: 170 }),
      { player_id: 'odds-nba-twin-a', player_name: 'Twin Name', team: 'LAL', opponent: 'DEN', game_id: 'nba-20261124-LAL-DEN', market: 'points', line: 20.5, over_odds: -110, under_odds: -110, book: 'Kalshi', observed_at: FRESH },
      { player_id: 'odds-nba-twin-b', player_name: 'Twin Name', team: 'PHX', opponent: 'UTA', game_id: 'nba-20261124-PHX-UTA', market: 'points', line: 18.5, over_odds: -110, under_odds: -110, book: 'Kalshi', observed_at: FRESH }] };
    await page.goto(`${base}/check/`, { waitUntil: 'networkidle' });
    assert.match(await page.locator('[data-slip-example]').innerText(), /Jalen Brunson 3PM 3\+ \+150/);
    for (const [input, status, re] of [
      ['Test Shooter 3PM 5+ +400', 'NEEDS_DETAIL', /Only 3\+ and 4\+/],
      ['Test Shooter 3PM under 2.5 +100', 'NEEDS_DETAIL', /Only 3\+ and 4\+ \(Over\)/],
      ['Test Shooter 3PM +150', 'NEEDS_DETAIL', /Missing the rung/],
      ['Test Shooter 3PM 3+', 'NEEDS_DETAIL', /Missing odds/],
      ['Test Shooter 3PM 3+ 150', 'NEEDS_DETAIL', /explicit sign/],
      ['Test Shooter 3PM 3+ +150, Twin Name 3PM 3+ +200', 'NEEDS_DETAIL', /Multi-leg/],
      ['Twin Name 3PM 3+ +200', 'AMBIGUOUS', /Twin Name \(LAL vs DEN\).*Twin Name \(PHX vs UTA\)/],
      ['Nobody Realname 3PM 3+ +200', 'NO_MATCH', /not on the current NBA slate/],
    ]) {
      s = await submit(input);
      assert.equal(s.slipStatus, status, input);
      assert.match(s.all, re, input);
      assert.match(page.url(), /\/check\/$/);
      clean(s, `/check "${input}"`);
    }
    s = await submit('Test Shooter 3PM 3+');
    assert.match(s.all, /Read: player “Test Shooter” · NBA 3PM · rung 3\+/);
    assert.match(s.all, /Missing odds/);
    s = await submit('Test Shooter 3PM over 2.5 +160');
    assert.equal(s.slipStatus, 'PREVIEW');
    assert.equal(s.dk.vsPosted, 'Worse than posted');
    assert.equal(s.fd.vsPosted, 'Better than posted');
    assert.equal(s.fd.diff, '+$2.00');
    clean(s, '/check fixture NBA preview');
    await page.click('#makeTicket');
    const href = await page.getAttribute('[data-ticket-link]', 'href');
    assert.match(href, /^\/t\/#[A-Za-z0-9_-]+$/);
    const recipient = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
    const rp = await recipient.newPage();
    await rp.clock.setFixedTime(new Date(NOW));
    await openTicket(rp, href.replace('/t/#', ''));
    const rtext = await rp.locator('body').innerText();
    assert.match(rtext, /Test Shooter · 3PM 3\+ \(Over 2\.5\)/);
    assert.match(rtext, /Created 2026-11-24 18:00 UTC/);
    assert.match(rtext, /−\$2\.00 profit on \$20 vs posted/);
    assert.doesNotMatch(rtext, FORBIDDEN_RE);
    await recipient.close();

    assert.deepEqual(pageErrors, []);
  } finally {
    await browser.close();
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
    server.close();
  }
});
