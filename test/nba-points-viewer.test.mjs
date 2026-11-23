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

const FIXTURE_NBA_SLATE = [
  {
    player_id: '203999',
    player_display_name: 'Nikola Jokic',
    team: 'DEN',
    opponent: 'CHI',
    game_id: 'nba-20261123-DEN-CHI',
    position: 'C',
    minutes_p10: 31.2,
    minutes_hat: 35.8,
    minutes_p90: 40.4,
    sd_minutes: 3.6,
    role_surge: true,
    role_surge_reason: '+3.5 min · 1 teammate OUT/D',
    proj_points: 27.4,
    fair_line_points: 27.5,
    sd_points: 6.1,
    proj_rebounds: 12.1,
    fair_line_rebounds: 11.5,
    proj_assists: 9.4,
    fair_line_assists: 9.5,
    proj_threes: 1.8,
    fair_line_threes: 1.5,
    proj_pra: 48.9,
    fair_line_pra: 48.5,
    market: 'points',
    actionable: false,
    edge_pct: null,
    kelly_stake: 0.0,
    gate_status: 'RESEARCH_ONLY_UNVERIFIED',
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

test('viewer/app.js renders fail-closed NBA Points card contract', () => {
  const appJs = fs.readFileSync(path.resolve('viewer/app.js'), 'utf8');
  assert.match(appJs, /function nbaPointsCard\(row\)/);
  assert.match(appJs, /data-nba-points-card="1"/);
  assert.match(appJs, /MODEL FAIR · RESEARCH ONLY/);
});

test('NBA Points board renders minutes band, Role Surge, and fail-closed copy at 390px dark mode', async (t) => {
  const executablePath = findChrome();
  if (!executablePath) {
    t.skip('No local Chrome/Chromium executable available in this CI environment');
    return;
  }

  const server = http.createServer((req, res) => {
    const urlPath = (req.url || '/').split('?')[0];
    if (urlPath.startsWith('/nba_slate_') && urlPath.endsWith('.json')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(FIXTURE_NBA_SLATE));
      return;
    }
    if (urlPath === '/feeds/slate-index.json') {
      const orig = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'feeds/slate-index.json'), 'utf8'));
      const nbaSlates = orig.sports?.nba?.slates || {};
      const keys = Object.keys(nbaSlates);
      if (keys.length) {
        const lastKey = keys[keys.length - 1];
        nbaSlates[lastKey] = {
          ...nbaSlates[lastKey],
          marketOnly: false,
          modelContextStatus: 'AVAILABLE',
          actionLocked: false,
        };
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(orig));
      return;
    }
    const filePath = path.join(VIEWER_DIR, urlPath === '/' ? 'index.html' : urlPath);
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
    await context.addInitScript(() => {
      window.localStorage.setItem('propwire_active_sport', 'nba');
    });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));

    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-nba-points-card="1"]', { timeout: 6000 });

    const collapsed = await page.evaluate(() => {
      const card = document.querySelector('[data-nba-points-card="1"]');
      const details = card ? card.querySelector('details.ps-card-details') : null;
      return {
        txt: card ? card.innerText : '',
        detailsOpen: details ? details.open : null,
      };
    });
    assert.equal(collapsed.detailsOpen, false);
    assert.match(collapsed.txt, /Nikola Jokic/);
    assert.match(collapsed.txt, /27\.5/);
    assert.match(collapsed.txt, /ROLE SURGE/);
    assert.doesNotMatch(collapsed.txt, /RESEARCH ONLY/);

    await page.locator('[data-nba-points-card="1"] .ps-card-details-summary').first().click();

    const result = await page.evaluate(() => {
      const card = document.querySelector('[data-nba-points-card="1"]');
      const txt = card ? card.innerText : '';
      return {
        txt,
        hasUndef: /\bundefined\b/i.test(txt),
        hasNaN: /\bNaN\b/.test(txt),
        hasNull: /\bnull\b/i.test(txt),
        hasEdgeOrKelly: /\bkelly\b|[+-]\d+(\.\d+)?%\s*ev/i.test(txt),
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
      };
    });

    assert.deepEqual(pageErrors, []);
    assert.match(result.txt, /Nikola Jokic/);
    assert.match(result.txt, /MIN 35\.8 \(31\.2–40\.4\)/);
    assert.match(result.txt, /ROLE SURGE/);
    assert.match(result.txt, /27\.5/);
    assert.match(result.txt, /27\.4/);
    assert.match(result.txt, /MODEL FAIR · RESEARCH ONLY/);
    assert.equal(result.hasUndef, false);
    assert.equal(result.hasNaN, false);
    assert.equal(result.hasNull, false);
    assert.equal(result.hasEdgeOrKelly, false);
    assert.ok(result.scrollWidth <= result.innerWidth + 2, `Horizontal overflow: ${result.scrollWidth} > ${result.innerWidth}`);

    // Switch to non-points market (PRA) and confirm multi-stat card updates fail-closed
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(100);
    const praBtn = page.locator('[data-market="pra"]').first();
    await praBtn.click();
    await page.waitForSelector('[data-nba-market="pra"]', { timeout: 5000 });
    await page.locator('[data-nba-market="pra"] .ps-card-details-summary').first().click();
    const praTxt = await page.locator('[data-nba-market="pra"]').first().innerText();
    assert.match(praTxt, /MODEL FAIR \(PRA\)/);
    assert.match(praTxt, /48\.5/);
    assert.match(praTxt, /MODEL FAIR · RESEARCH ONLY/);
    assert.equal(/\bkelly\b|[+-]\d+(\.\d+)?%\s*ev/i.test(praTxt), false);
  } finally {
    await browser.close();
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
    server.close();
  }
});
