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

test('viewer/app.js renders fail-closed TD Scorers card contract', () => {
  const appJs = fs.readFileSync(path.resolve('viewer/app.js'), 'utf8');
  assert.match(appJs, /function tdScorerCard\(row\)/);
  assert.match(appJs, /data-td-card="1"/);
  assert.match(appJs, /MODEL FAIR · RESEARCH ONLY/);
});

test('TD Scorers tab renders fail-closed TD cards cleanly at 390px dark mode', async (t) => {
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
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));

    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
    const tdBtn = page.locator('[data-market="anytime_td"]').first();
    await tdBtn.click();
    await page.waitForSelector('[data-td-card="1"]', { timeout: 5000 });

    const result = await page.evaluate(() => {
      const card = document.querySelector('[data-td-card="1"]');
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
    assert.match(result.txt, /CeeDee Lamb/);
    assert.match(result.txt, /52\.1%\s*\(-109\)/);
    assert.match(result.txt, /17\.2%\s*\(\+481\)/);
    assert.match(result.txt, /13\.5%\s*\(\+641\)/);
    assert.match(result.txt, /CONF:\s*HIGH/);
    assert.match(result.txt, /ROLE SURGE/);
    assert.match(result.txt, /VACATED RZ \+2\.1/);
    assert.match(result.txt, /MODEL FAIR · RESEARCH ONLY/);
    assert.equal(result.hasUndef, false);
    assert.equal(result.hasNaN, false);
    assert.equal(result.hasNull, false);
    assert.equal(result.hasEdgeOrKelly, false);
    assert.ok(result.scrollWidth <= result.innerWidth + 2, `Horizontal overflow: ${result.scrollWidth} > ${result.innerWidth}`);
  } finally {
    await browser.close();
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
    server.close();
  }
});
