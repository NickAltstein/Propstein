// Tail check on /r/<hash>, /check slip preview, and /t/ single-leg ticket.
// 390px dark mode, fixed clock, local fixtures only. A passing fixture is
// not evidence of a live DK/FD feed.
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
const STALE = '2026-11-24T08:00:00Z'; // 10h old; existing gate is 6h (odds_stale_max_age_seconds)

// Committed W7 snapshot with a logged DraftKings Over 34.5 (-110), fair line 43.5.
const DOUGLAS_HASH = '3217826645f7b79960b9f6da7e5f51bf14e4477ebb4257bdd93fd7134dc256b9';

const FORBIDDEN_RE = /\bkelly\b|\bedge\b|\bEV\b|expected return|\d+(\.\d+)?\s*%|you should bet|bet (?:was )?placed|we placed|take it|\bpass\b|still playable|bet now|place bet|tail this|hammer/i;

const FX_SLATE = [
  { player_id: '00-0099001', player_display_name: 'Test Receiver', team: 'BUF', opponent_team: 'KC', game_id: '2026_12_KC_BUF', position: 'WR', fair_odds_anytime: 205, td_gate_status: 'RESEARCH_ONLY_UNVERIFIED' },
  { player_id: '00-0099002', player_display_name: 'Mike Williams', team: 'NYJ', opponent_team: 'MIA', game_id: '2026_12_NYJ_MIA', position: 'WR' },
  { player_id: '00-0099003', player_display_name: 'Mike Williams', team: 'PIT', opponent_team: 'CLE', game_id: '2026_12_PIT_CLE', position: 'WR' },
  { player_id: '00-0099004', player_display_name: 'Nofair Guy', team: 'KC', opponent_team: 'BUF', game_id: '2026_12_KC_BUF', position: 'TE' },
];

const tdQuote = (o) => ({ player_id: '00-0099001', game_id: '2026_12_KC_BUF', market: 'anytime_td', selection: 'YES', observed_at: FRESH, ...o });

function b64url(obj) {
  return Buffer.from(JSON.stringify(obj), 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function findChrome() {
  const candidates = [process.env.CHROME_PATH, '/opt/pw-browsers/chromium', '/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium'].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p) && fs.statSync(p).isFile()) || null;
}

test('Tail check, slip preview and ticket at 390px dark mode', async (t) => {
  const executablePath = findChrome();
  if (!executablePath) {
    t.skip('No local Chrome/Chromium executable available in this CI environment');
    return;
  }

  // Feed health is a fixture, never the repo's live viewer/feeds/feed_status.json:
  // reading the live file made this test depend on whatever the feed was doing.
  //   object -> served as JSON; string -> served raw (malformed); null -> 404 (missing).
  const healthyStatus = () => ({ schema_version: 'feed-status.v1', generated_at: FRESH, sports: {
    nfl: { level: 'ok', fail_closed: false }, nba: { level: 'ok', fail_closed: false } } });
  const fx = { tdFeed: { quotes: [] }, tdFeedStatus: 200, receiptFeed: null, receiptFeedStatus: 200, feedStatus: healthyStatus() };
  const server = http.createServer((req, res) => {
    const urlPath = (req.url || '/').split('?')[0];
    const json = (status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
    if (urlPath === '/feeds/slate-index.json') {
      const idx = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'feeds/slate-index.json'), 'utf8'));
      idx.sports.nfl.default_slate = 'fx_w12';
      idx.sports.nfl.slates.fx_w12 = { season: 2026, week: 12, slate: 'fx_slate_w12.json', games: 'games_2026_w11.json', odds: 'fx_td_odds.json', snapshots: null, historical: false };
      // Live W11 feed is excluded so only fixtures drive "current".
      idx.sports.nfl.slates['2026_w11'].odds = null;
      if (fx.receiptFeed !== null) idx.sports.nfl.slates['2026_w7'].odds = 'fx_receipt_odds.json';
      return json(200, idx);
    }
    if (urlPath === '/feeds/feed_status.json') {
      if (fx.feedStatus === null) { res.writeHead(404); res.end('not found'); return; }
      if (typeof fx.feedStatus === 'string') { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(fx.feedStatus); return; }
      return json(200, fx.feedStatus);
    }
    if (urlPath === '/fx_slate_w12.json') return json(200, FX_SLATE);
    if (urlPath === '/fx_td_odds.json') return fx.tdFeedStatus === 200 ? json(200, fx.tdFeed) : json(fx.tdFeedStatus, { error: 'down' });
    if (urlPath === '/fx_receipt_odds.json') return fx.receiptFeedStatus === 200 ? json(200, fx.receiptFeed) : json(fx.receiptFeedStatus, { error: 'down' });
    let rel = urlPath;
    if (urlPath.startsWith('/r/') && !/\.(js|css)$/.test(urlPath)) rel = '/r/index.html';
    else if (urlPath === '/check/' || urlPath === '/check') rel = '/check/index.html';
    else if (urlPath === '/t/') rel = '/t/index.html';
    else if (urlPath === '/') rel = '/index.html';
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

    const snapshot = () => page.evaluate(() => {
      const text = (sel) => document.querySelector(sel)?.innerText || '';
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
      const cur = document.querySelector('[data-current-row]');
      return {
        all: document.body.innerText,
        receiptStatus: document.querySelector('[data-receipt-status]')?.getAttribute('data-receipt-status') || null,
        receiptBody: document.querySelector('#receiptBody')?.innerHTML || '',
        tail: text('[data-tail-check="1"]'),
        current: cur ? { text: cur.innerText, noQuote: Boolean(cur.querySelector('[data-no-current-quote]')), vsPosted: cur.querySelector('[data-vs-posted]')?.getAttribute('data-vs-posted') || null, vsFair: cur.querySelector('[data-vs-fair]')?.getAttribute('data-vs-fair') || null } : null,
        dk: bookRow('DraftKings'),
        fd: bookRow('FanDuel'),
        fairUnavailable: Boolean(document.querySelector('[data-fair-unavailable]')),
        sourceStatus: document.querySelector('[data-source-status]')?.getAttribute('data-source-status') || null,
        sourceNote: document.querySelector('[data-source-status]')?.innerText || '',
        ticketStatus: document.querySelector('[data-ticket-status]')?.getAttribute('data-ticket-status') || null,
        slipStatus: document.querySelector('[data-slip-status]')?.getAttribute('data-slip-status') || null,
        shareButtons: document.querySelectorAll('[data-share-receipt]').length,
        receiptLinks: [...document.querySelectorAll('a[href]')].filter((a) => /\/r\/[0-9a-f]/.test(a.getAttribute('href'))).length,
        fits: document.documentElement.scrollWidth <= window.innerWidth + 2,
      };
    });
    const clean = (s, where) => {
      assert.doesNotMatch(s.all, FORBIDDEN_RE, `forbidden copy on ${where}`);
      assert.ok(s.fits, `no horizontal overflow on ${where}`);
    };

    // ------------------------------------------------------------ /r/<hash>
    const openReceipt = async () => {
      await page.goto(`${base}/r/${DOUGLAS_HASH}`, { waitUntil: 'networkidle' });
      await page.waitForSelector('[data-receipt-status="VERIFIED"]', { timeout: 10000 });
      await page.waitForSelector('[data-tail-check="1"], [data-tail-status]', { timeout: 10000 });
      return snapshot();
    };
    const receiptQuote = (o) => ({ player_id: '00-0038621', game_id: '2026_07_NE_CHI', market: 'receiving_yards', line: 34.5, over_odds: -120, under_odds: -100, book: 'Polymarket', observed_at: FRESH, ...o });

    // No source for W7 (real index): honest "No current quote".
    let s = await openReceipt();
    const baselineReceipt = s.receiptBody;
    assert.ok(baselineReceipt.includes(DOUGLAS_HASH));
    assert.equal(s.current.noQuote, true);
    assert.match(s.tail, /Posted price/);
    assert.match(s.tail, /OBSERVED · COMMITTED SNAPSHOT/);
    assert.match(s.tail, /Over 34\.5/);
    assert.match(s.tail, /-110/);
    assert.match(s.tail, /2026-10-20 06:11 UTC/);
    assert.match(s.tail, /not part of the verified receipt/i);
    assert.equal(s.sourceStatus, 'no_source');
    clean(s, '/r no source');

    // Fresh exact match: compared with posted; fair is a line → unavailable, historical fair labeled.
    fx.receiptFeed = { quotes: [receiptQuote({})] };
    s = await openReceipt();
    assert.equal(s.receiptBody, baselineReceipt, 'receipt payload unchanged by live data');
    assert.equal(s.current.noQuote, false);
    assert.match(s.current.text, /-120/);
    assert.match(s.current.text, /Polymarket/);
    assert.match(s.current.text, /observed 2026-11-24 17:30 UTC/);
    assert.equal(s.current.vsPosted, 'Worse than posted');
    assert.equal(s.current.vsFair, null);
    assert.equal(s.fairUnavailable, true);
    assert.match(s.tail, /Fair when logged: 43\.5/);
    clean(s, '/r fresh match');

    // Feed health (P0-1): only an explicitly healthy level serves quotes. A fresh,
    // exact-match quote stays hidden when the feed is suppressed (an intended pause,
    // explained as such) or when the status is unknown, missing or malformed (error).
    const nflStatus = (row) => ({ ...healthyStatus(), sports: { ...healthyStatus().sports, nfl: row } });
    for (const [label, status, want, note] of [
      ['suppressed: slate blocked', nflStatus({ level: 'suppressed', fail_closed: false, slate_state: { state: 'SLATE_BLOCKED' } }), 'paused', /Quotes paused: slate blocked\./],
      ['suppressed: between weeks', nflStatus({ level: 'suppressed', fail_closed: false, slate_state: { state: 'NO_CURRENT_SLATE' } }), 'paused', /Quotes paused: between weeks\./],
      ['suppressed: no game in window', nflStatus({ level: 'suppressed', fail_closed: false }), 'paused', /Quotes paused: no game in the current window\./],
      ['unknown level', nflStatus({ level: 'mystery', fail_closed: false }), 'fail_closed', /fail-closed/],
      ['critical', nflStatus({ level: 'critical', fail_closed: true }), 'fail_closed', /fail-closed/],
      ['missing status', null, 'fail_closed', /fail-closed/],
      ['malformed status', '{"sports": ', 'fail_closed', /fail-closed/],
    ]) {
      fx.feedStatus = status;
      s = await openReceipt();
      assert.equal(s.receiptBody, baselineReceipt, `${label}: receipt unchanged`);
      assert.equal(s.current.noQuote, true, `${label}: fresh quote must not be served`);
      assert.equal(s.sourceStatus, want, `${label}: source status`);
      assert.match(s.sourceNote, note, `${label}: note`);
      if (want === 'paused') assert.doesNotMatch(s.sourceNote, /fail-closed/, `${label}: a pause is not reported as an error`);
      else assert.doesNotMatch(s.sourceNote, /Quotes paused/, `${label}: an error is not reported as a pause`);
      clean(s, `/r health ${label}`);
    }
    fx.feedStatus = healthyStatus();

    // Stale quote, wrong rung, wrong side-of-game, source failure → receipt intact + "No current quote".
    for (const [label, feed, status] of [
      ['stale', { quotes: [receiptQuote({ observed_at: STALE })] }, 200],
      ['wrong rung', { quotes: [receiptQuote({ line: 35.5 })] }, 200],
      ['other game', { quotes: [receiptQuote({ game_id: '2026_12_NE_XXX' })] }, 200],
      ['source failure', { quotes: [] }, 500],
    ]) {
      fx.receiptFeed = feed;
      fx.receiptFeedStatus = status;
      s = await openReceipt();
      assert.equal(s.receiptStatus, 'VERIFIED', `${label}: receipt still verified`);
      assert.equal(s.receiptBody, baselineReceipt, `${label}: receipt unchanged`);
      assert.equal(s.current.noQuote, true, `${label}: No current quote`);
      assert.equal(s.current.vsPosted, null);
      clean(s, `/r ${label}`);
    }
    assert.equal(s.sourceStatus, 'source_failed');
    fx.receiptFeed = null;
    fx.receiptFeedStatus = 200;

    // Unknown hash fails closed (static host serves the page; the API route returns HTTP 404).
    await page.goto(`${base}/r/deadbeef`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-receipt-status="NOT_FOUND"]', { timeout: 10000 });
    assert.equal(await page.locator('[data-tail-check="1"]').count(), 0);

    // ------------------------------------------------------------- /t/ ticket
    const ticket = { v: 1, m: 'anytime_td', p: '00-0099001', g: '2026_12_KC_BUF', n: 'Test Receiver', o: 240, at: '2026-11-24T17:00:00.000Z', take: 'Red zone role is back' };
    const openTicket = async (frag) => {
      await page.goto('about:blank');
      await page.goto(`${base}/t/#${frag}`, { waitUntil: 'networkidle' });
      await page.waitForSelector('[data-ticket-status]', { timeout: 10000 });
      if (await page.locator('[data-ticket-status="OK"]').count()) await page.waitForSelector('[data-tail-check="1"], [data-source-status]', { timeout: 10000 });
      return snapshot();
    };

    // Exact match at both books: +240 posted, DK +220, FD +250, fair +205.
    fx.tdFeed = { quotes: [
      tdQuote({ book: 'DraftKings', odds: 220 }),
      tdQuote({ book: 'FanDuel', odds: 250 }),
      tdQuote({ book: 'DraftKings', odds: 400, observed_at: STALE }),
      tdQuote({ book: 'DraftKings', odds: 900, market: 'first_td' }),
      tdQuote({ book: 'FanDuel', odds: 700, market: 'two_plus_td' }),
      tdQuote({ book: 'FanDuel', odds: 120, selection: 'NO' }),
      tdQuote({ book: 'DraftKings', odds: 600, player_id: '00-0099004' }),
    ] };
    s = await openTicket(b64url(ticket));
    assert.equal(s.ticketStatus, 'OK');
    assert.match(s.all, /Test Receiver · Anytime TD · Yes/);
    assert.match(s.all, /Minimum odds \+240/);
    assert.match(s.all, /Created 2026-11-24 17:00 UTC/);
    assert.match(s.all, /Red zone role is back/);
    assert.match(s.all, /USER-ENTERED · NOT VERIFIED/);
    assert.match(s.dk.text, /\+220/);
    assert.match(s.dk.text, /observed 2026-11-24 17:30 UTC/);
    assert.equal(s.dk.vsPosted, 'Worse than posted');
    assert.equal(s.dk.vsFair, 'Above fair');
    assert.equal(s.dk.diff, '−$4.00');
    assert.match(s.fd.text, /\+250/);
    assert.equal(s.fd.vsPosted, 'Better than posted');
    assert.equal(s.fd.vsFair, 'Above fair');
    assert.equal(s.fd.diff, '+$2.00');
    assert.match(s.tail, /\+205/);
    assert.doesNotMatch(s.tail, /\+400|\+900|\+700|\+600/);
    assert.equal(s.shareButtons, 0);
    assert.equal(s.receiptLinks, 0);
    clean(s, '/t exact match');

    // Watch stub: local only, survives reload, can be removed.
    await page.click('[data-watch-btn]');
    let note = await page.locator('[data-watch-note]').innerText();
    assert.match(note, /Watching \+240 on this device only\. No alerts yet\./);
    assert.match(note, /FanDuel \+250/);
    assert.doesNotMatch(note, /DraftKings \+220/);
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('[data-watch-btn]');
    assert.equal(await page.getAttribute('[data-watch-btn]', 'aria-pressed'), 'true');
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('propstein.watch.v1')));
    assert.equal(stored.length, 1);
    assert.equal(stored[0].cutoff, 240);
    await page.click('[data-watch-btn]');
    assert.equal(await page.getAttribute('[data-watch-btn]', 'aria-pressed'), 'false');

    // Missing quote: only stale / wrong-rung quotes → both books "No current quote".
    fx.tdFeed = { quotes: [tdQuote({ book: 'DraftKings', odds: 300, observed_at: STALE }), tdQuote({ book: 'FanDuel', odds: 900, market: 'first_td' })] };
    s = await openTicket(b64url(ticket));
    assert.equal(s.dk.noQuote, true);
    assert.equal(s.fd.noQuote, true);
    assert.match(s.dk.text, /No current quote/);
    assert.match(s.all, /Minimum odds \+240/);
    assert.equal(s.dk.diff, null);
    clean(s, '/t no quote');

    // Quote source failure: ticket still shown, "No current quote".
    fx.tdFeedStatus = 500;
    s = await openTicket(b64url(ticket));
    assert.equal(s.ticketStatus, 'OK');
    assert.equal(s.dk.noQuote, true);
    assert.equal(s.fd.noQuote, true);
    assert.equal(s.sourceStatus, 'source_failed');
    fx.tdFeedStatus = 200;

    // Fair unavailable with a valid current quote; even money normalizes (+100 == -100).
    fx.tdFeed = { quotes: [tdQuote({ player_id: '00-0099004', book: 'DraftKings', odds: -100 })] };
    s = await openTicket(b64url({ ...ticket, p: '00-0099004', n: 'Nofair Guy', o: 100, take: undefined }));
    assert.equal(s.dk.vsPosted, 'Matches posted price');
    assert.equal(s.dk.diff, '$0.00');
    assert.equal(s.dk.vsFair, null);
    assert.equal(s.fairUnavailable, true);
    assert.equal(s.fd.noQuote, true);
    clean(s, '/t no fair');

    // Negative odds normalization: posted -120, DK -110 is better.
    fx.tdFeed = { quotes: [tdQuote({ book: 'DraftKings', odds: -110 })] };
    s = await openTicket(b64url({ ...ticket, o: -120 }));
    assert.equal(s.dk.vsPosted, 'Better than posted');
    assert.equal(s.dk.diff, '+$1.52');

    // Unknown / malformed tickets.
    for (const frag of ['', 'garbage!!', b64url({ ...ticket, m: 'first_td' }), b64url({ ...ticket, o: 50 }), b64url({ ...ticket, extra: 1 })]) {
      s = await openTicket(frag);
      assert.equal(s.ticketStatus, 'NOT_FOUND', `ticket "${frag.slice(0, 12)}" must not render`);
      assert.equal(await page.locator('[data-tail-check="1"]').count(), 0);
    }
    const pathTicket = await page.goto(`${base}/t/not-a-ticket`);
    assert.equal(pathTicket.status(), 404);

    // ---------------------------------------------------------------- /check
    fx.tdFeed = { quotes: [tdQuote({ book: 'DraftKings', odds: 220 }), tdQuote({ book: 'FanDuel', odds: 250 })] };
    await page.goto(`${base}/check/`, { waitUntil: 'networkidle' });
    assert.match(await page.locator('[data-slip-example]').innerText(), /Ja'Marr Chase anytime TD \+150/);
    const submit = async (text) => {
      await page.evaluate(() => { document.getElementById('slipResult').innerHTML = ''; });
      await page.fill('#slipInput', text);
      await page.click('#slipSubmit');
      await page.waitForSelector('[data-slip-status]:not([data-slip-status="LOOKING_UP"])', { timeout: 10000 });
      return snapshot();
    };
    for (const [input, status, re] of [
      ['asdf lol', 'NEEDS_DETAIL', /Only NFL anytime TD/],
      ['Test Receiver anytime TD', 'NEEDS_DETAIL', /Missing odds/],
      ['Test Receiver anytime TD 240', 'NEEDS_DETAIL', /explicit sign/],
      ['Test Receiver anytime TD +240, Nofair Guy anytime TD +300', 'NEEDS_DETAIL', /Multi-leg/],
      ['Test Receiver receiving yards over 40.5 -110', 'NEEDS_DETAIL', /Only NFL anytime TD/],
      ['Mike Williams anytime TD +300', 'AMBIGUOUS', /Mike Williams \(NYJ vs MIA\).*Mike Williams \(PIT vs CLE\)/],
      ['Nobody Realname anytime TD +300', 'NO_MATCH', /not on the current NFL slate/],
    ]) {
      s = await submit(input);
      assert.equal(s.slipStatus, status, input);
      assert.match(s.all, re, input);
      assert.match(page.url(), /\/check\/$/, 'never navigates');
      assert.equal(s.shareButtons, 0);
      assert.equal(s.receiptLinks, 0);
      clean(s, `/check "${input}"`);
    }
    // Incomplete input shows what was read and what is missing.
    s = await submit('Test Receiver anytime TD');
    assert.match(s.all, /Read: player “Test Receiver” · Anytime TD/);
    assert.match(s.all, /Missing odds/);

    // Valid paste → unverified preview with user-entered odds, no receipt, no navigation.
    s = await submit('Mike Williams PIT anytime TD +240');
    assert.equal(s.slipStatus, 'PREVIEW');
    assert.match(s.all, /Unverified preview · user-entered odds · not a receipt/i);
    assert.match(s.all, /USER-ENTERED ODDS/);
    assert.match(s.all, /not proof of a wager/);
    assert.equal(s.dk.noQuote, true, 'quotes for another player never match');
    s = await submit('Test Receiver anytime TD +240');
    assert.equal(s.slipStatus, 'PREVIEW');
    assert.equal(s.dk.vsPosted, 'Worse than posted');
    assert.equal(s.dk.vsFair, 'Above fair');
    assert.equal(s.fd.vsPosted, 'Better than posted');
    assert.equal(s.shareButtons, 0);
    assert.equal(s.receiptLinks, 0);
    assert.match(page.url(), /\/check\/$/);
    clean(s, '/check preview');

    // Ticket link from the preview opens the same comparison for a recipient.
    await page.fill('#takeInput', 'Goal-line usage up');
    await page.click('#makeTicket');
    const href = await page.getAttribute('[data-ticket-link]', 'href');
    assert.match(href, /^\/t\/#[A-Za-z0-9_-]+$/);
    const recipient = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
    const rp = await recipient.newPage();
    await rp.clock.setFixedTime(new Date(NOW));
    await rp.goto(`${base}${href}`, { waitUntil: 'networkidle' });
    await rp.waitForSelector('[data-book-row="FanDuel"]', { timeout: 10000 });
    const rtext = await rp.locator('body').innerText();
    assert.match(rtext, /Test Receiver · Anytime TD · Yes/);
    assert.match(rtext, /Created 2026-11-24 18:00 UTC/);
    assert.match(rtext, /Goal-line usage up/);
    assert.match(rtext, /−\$4\.00 profit on \$20 vs posted/);
    assert.doesNotMatch(rtext, FORBIDDEN_RE);
    await recipient.close();

    assert.deepEqual(pageErrors, []);
  } finally {
    await browser.close();
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
    server.close();
  }
});
