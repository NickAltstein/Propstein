#!/usr/bin/env node
/* Run after staging_receipt.py. In-memory route override, no published feed edit. */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

if (process.env.PROPSTEIN_STAGING_VERIFY !== '1') throw new Error('Staging flag required');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../viewer');
const out = path.resolve(root, '../artifacts/graphite-review/staging');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp' };
const server = http.createServer((req, res) => {
  const rel = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, rel);
  if (!file.startsWith(`${root}/`) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404).end(); return; }
  res.writeHead(200, { 'content-type': types[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
try {
  for (const name of ['receipt-matched', 'receipt-failed']) {
    const feed = fs.readFileSync(path.join(out, `${name}.json`));
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block' });
    await page.route('**/*', (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/live_odds_feed.json')) route.fulfill({ status: 200, contentType: 'application/json', body: feed });
      else if (url.origin !== origin) route.abort();
      else route.continue();
    });
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    await page.evaluate(() => document.getElementById('homeEdgeOpenBoard')?.click());
    await page.waitForTimeout(400);
    const chip = page.locator('.terminal-receipt').first();
    const count = await chip.count();
    if (count && name === 'receipt-matched') {
      await chip.scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, 'receipt-chip-390.png'), animations: 'disabled' });
      await chip.screenshot({ path: path.join(out, 'receipt-chip-detail.png'), animations: 'disabled' });
      await chip.locator('summary').click();
      await page.screenshot({ path: path.join(out, 'receipt-expanded-390.png'), animations: 'disabled' });
      if (!(await chip.locator('code').isVisible())) throw new Error('Expanded SHA-256 receipt is not visible');
      console.log(JSON.stringify(await chip.evaluate((el) => {
        const rect = (node) => { const r = node.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; };
        return { receipt: rect(el), code: rect(el.querySelector('code')), parent: el.parentElement?.className };
      })));
      await chip.screenshot({ path: path.join(out, 'receipt-expanded-detail.png'), animations: 'disabled' });
    } else {
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
      await page.screenshot({ path: path.join(out, `${name}-390.png`), animations: 'disabled' });
    }
    if (name === 'receipt-failed' && !await page.getByText('No verified plays', { exact: false }).count()) {
      throw new Error('Failed receipt did not remain in research-only state');
    }
    console.log(`${name}: receipt chips=${count}, research-only=${await page.getByText('No verified plays', { exact: false }).count() > 0}`);
    await page.close();
  }
} finally { await browser.close(); server.close(); }
