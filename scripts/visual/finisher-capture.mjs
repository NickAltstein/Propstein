#!/usr/bin/env node
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const viewer = path.join(root, 'viewer');
const baseCommit = '3b4d099c';

async function capturePhase(phase) {
  const output = path.join(root, 'test/screenshots/finisher', phase);
  fs.mkdirSync(output, { recursive: true });

  const beforeFiles = new Map();
  const mime = {
    '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json',
    '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp',
    '.woff2': 'font/woff2'
  };

  const server = http.createServer((request, response) => {
    const rawPath = new URL(request.url, 'http://local').pathname;
    const relative = decodeURIComponent(rawPath).replace(/^\/+/, '') || 'index.html';
    const file = path.resolve(viewer, relative);

    if (!file.startsWith(`${viewer}${path.sep}`)) {
      response.writeHead(403).end();
      return;
    }

    if (phase === 'before') {
      if (!beforeFiles.has(relative)) {
        try {
          beforeFiles.set(relative, execFileSync('git', ['show', `${baseCommit}:viewer/${relative}`], { cwd: root, maxBuffer: 64 << 20 }));
        } catch {
          beforeFiles.set(relative, null);
        }
      }
      const body = beforeFiles.get(relative);
      if (!body) {
        if (fs.existsSync(file) && fs.statSync(file).isFile()) {
          response.writeHead(200, { 'content-type': mime[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
          fs.createReadStream(file).pipe(response);
          return;
        }
        response.writeHead(404).end();
        return;
      }
      response.writeHead(200, { 'content-type': mime[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' }).end(body);
      return;
    }

    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { 'content-type': mime[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
    fs.createReadStream(file).pipe(response);
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;

  const browser = await chromium.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true
  });

  for (const width of [390, 1440]) {
    const page = await browser.newPage({
      viewport: { width, height: width === 390 ? 844 : 960 },
      serviceWorkers: 'block'
    });

    await page.goto(origin, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => window.__PROP_WIRE_READY__ === true || document.readyState === 'complete', { timeout: 15000 });
    await page.waitForTimeout(600);

    // 1. Capture Item 1: Price-a-Line panel
    const pickFlow = page.locator('#pickFlow');
    if (await pickFlow.count()) {
      await pickFlow.scrollIntoViewIfNeeded();
      await page.waitForTimeout(200);
      await pickFlow.screenshot({ path: path.join(output, `${width}-item1-price-a-line.png`) });
    }

    // 2. Capture Item 3: Card Subtitles on Home/Board
    const homeCards = page.locator('.home-edge-card, [data-rf-card]');
    if (await homeCards.count()) {
      await homeCards.first().scrollIntoViewIfNeeded();
      await page.waitForTimeout(200);
      await homeCards.first().screenshot({ path: path.join(output, `${width}-item3-card-subtitle.png`) });
    }

    // Open board
    const openBoardBtn = page.locator('#homeEdgeOpenBoard');
    if (await openBoardBtn.isVisible()) {
      await openBoardBtn.click();
      await page.waitForTimeout(600);
    }

    // On desktop, switch to card view if in table view
    if (width === 1440) {
      const cardViewBtn = page.locator('#viewCardsBtn, [data-view="cards"]');
      if (await cardViewBtn.isVisible()) {
        await cardViewBtn.click();
        await page.waitForTimeout(300);
      }
    }

    // Switch to research decision card view (historical = false, quotes cleared)
    // so card displays the money row, sim line, copy fair, matchup chips
    await page.evaluate(() => {
      const s = window.__PROP_WIRE_STATE__;
      if (window.PropWireSports && s && s.slateKey) {
        const nfl = window.PropWireSports.require('nfl');
        if (nfl && nfl.slates && nfl.slates[s.slateKey]) {
          nfl.slates[s.slateKey].historical = false;
        }
      }
      if (window.PropSteinTest && window.PropSteinTest.clearQuotes) {
        window.PropSteinTest.clearQuotes();
      }
    });
    await page.waitForTimeout(500);

    // Capture Item 2: card inspect / decision view
    const researchCard = page.locator('.mobile-quant-row.research-card, .mobile-quant-row');
    if (await researchCard.count()) {
      await researchCard.first().scrollIntoViewIfNeeded();
      await page.waitForTimeout(200);
      await researchCard.first().screenshot({ path: path.join(output, `${width}-item2-card-inspect-view.png`) });
    }

    await page.close();
  }

  await browser.close();
  await new Promise((resolve) => server.close(resolve));
  console.log(`Captured ${phase} screenshots in ${output}`);
}

async function main() {
  await capturePhase('before');
  await capturePhase('after');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
