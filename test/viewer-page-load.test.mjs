import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';

const ROOT = path.resolve('viewer');
const fixture = (name) => fs.readFileSync(path.resolve('test/fixtures', name));
const ledger = fixture('standalone-ledger-report.json');
const slate = fixture('status-pr1-slate.json');
const games = fixture('status-pr1-games.json');
const feed = fixture('status-pr1-feed.json');
const health = fixture('status-pr1-health.json');
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml' };
const APP_PAGES = new Set(['index.html', 'audit.html', 'ledger.html', 'ledger/index.html', 'audit/', 'ledger/']);

function findChrome() {
  return [process.env.CHROME_PATH, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium', chromium.executablePath()]
    .find((file) => file && fs.existsSync(file)) || null;
}

function htmlFiles(dir = ROOT) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? htmlFiles(file) : entry.name.endsWith('.html') ? [path.relative(ROOT, file)] : [];
  }).sort();
}

function serverForFixtures({ omitQuant = false } = {}) {
  return http.createServer((req, res) => {
    const url = new URL(req.url || '/', 'http://localhost');
    const pathname = decodeURIComponent(url.pathname);
    const reply = (code, mime, body) => { res.writeHead(code, { 'Content-Type': mime }); res.end(body); };
    if (omitQuant && pathname === '/js/quant-math.js') return reply(404, 'text/plain', 'missing prerequisite');
    if (pathname === '/clv_report_2026_w7.json') return reply(200, 'application/json', ledger);
    if (pathname === '/clv_audit_report.json') return reply(200, 'application/json', '{"ledger":[],"ledger_root_hash":"fixture-root"}');
    if (pathname === '/feeds/slate-index.json') return reply(200, 'application/json', JSON.stringify({
      schema_version: 'slate-index.v1', sports: { nfl: { default_slate: 'fixture', slates: { fixture: {
        season: 2026, week: 1, slate: 'slate_fixture.json', games: 'games_fixture.json',
        picks: null, livePicks: null, injuries: null, odds: null, snapshots: null, historical: false,
      } } } },
    }));
    if (pathname === '/slate_fixture.json') return reply(200, 'application/json', slate);
    if (pathname === '/games_fixture.json') return reply(200, 'application/json', games);
    if (pathname === '/feeds/feed_status.json') return reply(200, 'application/json', feed);
    if (pathname === '/feeds/health.json') return reply(200, 'application/json', health);
    if (pathname.endsWith('.json')) return reply(200, 'application/json', '{}');
    const routedPath = pathname === '/audit/' ? '/audit.html' : pathname === '/ledger/' ? '/ledger/index.html' : pathname;
    const file = path.resolve(ROOT, `.${routedPath}`);
    if (!file.startsWith(`${ROOT}${path.sep}`) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      if (pathname.endsWith('.js')) return reply(404, 'text/plain', 'missing script');
      return reply(204, 'text/plain', '');
    }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
}

async function checkPage(page, base, file, { expectFailure = false } = {}) {
  const errors = [];
  const onConsole = (msg) => { if (msg.type() === 'error') errors.push(`console: ${msg.text()}`); };
  const onError = (error) => errors.push(`exception: ${error.message}`);
  page.on('console', onConsole);
  page.on('pageerror', onError);
  try {
    await page.goto(`${base}/${file}`, { waitUntil: 'domcontentloaded', timeout: 15000 });
    if (APP_PAGES.has(file)) {
      await page.waitForFunction(() => document.querySelector('#clvStatus')?.textContent?.includes('1/1') || document.body.textContent.includes('LEDGER ROOT UNAVAILABLE'), { timeout: 8000 });
    }
    const text = await page.locator('body').innerText();
    if (expectFailure) {
      assert.ok(errors.length > 0 || /LEDGER ROOT UNAVAILABLE|ROOT UNAVAILABLE/i.test(text), `${file}: removing quant-math.js did not fail`);
    } else {
      assert.deepEqual(errors, [], `${file}: console or uncaught exception`);
      assert.doesNotMatch(text, /LEDGER ROOT UNAVAILABLE|ROOT UNAVAILABLE|BOOT FAILURE/i, `${file}: boot-failure banner`);
    }
    return errors;
  } finally {
    page.off('console', onConsole);
    page.off('pageerror', onError);
  }
}

async function fixturePage(browser, base) {
  const page = await browser.newPage();
  await page.route('**/*', (route) => {
    if (route.request().url().startsWith(base)) return route.continue();
    return route.fulfill({ status: 204, body: '' });
  });
  return page;
}

test('every viewer HTML entry point loads without console errors or boot-failure banners using fixtures', { timeout: 300000 }, async (t) => {
  const executablePath = findChrome();
  if (!executablePath) return t.skip('Chrome/Chromium unavailable');
  const server = serverForFixtures();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, executablePath });
  const base = `http://127.0.0.1:${server.address().port}`;
  const allFiles = htmlFiles();
  assert.ok(allFiles.length > 0, 'No viewer HTML entry points found');
  const files = process.env.SMOKE_PATHS ? process.env.SMOKE_PATHS.split(',') : [...allFiles, 'audit/', 'ledger/'];
  try {
    // Four tabs keep the generated player pages tractable in CI while every path is visited.
    let next = 0;
    await Promise.all(Array.from({ length: 4 }, async () => {
      const page = await fixturePage(browser, base);
      try {
        while (next < files.length) await checkPage(page, base, files[next++]);
      } finally {
        await page.close();
      }
    }));
    t.diagnostic(`Loaded ${files.length} viewer paths from local fixtures`);
  } finally {
    await browser.close();
    server.close();
  }
});

test('removing quant-math.js makes the browser smoke fail', { timeout: 30000 }, async (t) => {
  const executablePath = findChrome();
  if (!executablePath) return t.skip('Chrome/Chromium unavailable');
  const server = serverForFixtures({ omitQuant: true });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, executablePath });
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    const page = await fixturePage(browser, base);
    try {
      await checkPage(page, base, 'audit.html', { expectFailure: true });
    } finally {
      await page.close();
    }
  } finally {
    await browser.close();
    server.close();
  }
});
