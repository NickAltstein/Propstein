#!/usr/bin/env node
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../viewer');
const out = path.resolve(root, '../artifacts/graphite-review');
fs.mkdirSync(out, { recursive: true });
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.webp': 'image/webp', '.woff2': 'font/woff2' };
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  let rel = decodeURIComponent(url.pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, rel);
  if (!file.startsWith(`${root}/`) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404).end(); return;
  }
  res.writeHead(200, { 'content-type': types[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
const failures = [];
try {
  for (const width of [360, 390, 768, 1024, 1280, 1440, 1680]) {
    console.log(`starting ${width}`);
    const page = await browser.newPage({ viewport: { width, height: 900 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
    await page.route('**/*', (route) => {
      if (new URL(route.request().url()).origin !== `http://127.0.0.1:${server.address().port}`) route.abort();
      else route.continue();
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__PROP_WIRE_READY__ === true, { timeout: 3000 }).catch(() => {});
    await page.locator('#homeEdgeOpenBoard').click({ timeout: 2000, force: true }).catch(() => {});
    await page.waitForTimeout(250);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.waitForTimeout(80);
    const layout = await page.evaluate(() => {
      const box = (selector) => {
        const el = document.querySelector(selector);
        if (!el || getComputedStyle(el).display === 'none') return null;
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
      };
      return { viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        topbar: box('.pw-topbar'), filterbar: box('.pw-filterbar'),
        leagues: box('.terminal-leagues'), boardControls: box('.terminal-board-controls'),
        desktopNav: box('.terminal-desktop-nav'), bottomNav: box('.mobile-bottom-nav'),
        search: box('#headerSearchTrigger'), slate: box('#slateSelector'),
        telemetry: box('#terminalQuoteStatus'), menu: box('#frontMenuBtn') };
    });
    if (layout.scrollWidth > width + 1) failures.push(`${width}: horizontal overflow ${layout.scrollWidth}`);
    for (const [key, value] of Object.entries(layout)) {
      if (value && typeof value === 'object' && (value.x < -1 || value.right > width + 1)
          && !['boardControls'].includes(key)) failures.push(`${width}: ${key} outside viewport`);
    }
    if (width >= 1101 && !layout.desktopNav) failures.push(`${width}: desktop navigation missing`);
    if (width >= 769 && layout.bottomNav) failures.push(`${width}: mobile navigation visible`);
    if ([390, 1440].includes(width)) {
      await page.screenshot({ path: path.join(out, `final-${width}-rest.png`), animations: 'disabled', timeout: 10000 });
      await page.locator('#headerSearchTrigger').hover();
      await page.screenshot({ path: path.join(out, `final-${width}-hover.png`), animations: 'disabled', timeout: 10000 });
      await page.locator('#headerSearchTrigger').focus();
      await page.screenshot({ path: path.join(out, `final-${width}-focus.png`), animations: 'disabled', timeout: 10000 });
      await page.locator('#headerSearchTrigger').click();
      if (!await page.locator('#terminalHeaderSearch').evaluate((el) => el === document.activeElement)) {
        failures.push(`${width}: inline search did not receive focus`);
      }
      await page.locator('#terminalHeaderSearch').fill('Zay');
      await page.locator('#terminalHeaderSearch').press('Enter');
      if (!await page.locator('#terminalBoardTitle').evaluate((el) => el === document.activeElement)) {
        failures.push(`${width}: search submit did not focus board heading`);
      }
      if (width === 1440) {
        const first = page.locator('#marketChipGroup > button').first();
        await first.focus();
        await first.press('ArrowRight');
        if (!await page.locator('#marketChipGroup > button').nth(1).evaluate((el) => el.classList.contains('active'))) {
          failures.push('1440: arrow key did not move market selection');
        }
      }
    }
    console.log(JSON.stringify({ width, ...layout }));
    await page.close();
  }
} finally {
  await browser.close();
  server.close();
}
if (failures.length) {
  failures.forEach((failure) => console.error(failure));
  process.exitCode = 1;
}
