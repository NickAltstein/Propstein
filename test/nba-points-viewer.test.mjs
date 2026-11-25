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

// NFL has a published model, so model-card rendering (layout, numbers, footer states) is
// covered on an NFL card. NBA has no model (Option A, #270): fed the same model-shaped
// fixture above, it must render no model card and no "MODEL FAIR" at all.
const W11_ROW = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'slate_2026_w11.json'), 'utf8'))[0];
const FIXTURE_NFL_SLATE = [{
  ...W11_ROW,
  player_id: 'fx-nfl-model-card',
  player_display_name: 'Model Card Receiver',
  team: 'DEN',
  opponent_team: 'PIT',
  game_id: '2026_12_DEN_PIT',
  context_status: 'READY',
  role_surge: true,
  role_surge_reason: '+2.1 targets · 1 teammate OUT',
  fair_line_yards: 64.5,
  proj_yards: 70.4,
  fair_line_rec: 5.5,
  proj_rec: 5.9,
}];
const FIXTURE_NFL_GAMES = [{ game_id: '2026_12_DEN_PIT', away_team: 'DEN', home_team: 'PIT', gameday: '2026-11-29', gametime: '13:00' }];

function slateIndex(sport) {
  const nfl = { default_slate: '2026_w12', slates: { '2026_w12': {
    season: 2026, week: 12, shortLabel: 'W12', slate: 'slate_2026_w12.json', games: 'games_2026_w12.json',
    picks: null, livePicks: null, injuries: null, odds: null, snapshots: null, historical: false } } };
  const nba = { default_slate: '2026-11-23', slates: { '2026-11-23': {
    season: 2026, date: '2026-11-23', shortLabel: 'Nov 23', slate: 'nba_slate_2026-11-23.json',
    games: 'nba_games_2026-11-23.json', odds: 'nba_odds_2026-11-23.json', injuries: 'nba_injuries_2026-11-23.json',
    snapshots: null, historical: false, marketOnly: false, modelContextStatus: 'AVAILABLE', actionLocked: false } } };
  return { schema_version: 'slate-index.v1', generated_at: '2026-11-23T18:00:00Z', sports: sport === 'nba' ? { nba } : { nfl } };
}

async function withViewer(sport, run) {
  const server = http.createServer((req, res) => {
    const urlPath = (req.url || '/').split('?')[0];
    const json = (body) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
    if (urlPath === '/feeds/slate-index.json') return json(slateIndex(sport));
    if (urlPath.startsWith('/nba_slate_') && urlPath.endsWith('.json')) return json(FIXTURE_NBA_SLATE);
    if (urlPath === '/slate_2026_w12.json') return json(FIXTURE_NFL_SLATE);
    if (urlPath === '/games_2026_w12.json') return json(FIXTURE_NFL_GAMES);
    const filePath = path.join(VIEWER_DIR, urlPath === '/' ? 'index.html' : urlPath);
    if (!filePath.startsWith(VIEWER_DIR) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.writeHead(404);
      res.end('not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream' });
    fs.createReadStream(filePath).pipe(res);
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const browser = await chromium.launch({ headless: true, executablePath: findChrome() });
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
    await context.addInitScript((s) => { window.localStorage.setItem('propwire_active_sport', s); }, sport);
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`, { waitUntil: 'networkidle' });
    await run(page, pageErrors);
  } finally {
    await browser.close();
    if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
    server.close();
  }
}

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

test('model card renders role surge, fair/projection and fail-closed copy at 390px dark mode (NFL)', async (t) => {
  if (!findChrome()) {
    t.skip('No local Chrome/Chromium executable available in this CI environment');
    return;
  }
  await withViewer('nfl', async (page, pageErrors) => {
    const card = '#cardView [data-scan-card][data-player-id="fx-nfl-model-card"]';
    await page.waitForSelector(card, { timeout: 6000 });

    const collapsed = await page.evaluate((sel) => {
      const el = document.querySelector(sel);
      const details = el ? el.querySelector('details.ps-card-details') : null;
      return { txt: el ? el.innerText : '', detailsOpen: details ? details.open : null };
    }, card);
    assert.equal(collapsed.detailsOpen, false);
    assert.match(collapsed.txt, /Model Card Receiver/);
    assert.match(collapsed.txt, /64\.5/);
    assert.match(collapsed.txt, /ROLE SURGE/);
    assert.doesNotMatch(collapsed.txt, /RESEARCH ONLY/);

    await page.locator(`${card} .ps-card-details-summary`).first().click();
    const result = await page.evaluate((sel) => {
      const el = document.querySelector(sel);
      const txt = el ? el.innerText : '';
      return {
        txt,
        hasUndef: /\bundefined\b/i.test(txt),
        hasNaN: /\bNaN\b/.test(txt),
        hasNull: /\bnull\b/i.test(txt),
        hasEdgeOrKelly: /\bkelly\b|[+-]\d+(\.\d+)?%\s*ev/i.test(txt),
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
      };
    }, card);
    assert.deepEqual(pageErrors, []);
    assert.match(result.txt, /Model Card Receiver/);
    assert.match(result.txt, /ROLE SURGE/);
    assert.match(result.txt, /64\.5/);
    assert.match(result.txt, /70\.4/);
    assert.match(result.txt, /MODEL FAIR · RESEARCH ONLY/);
    assert.equal(result.hasUndef, false);
    assert.equal(result.hasNaN, false);
    assert.equal(result.hasNull, false);
    assert.equal(result.hasEdgeOrKelly, false);
    assert.ok(result.scrollWidth <= result.innerWidth + 2, `Horizontal overflow: ${result.scrollWidth} > ${result.innerWidth}`);

    // Switch market (receptions): the model card updates and stays fail-closed.
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.locator('[data-market="receptions"]').first().click();
    await page.waitForFunction((sel) => /5\.5/.test(document.querySelector(sel)?.innerText || ''), card, { timeout: 5000 });
    await page.locator(`${card} .ps-card-details-summary`).first().click();
    const recTxt = await page.locator(card).first().innerText();
    assert.match(recTxt, /5\.5/);
    assert.match(recTxt, /MODEL FAIR · RESEARCH ONLY/);
    assert.equal(/\bkelly\b|[+-]\d+(\.\d+)?%\s*ev/i.test(recTxt), false);
  });
});

test('NBA has no model: a model-shaped slate renders no model card and no MODEL FAIR at 390px', async (t) => {
  if (!findChrome()) {
    t.skip('No local Chrome/Chromium executable available in this CI environment');
    return;
  }
  await withViewer('nba', async (page, pageErrors) => {
    await page.waitForSelector('#emptyState.nba-market-only', { timeout: 6000 });
    const state = await page.evaluate(() => ({
      cards: document.querySelectorAll('[data-nba-points-card], #cardView [data-scan-card], [data-rf-card]').length,
      txt: document.querySelector('main')?.innerText || document.body.innerText,
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    assert.deepEqual(pageErrors, []);
    assert.equal(state.cards, 0, 'no model cards on NBA');
    assert.doesNotMatch(state.txt, /MODEL FAIR/);
    assert.doesNotMatch(state.txt, /Nikola Jokic[\s\S]{0,80}27\.5/, 'the fixture fair line is not shown');
    assert.ok(state.scrollWidth <= state.innerWidth + 2, `Horizontal overflow: ${state.scrollWidth} > ${state.innerWidth}`);
  });
});
