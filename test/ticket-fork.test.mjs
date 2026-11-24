// "Recipient becomes sender": fork an existing /t/ ticket with a new cutoff.
// Real committed data only (no fixtures), 390px dark mode, fixed clock.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';

const VIEWER_DIR = path.resolve('viewer');
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml' };
const NOW = '2026-11-24T18:00:00Z';
const FORBIDDEN_RE = /\bkelly\b|\bedge\b|\bEV\b|expected return|\d+(\.\d+)?\s*%|you should bet|bet (?:was )?placed|we placed|take it|\bpass\b|still playable|bet now|place bet|tail this|hammer|alert sent/i;

const NFL_ROW = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'slate_2026_w11.json'), 'utf8')).find((r) => r && r.player_id && r.game_id);
const NBA_Q = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'nba_odds_2026-11-23.json'), 'utf8')).quotes.find((q) => q && q.player_id && q.game_id && q._fixture !== true);

function b64url(obj) {
  return Buffer.from(JSON.stringify(obj), 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function unb64url(s) {
  return JSON.parse(Buffer.from(s.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8'));
}
function findChrome() {
  const candidates = [process.env.CHROME_PATH, '/opt/pw-browsers/chromium', '/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium'].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p) && fs.statSync(p).isFile()) || null;
}

test('Recipient forks a ticket with a new cutoff; original stays intact; honest empty state on real data', async (t) => {
  const executablePath = findChrome();
  if (!executablePath) {
    t.skip('No local Chrome/Chromium executable available in this CI environment');
    return;
  }
  assert.ok(NFL_ROW && NBA_Q, 'real players available');

  const server = http.createServer((req, res) => {
    const urlPath = (req.url || '/').split('?')[0];
    const rel = urlPath === '/t/' ? '/t/index.html' : urlPath;
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
  const pageErrors = [];

  const newRecipient = async () => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
    const p = await ctx.newPage();
    await p.clock.setFixedTime(new Date(NOW));
    p.on('pageerror', (e) => pageErrors.push(e.message));
    return { ctx, p };
  };
  const open = async (p, frag) => {
    await p.goto('about:blank');
    await p.goto(`${base}/t/#${frag}`, { waitUntil: 'networkidle' });
    await p.waitForSelector('[data-ticket-status]', { timeout: 10000 });
    if (await p.locator('[data-ticket-status="OK"]').count()) await p.waitForSelector('[data-book-row="FanDuel"]', { timeout: 10000 });
    return p.evaluate(() => {
      const row = (b) => document.querySelector(`[data-book-row="${b}"]`);
      return {
        status: document.querySelector('[data-ticket-status]')?.getAttribute('data-ticket-status'),
        ask: document.querySelector('[data-ticket-ask]')?.innerText || '',
        parent: document.querySelector('[data-ticket-parent]')?.innerText || null,
        all: document.body.innerText,
        dkNoQuote: Boolean(row('DraftKings')?.querySelector('[data-no-current-quote]')),
        fdNoQuote: Boolean(row('FanDuel')?.querySelector('[data-no-current-quote]')),
        fairUnavailable: Boolean(document.querySelector('[data-fair-unavailable]')),
        receiptLinks: [...document.querySelectorAll('a[href]')].filter((a) => /\/r\/[0-9a-f]/.test(a.getAttribute('href'))).length,
        fits: document.documentElement.scrollWidth <= window.innerWidth + 2,
      };
    });
  };
  const honest = (s, where) => {
    assert.equal(s.status, 'OK', where);
    assert.equal(s.dkNoQuote, true, `${where}: DraftKings no current quote`);
    assert.equal(s.fdNoQuote, true, `${where}: FanDuel no current quote`);
    assert.equal(s.fairUnavailable, true, where);
    assert.equal(s.receiptLinks, 0, where);
    assert.doesNotMatch(s.all, FORBIDDEN_RE, where);
    assert.ok(s.fits, `${where}: no overflow at 390px`);
  };
  const fork = async (p, odds, take) => {
    await p.evaluate(() => { document.getElementById('forkOut').innerHTML = ''; });
    await p.fill('#forkOdds', odds);
    await p.fill('#forkTake', take || '');
    await p.click('#forkBtn');
    await p.waitForSelector('[data-fork-status]', { timeout: 5000 });
    const status = await p.getAttribute('[data-fork-status]', 'data-fork-status');
    const href = status === 'OK' ? await p.getAttribute('[data-fork-link]', 'href') : null;
    return { status, href, text: await p.locator('#forkOut').innerText() };
  };

  try {
    for (const [label, original] of [
      ['NFL anytime TD', { v: 1, m: 'anytime_td', p: NFL_ROW.player_id, g: NFL_ROW.game_id, n: NFL_ROW.player_display_name, o: 200, at: '2026-11-24T16:00:00.000Z', take: 'Original sender take' }],
      ['NBA 3PM 4+', { v: 1, m: 'nba_threes', r: 4, p: NBA_Q.player_id, g: NBA_Q.game_id, n: NBA_Q.player_name, o: 350, at: '2026-11-24T16:00:00.000Z', take: 'Original sender take' }],
    ]) {
      const originalFrag = b64url(original);
      const { ctx, p } = await newRecipient();

      // Recipient opens the original.
      const before = await open(p, originalFrag);
      honest(before, `${label} original`);
      assert.equal(before.parent, null);
      assert.match(before.ask, new RegExp(`Minimum odds \\${original.o > 0 ? '+' : ''}${original.o}`));

      // Same cutoff and unsigned odds are rejected; nothing is created.
      let r = await fork(p, `+${original.o}`);
      assert.equal(r.status, 'REJECTED');
      assert.match(r.text, /different minimum/);
      r = await fork(p, '275');
      assert.equal(r.status, 'REJECTED');
      assert.match(r.text, /explicit sign/);

      // Recipient sets their own cutoff → new link.
      const newOdds = original.o + 75;
      r = await fork(p, `+${newOdds}`, 'My own cutoff');
      assert.equal(r.status, 'OK');
      assert.match(r.href, /^\/t\/#[A-Za-z0-9_-]+$/);
      const forkFrag = r.href.replace('/t/#', '');
      assert.notEqual(forkFrag, originalFrag);
      const decoded = unb64url(forkFrag);
      assert.equal(decoded.m, original.m);
      assert.equal(decoded.p, original.p);
      assert.equal(decoded.g, original.g);
      if (original.m === 'nba_threes') assert.equal(decoded.r, original.r);
      assert.equal(decoded.o, newOdds);
      assert.equal(decoded.at, '2026-11-24T18:00:00.000Z');
      assert.deepEqual(decoded.f, { o: original.o, at: original.at, take: original.take });
      assert.equal(decoded.take, 'My own cutoff');

      // The page the recipient is on is still the original, unchanged.
      assert.match(p.url(), new RegExp(`#${originalFrag}$`));
      assert.match(await p.locator('[data-ticket-ask]').innerText(), new RegExp(`Minimum odds \\+${original.o}`));

      // A third person opens the fork: new cutoff, original ask as context, honest empty state.
      const third = await newRecipient();
      const forked = await open(third.p, forkFrag);
      honest(forked, `${label} fork`);
      assert.match(forked.ask, new RegExp(`Minimum odds \\+${newOdds}`));
      assert.match(forked.ask, /Created 2026-11-24 18:00 UTC/);
      assert.match(forked.ask, /My own cutoff/);
      assert.ok(forked.parent, 'fork shows the original ask');
      assert.match(forked.parent, new RegExp(`Original minimum \\+${original.o}`));
      assert.match(forked.parent, /Created 2026-11-24 16:00 UTC/);
      assert.match(forked.parent, /Original take: Original sender take/);
      if (original.m === 'nba_threes') assert.match(forked.all, /3PM 4\+ \(Over 3\.5\)/);
      else assert.match(forked.all, /Anytime TD · Yes/);

      // Watch on the fork stores the fork's cutoff only (local).
      await third.p.click('[data-watch-btn]');
      assert.match(await third.p.locator('[data-watch-note]').innerText(), new RegExp(`Watching \\+${newOdds} on this device only\\. No alerts yet\\.`));
      const stored = await third.p.evaluate(() => JSON.parse(localStorage.getItem('propstein.watch.v1')));
      assert.equal(stored.length, 1);
      assert.equal(stored[0].cutoff, newOdds);

      // Fork of a fork keeps only its immediate parent's ask as context.
      r = await fork(third.p, `+${newOdds + 50}`);
      assert.equal(r.status, 'OK');
      assert.deepEqual(unb64url(r.href.replace('/t/#', '')).f, { o: newOdds, at: '2026-11-24T18:00:00.000Z', take: 'My own cutoff' });

      // Original link, opened fresh, is unchanged.
      const again = await open(third.p, originalFrag);
      honest(again, `${label} original reopened`);
      assert.equal(again.parent, null);
      assert.match(again.ask, new RegExp(`Minimum odds \\+${original.o}`));
      assert.match(again.ask, /Original sender take/);
      assert.doesNotMatch(again.ask, /My own cutoff/);

      // Tampered fork context fails closed.
      for (const bad of [{ ...decoded, f: { ...decoded.f, o: newOdds } }, { ...decoded, f: { ...decoded.f, extra: 1 } }, { ...decoded, f: 'x' }]) {
        assert.equal((await open(third.p, b64url(bad))).status, 'NOT_FOUND');
      }

      await third.ctx.close();
      await ctx.close();
    }
    assert.deepEqual(pageErrors, []);
  } finally {
    await browser.close();
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
    server.close();
  }
});
