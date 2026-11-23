// "Would you still tail?" receipt view + slip-check stub, 390px dark mode.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';

const VIEWER_DIR = path.resolve('viewer');
const MIME = {
  '.html': 'text/html',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

// Committed W7 snapshot with a logged DraftKings Over 34.5 (-110), fair 43.5.
const DOUGLAS_HASH = '3217826645f7b79960b9f6da7e5f51bf14e4477ebb4257bdd93fd7134dc256b9';
// Committed W7 snapshot with no logged price, fair 46.5.
const EVANS_HASH = '9f123b877d50';

// Anything that reads as sizing, edge, or a recommendation. None of it may render.
const FORBIDDEN_RE = /\bkelly\b|\bedge\b|\bEV\b|expected return|\bstake\b|\d+(\.\d+)?\s*%|you should bet|bet placed|we placed|tail this|hammer/i;

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    '/opt/pw-browsers/chromium',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p) && fs.statSync(p).isFile()) || null;
}

function feedQuote(overrides) {
  return {
    quote_id: `fixture_${overrides.line}_${overrides.game_id}`,
    book: 'Polymarket',
    player_id: '00-0038621',
    player_name: 'DeMario Douglas',
    market: 'receiving_yards',
    game_id: '2026_07_NE_CHI',
    over_odds: -115,
    under_odds: -105,
    observed_at: '2026-10-25T16:00:00+00:00',
    verification_status: 'OBSERVED',
    // Fields that must never render.
    evaluation: { prob_edge: 0.2, expected_return: 0.3, actionable_stake: 0.05, best_side: 'OVER' },
    ...overrides,
  };
}

test('Tail check and slip stub at 390px dark mode', async (t) => {
  const executablePath = findChrome();
  if (!executablePath) {
    t.skip('No local Chrome/Chromium executable available in this CI environment');
    return;
  }

  // Test-only feed. The real slate index stays untouched unless a test sets one.
  let fixtureFeed = null;
  const server = http.createServer((req, res) => {
    const urlPath = (req.url || '/').split('?')[0];
    if (fixtureFeed && urlPath === '/feeds/slate-index.json') {
      const idx = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'feeds/slate-index.json'), 'utf8'));
      idx.sports.nfl.slates['2026_w7'].odds = 'tail_fixture_feed.json';
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(idx));
      return;
    }
    if (fixtureFeed && urlPath === '/tail_fixture_feed.json') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(fixtureFeed));
      return;
    }
    let rel = urlPath;
    if (urlPath.startsWith('/r/') && !/\.(js|css)$/.test(urlPath)) rel = '/r/index.html';
    else if (urlPath === '/check' || urlPath === '/check/') rel = '/check/index.html';
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
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));

    const readTail = async () => page.evaluate(() => ({
      root: document.querySelector('#receiptRoot, #checkRoot')?.innerText || '',
      status: document.querySelector('[data-tail-status]')?.getAttribute('data-tail-status') || null,
      tail: document.querySelector('[data-tail-check="1"]')?.innerText || '',
      fits: document.documentElement.scrollWidth <= window.innerWidth + 2,
    }));
    const openReceipt = async (hashAndQuery) => {
      await page.goto(`${base}/r/${hashAndQuery}`, { waitUntil: 'networkidle' });
      await page.waitForSelector('[data-receipt-status]', { timeout: 10000 });
    };

    // 1. Known hash, real committed files only: no current quote is said plainly.
    await openReceipt(EVANS_HASH);
    await page.waitForSelector('[data-tail-check="1"]', { timeout: 10000 });
    let v = await readTail();
    assert.equal(await page.getAttribute('[data-receipt-status]', 'data-receipt-status'), 'VERIFIED');
    assert.equal(v.status, 'no current quote');
    assert.match(v.tail, /Would you still tail\?/);
    assert.match(v.tail, /Mike Evans/);
    assert.match(v.tail, /No price was logged in this snapshot/);
    assert.match(v.tail, /No current quote in PropStein feeds for this game/);
    assert.match(v.tail, /46\.5/);
    assert.doesNotMatch(v.root, FORBIDDEN_RE);
    assert.ok(v.fits, 'no horizontal overflow at 390px');

    // 2. Known hash with a logged price, real files: original shown, no current quote.
    await openReceipt(DOUGLAS_HASH);
    await page.waitForSelector('[data-tail-check="1"]');
    v = await readTail();
    assert.equal(v.status, 'no current quote');
    assert.match(v.tail, /Logged price/);
    assert.match(v.tail, /OBSERVED · COMMITTED SNAPSHOT/);
    assert.match(v.tail, /Over 34\.5/);
    assert.match(v.tail, /-110/);
    assert.match(v.tail, /DraftKings/);
    assert.match(v.tail, /2026-10-20 06:11 UTC/);
    assert.match(v.tail, /43\.5/);
    assert.doesNotMatch(v.root, FORBIDDEN_RE);

    // 3. Same receipt with an observed current quote (test feed): original vs current.
    //    A quote for the same player in another game must be ignored.
    fixtureFeed = { quotes: [
      feedQuote({ line: 38.5 }),
      feedQuote({ line: 40.5, observed_at: '2026-10-25T17:00:00+00:00' }),
      feedQuote({ line: 20.5, game_id: '2026_11_NE_XXX' }),
    ] };
    await openReceipt(DOUGLAS_HASH);
    await page.waitForSelector('[data-tail-check="1"]');
    v = await readTail();
    assert.equal(v.status, 'still above fair');
    assert.match(v.tail, /Current best quote/);
    assert.match(v.tail, /OBSERVED · PROPSTEIN FEED/);
    assert.match(v.tail, /Over 38\.5/);
    assert.match(v.tail, /-115/);
    assert.match(v.tail, /2026-10-25 16:00 UTC/);
    assert.doesNotMatch(v.tail, /20\.5/);
    assert.doesNotMatch(v.root, FORBIDDEN_RE);
    assert.ok(v.fits);

    fixtureFeed = { quotes: [feedQuote({ line: 45.5 })] };
    await openReceipt(DOUGLAS_HASH);
    await page.waitForSelector('[data-tail-check="1"]');
    v = await readTail();
    assert.equal(v.status, 'price gone');
    assert.match(v.tail, /Over 45\.5/);
    assert.doesNotMatch(v.root, FORBIDDEN_RE);
    fixtureFeed = null;

    // 4. Unknown hash still fails closed, with no tail check.
    await openReceipt('deadbeef');
    assert.equal(await page.getAttribute('[data-receipt-status]', 'data-receipt-status'), 'NOT_FOUND');
    assert.equal(await page.locator('[data-tail-check="1"]').count(), 0);
    assert.match(await page.locator('#receiptRoot').innerText(), /Receipt not found/i);

    // 5. Slip stub: garbage is rejected and nothing routes.
    await page.goto(`${base}/check/`, { waitUntil: 'networkidle' });
    for (const garbage of ['asdf lol', 'Mike Evans 46.5', 'Mike Evans passing yards over 220.5 -110']) {
      await page.fill('#slipInput', garbage);
      await page.click('#slipSubmit');
      await page.waitForSelector('[data-slip-status="PARSE_FAILED"]', { timeout: 5000 });
      assert.match(page.url(), /\/check\/$/);
    }
    await page.fill('#slipInput', 'Nobody Realname receiving yards over 40.5 -110');
    await page.click('#slipSubmit');
    await page.waitForSelector('[data-slip-status="NO_SNAPSHOT"]', { timeout: 10000 });
    assert.match(page.url(), /\/check\/$/);
    v = await readTail();
    assert.doesNotMatch(v.root, FORBIDDEN_RE);
    assert.ok(v.fits);

    // 6. Clean slip routes to the committed receipt's tail check, labeled user-reported.
    await page.fill('#slipInput', 'DeMario Douglas receiving yards over 36.5 -115');
    await Promise.all([
      page.waitForURL(/\/r\/[0-9a-f]{64}\?slip=/, { timeout: 10000 }),
      page.click('#slipSubmit'),
    ]);
    await page.waitForSelector('[data-tail-check="1"]', { timeout: 10000 });
    v = await readTail();
    const routedHash = new URL(page.url()).pathname.split('/').filter(Boolean).pop();
    const committed = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'snapshots_2026_w7.json'), 'utf8'));
    const row = committed.find((r) => r.content_hash === routedHash);
    assert.ok(row, 'slip routes to a committed snapshot hash');
    assert.equal(row.player_name, 'DeMario Douglas');
    assert.equal(row.market, 'receiving_yards');
    assert.equal(await page.getAttribute('[data-receipt-status]', 'data-receipt-status'), 'VERIFIED');
    assert.match(v.tail, /Your slip/);
    assert.match(v.tail, /USER-REPORTED · NOT VERIFIED/);
    assert.match(v.tail, /Over 36\.5/);
    assert.equal(v.status, 'no current quote');
    assert.doesNotMatch(v.root, FORBIDDEN_RE);
    assert.ok(v.fits);

    // 7. A slip that does not match the receipt is ignored, not merged.
    await openReceipt(`${EVANS_HASH}?slip=${encodeURIComponent('DeMario Douglas receiving yards over 36.5 -115')}`);
    await page.waitForSelector('[data-tail-check="1"]');
    v = await readTail();
    assert.match(v.root, /Slip ignored/);
    assert.doesNotMatch(v.tail, /USER-REPORTED/);

    assert.deepEqual(pageErrors, []);
  } finally {
    await browser.close();
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
    server.close();
  }
});
