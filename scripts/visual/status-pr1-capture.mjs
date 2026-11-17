#!/usr/bin/env node
// Fixture-only screenshots for the status/count/overlay PR.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const viewer = path.join(root, 'viewer');
const fixtures = path.join(root, 'test/fixtures');
const phase = process.argv[2] || 'before';
const sweep = phase === 'sweep';
const fixedTime = phase.includes('snapshot') || phase.includes('mixed') ? '2026-11-16T23:30:00Z'
  : phase.includes('zero') ? '2026-11-16T21:05:00Z' : '2026-11-17T02:50:00Z';
const output = path.join(root, 'test/screenshots/status-pr1', phase);
fs.mkdirSync(output, { recursive: true });
const substitutions = new Map([
  ['slate_2026_w10.json', 'status-pr1-slate.json'],
  ['live_odds_feed.json', phase.includes('mixed') ? 'status-pr1-mixed-feed.json' : 'status-pr1-feed.json'],
  ['games_2026_w10.json', 'status-pr1-games.json'],
  ['feeds/feed_status.json', 'status-pr1-health.json'],
]);
const beforeFiles = new Map();
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json',
  '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp',
  '.woff2': 'font/woff2' };
const server = http.createServer((request, response) => {
  const relative = decodeURIComponent(new URL(request.url, 'http://local').pathname).replace(/^\/+/, '') || 'index.html';
  const fixture = substitutions.get(relative);
  const file = fixture ? path.join(fixtures, fixture) : path.resolve(viewer, relative);
  if (!fixture && !file.startsWith(`${viewer}${path.sep}`)) { response.writeHead(403).end(); return; }
  if (!fixture && phase.startsWith('before')) {
    if (!beforeFiles.has(relative)) {
      try { beforeFiles.set(relative, execFileSync('git', ['show', `f61fe969:viewer/${relative}`], { cwd: root, maxBuffer: 64 << 20 })); }
      catch { beforeFiles.set(relative, null); }
    }
    const body = beforeFiles.get(relative);
    if (!body) { response.writeHead(404).end(); return; }
    response.writeHead(200, { 'content-type': mime[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' }).end(body);
    return;
  }
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404).end(); return; }
  response.writeHead(200, { 'content-type': mime[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
  fs.createReadStream(file).pipe(response);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
const failures = [];
try {
  for (const width of (sweep ? [360, 390, 430, 768, 880, 1024, 1280, 1440, 1680] : [390, 880])) {
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 900 }, serviceWorkers: 'block' });
    await page.addInitScript((fixedIso) => {
      const fixed = new Date(fixedIso).getTime();
      const NativeDate = Date;
      window.Date = class extends NativeDate {
        constructor(...args) { super(...(args.length ? args : [fixed])); }
        static now() { return fixed; }
      };
      localStorage.setItem('propwire_active_sport', 'nfl');
    }, fixedTime);
    await page.route('**/*', (route) => {
      const url = new URL(route.request().url());
      if (url.origin === origin) return route.continue();
      if (phase.includes('mixed') && url.hostname === 'site.api.espn.com')
        return route.fulfill({ status: 200, contentType: 'application/json', body: fs.readFileSync(path.join(fixtures, 'status-pr1-schedule.json')) });
      return route.abort();
    });
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__PROP_WIRE_READY__ === true, { timeout: 15000 });
    if (phase.includes('mixed') && phase.startsWith('after'))
      await page.waitForFunction(() => Array.isArray(window.PropSteinScheduleEvents), { timeout: 10000 });
    await page.waitForTimeout(500);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.waitForTimeout(100);
    if (!sweep) await page.screenshot({ path: path.join(output, `${width}-home.png`), animations: 'disabled' });
    if (await page.locator('#homeEdgeOpenBoard').isVisible()) await page.locator('#homeEdgeOpenBoard').click();
    await page.waitForTimeout(600);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    if (phase.includes('mixed') && await page.locator('#terminalFeedDetails').count())
      await page.locator('#terminalFeedDetails').evaluate((el) => { el.open = true; });
    const initialGeometry = sweep ? await page.evaluate(() => {
      const box = (selector) => {
        const el = document.querySelector(selector);
        if (!el || getComputedStyle(el).display === 'none') return null;
        const r = el.getBoundingClientRect();
        return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
      };
      return { banner: box('.terminal-snapshot-strip'), density: box('.view-switch') };
    }) : null;
    if (!sweep) await page.screenshot({ path: path.join(output, `${width}-board.png`), animations: 'disabled' });
    if (phase.includes('toast') || sweep) {
      await page.locator('#cardView [data-testid="slate-card"]').last().scrollIntoViewIfNeeded().catch(() => {});
      await page.evaluate(() => window.__PROP_WIRE_TRIGGER_POLL__?.(false));
      await page.waitForTimeout(150);
      if (!sweep) await page.screenshot({ path: path.join(output, `${width}-toast.png`), animations: 'disabled' });
    }
    if (sweep) {
      const geometry = await page.evaluate(() => {
        const rect = (selector) => {
          const el = document.querySelector(selector);
          if (!el || getComputedStyle(el).display === 'none') return null;
          const r = el.getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
        };
        return { header: rect('.pw-terminal-chrome'), banner: rect('.terminal-snapshot-strip'),
          density: rect('.view-switch'), toast: rect('#appToast'), nav: rect('.mobile-bottom-nav'),
          lastCard: rect('#cardView [data-testid="slate-card"]:last-of-type'),
          scrollWidth: document.documentElement.scrollWidth };
      });
      const overlap = (a, b) => a && b && a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      if (geometry.scrollWidth > width + 1) failures.push(`${width}: horizontal overflow`);
      if (overlap(initialGeometry.banner, initialGeometry.density)) failures.push(`${width}: banner overlaps density`);
      if (overlap(geometry.toast, geometry.nav)) failures.push(`${width}: toast overlaps navigation`);
      if (overlap(geometry.toast, geometry.lastCard)) failures.push(`${width}: toast overlaps last card`);
      console.log(width, geometry);
    }
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
if (failures.length) { failures.forEach((failure) => console.error(failure)); process.exitCode = 1; }
