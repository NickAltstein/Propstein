#!/usr/bin/env node
// Fixture-only screenshots for the status/count/overlay PR.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const viewer = path.join(root, 'viewer');
const fixtures = path.join(root, 'test/fixtures');
const phase = process.argv[2] || 'before';
const output = path.join(root, 'test/screenshots/status-pr1', phase);
fs.mkdirSync(output, { recursive: true });
const substitutions = new Map([
  ['slate_2026_w10.json', 'status-pr1-slate.json'],
  ['live_odds_feed.json', 'status-pr1-feed.json'],
  ['games_2026_w10.json', 'status-pr1-games.json'],
  ['feeds/feed_status.json', 'status-pr1-health.json'],
]);
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json',
  '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp',
  '.woff2': 'font/woff2' };
const server = http.createServer((request, response) => {
  const relative = decodeURIComponent(new URL(request.url, 'http://local').pathname).replace(/^\/+/, '') || 'index.html';
  const fixture = substitutions.get(relative);
  const file = fixture ? path.join(fixtures, fixture) : path.resolve(viewer, relative);
  if (!fixture && !file.startsWith(`${viewer}${path.sep}`)) { response.writeHead(403).end(); return; }
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404).end(); return; }
  response.writeHead(200, { 'content-type': mime[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
  fs.createReadStream(file).pipe(response);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
try {
  for (const width of [390, 880]) {
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 900 }, serviceWorkers: 'block' });
    await page.addInitScript(() => {
      const fixed = new Date('2026-11-17T02:50:00Z').getTime();
      const NativeDate = Date;
      window.Date = class extends NativeDate {
        constructor(...args) { super(...(args.length ? args : [fixed])); }
        static now() { return fixed; }
      };
      localStorage.setItem('propwire_active_sport', 'nfl');
    });
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__PROP_WIRE_READY__ === true, { timeout: 15000 });
    await page.waitForTimeout(500);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.waitForTimeout(100);
    await page.screenshot({ path: path.join(output, `${width}-home.png`), animations: 'disabled' });
    if (await page.locator('#homeEdgeOpenBoard').isVisible()) await page.locator('#homeEdgeOpenBoard').click();
    await page.waitForTimeout(250);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.screenshot({ path: path.join(output, `${width}-board.png`), animations: 'disabled' });
    const summary = await page.evaluate(() => ({
      active: document.querySelector('#actionableCount')?.textContent,
      top: document.querySelector('#topCount')?.textContent,
      all: document.querySelector('#allCount')?.textContent,
      telemetry: document.querySelector('#terminalQuoteStatus')?.textContent,
      snapshot: document.querySelector('#snapshotAgePill')?.textContent,
      hero: document.querySelector('#homeEdgeStatus')?.textContent,
    }));
    console.log(width, summary);
    await page.close();
  }
} finally {
  await browser.close();
  server.close();
}
