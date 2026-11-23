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

const FIXTURE_SLATE = [
  {
    player_id: '00-0036358',
    player_display_name: 'CeeDee Lamb',
    team: 'DAL',
    opponent_team: 'TEN',
    game_id: '2026_11_TEN_DAL',
    position: 'WR',
    status: 'ACT',
    proj_rec: 8.5,
    fair_line_rec: 8.5,
    proj_yards: 112.9,
    fair_line_yards: 104.0,
    p10_yards: 52.0,
    p90_yards: 168.0,
    td_mean: 0.78,
    p_anytime_td: 0.521,
    p_2plus_td: 0.172,
    p_first_td: 0.135,
    td_confidence_tier: 'HIGH',
    fair_odds_anytime: -109,
    fair_odds_2plus: 481,
    fair_odds_first: 641,
    td_role_surge_z: 1.85,
    td_vacated_rz_opps: 2.1,
    role_surge: true,
    td_gate_status: 'RESEARCH_ONLY_UNVERIFIED',
    actionable: false,
  },
  {
    player_id: '00-0034869',
    player_display_name: 'DJ Moore',
    team: 'CHI',
    opponent_team: 'DET',
    game_id: '2026_11_CHI_DET',
    position: 'WR',
    status: 'ACT',
    proj_rec: 5.2,
    fair_line_rec: 5.0,
    proj_yards: 64.0,
    fair_line_yards: 60.5,
    p10_yards: 22.0,
    p90_yards: 112.0,
    td_mean: 0.32,
    p_anytime_td: 0.274,
    p_2plus_td: 0.041,
    p_first_td: 0.055,
    td_confidence_tier: 'MEDIUM',
    fair_odds_anytime: 265,
    fair_odds_2plus: 2339,
    fair_odds_first: 1718,
    td_role_surge_z: 0.2,
    td_vacated_rz_opps: 0.0,
    role_surge: false,
    td_gate_status: 'RESEARCH_ONLY_UNVERIFIED',
    actionable: false,
  },
];

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p)) || null;
}

test('viewer scan-first card static contract', () => {
  const appJs = fs.readFileSync(path.resolve('viewer/app.js'), 'utf8');
  const css = fs.readFileSync(path.resolve('viewer/terminal-ui.css'), 'utf8');
  const html = fs.readFileSync(path.resolve('viewer/index.html'), 'utf8');
  assert.match(appJs, /class="prop-card ps-scan-card/);
  assert.match(appJs, /<details class="ps-card-details" data-card-details="1">/);
  assert.match(css, /\.ps-scan-card\s*\{/);
  assert.match(css, /\.ps-card-details\s*\{/);
  assert.match(html, /data-signal-chip="surge"/);
  assert.match(html, /data-signal-chip="high_var"/);
});

test('Scan-first player cards render clean hierarchy, collapsed Details, and zero overflow at 390px dark mode', async (t) => {
  const executablePath = findChrome();
  if (!executablePath) {
    t.skip('No local Chrome/Chromium executable available in this CI environment');
    return;
  }

  const server = http.createServer((req, res) => {
    const urlPath = (req.url || '/').split('?')[0];
    if (urlPath.startsWith('/slate_2026_w') && urlPath.endsWith('.json')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(FIXTURE_SLATE));
      return;
    }
    const resolvedPath = (urlPath.startsWith('/r/') && urlPath !== '/r/receipt.js') ? '/r/index.html' : (urlPath === '/' ? 'index.html' : urlPath);
    const filePath = path.join(VIEWER_DIR, resolvedPath);
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.writeHead(404);
      res.end('not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream' });
    fs.createReadStream(filePath).pipe(res);
  });

  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const { port } = server.address();
  const browser = await chromium.launch({ headless: true, executablePath });

  try {
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      colorScheme: 'dark',
    });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));

    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
    // Switch to All mode so both fixture cards render in the scan-first grid
    await page.locator('#modeAllBtn').click();
    await page.waitForSelector('[data-scan-card="1"]', { timeout: 5000 });

    const beforeExpand = await page.evaluate(() => {
      const card = document.querySelector('[data-scan-card="1"]');
      const details = card ? card.querySelector('details.ps-card-details') : null;
      const telemetry = document.querySelector('.pw-hero-telemetry-suite');
      const txt = card ? card.innerText : '';
      return {
        txt,
        detailsOpen: details ? details.open : null,
        hasHeader: Boolean(card && card.querySelector('.ps-scan-header')),
        hasPrimary: Boolean(card && card.querySelector('.ps-scan-primary')),
        hasSecondary: Boolean(card && card.querySelector('.ps-scan-secondary')),
        signalBadgeCount: card ? card.querySelectorAll('.ps-signal-badge').length : 0,
        telemetryHidden: telemetry ? getComputedStyle(telemetry).display === 'none' : true,
        hasResearchVisibleWhenCollapsed: /RESEARCH ONLY/i.test(txt),
        hasEdgeOrKelly: /\bkelly\b|[+-]\d+(\.\d+)?%\s*ev/i.test(txt),
        hasUndefNaNNull: /\b(undefined|NaN|null)\b/i.test(txt),
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
      };
    });

    assert.deepEqual(pageErrors, []);
    assert.equal(beforeExpand.hasHeader, true);
    assert.equal(beforeExpand.hasPrimary, true);
    assert.equal(beforeExpand.hasSecondary, true);
    assert.equal(beforeExpand.signalBadgeCount, 1);
    assert.equal(beforeExpand.detailsOpen, false);
    assert.equal(beforeExpand.telemetryHidden, true);
    assert.equal(beforeExpand.hasResearchVisibleWhenCollapsed, false);
    assert.equal(beforeExpand.hasEdgeOrKelly, false);
    assert.equal(beforeExpand.hasUndefNaNNull, false);
    assert.match(beforeExpand.txt, /CeeDee Lamb/);
    assert.match(beforeExpand.txt, /MODEL FAIR/);
    assert.match(beforeExpand.txt, /BOOK/);
    assert.ok(beforeExpand.scrollWidth <= beforeExpand.innerWidth + 2, `Horizontal overflow: ${beforeExpand.scrollWidth} > ${beforeExpand.innerWidth}`);

    // Expand Details and verify research disclaimer + no modal open + no overflow
    await page.locator('[data-scan-card="1"] .ps-card-details-summary').first().click();
    const afterExpand = await page.evaluate(() => {
      const card = document.querySelector('[data-scan-card="1"]');
      const details = card ? card.querySelector('details.ps-card-details') : null;
      const modal = document.querySelector('#detailModal');
      const txt = card ? card.innerText : '';
      return {
        txt,
        detailsOpen: details ? details.open : false,
        modalOpen: modal ? modal.open : false,
        hasEdgeOrKelly: /\bkelly\b|[+-]\d+(\.\d+)?%\s*ev/i.test(txt),
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
      };
    });

    assert.equal(afterExpand.detailsOpen, true);
    assert.equal(afterExpand.modalOpen, false);
    assert.match(afterExpand.txt, /MODEL FAIR · RESEARCH ONLY/);
    assert.equal(afterExpand.hasEdgeOrKelly, false);
    assert.ok(afterExpand.scrollWidth <= afterExpand.innerWidth + 2);

    // Scroll back to top so sticky header reveals the filter chip row, then test Role Surge chip
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(100);
    await page.locator('[data-signal-chip="surge"]').click();
    const surgeCount = await page.locator('[data-scan-card="1"]').count();
    assert.equal(surgeCount, 1);

    // One-tap Share to X button carries /r/<hash> and never leaks Kelly/EV when actionable is false
    const shareHref = await page.locator('[data-scan-card="1"] [data-share-receipt="1"]').first().getAttribute('href');
    assert.ok(shareHref, 'Expected one-tap Share to X link on scan-first card');
    assert.match(shareHref, /^https:\/\/x\.com\/intent\/post\?text=/);
    const decodedShare = decodeURIComponent(shareHref.split('text=')[1] || '');
    assert.match(decodedShare, /CeeDee Lamb/);
    assert.match(decodedShare, /https:\/\/propstein\.com\/r\/[0-9a-f]{8}/);
    assert.doesNotMatch(decodedShare, /\bkelly\b|[+-]\d+(\.\d+)?%\s*ev/i);

    // Verifiable /r/<hash> route resolves committed hash and fails closed on unknown hash
    await page.goto(`http://127.0.0.1:${port}/r/9f123b87`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-receipt-status="VERIFIED"]', { timeout: 5000 });
    const verifiedTxt = await page.locator('#receiptRoot').innerText();
    assert.match(verifiedTxt, /9f123b87/);

    await page.goto(`http://127.0.0.1:${port}/r/deadbeef`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-receipt-status="NOT_FOUND"]', { timeout: 5000 });
    const notFoundTxt = await page.locator('#receiptRoot').innerText();
    assert.match(notFoundTxt, /Receipt not found/i);
  } finally {
    await browser.close();
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
    server.close();
  }
});
