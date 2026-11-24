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

const REAL_W11_ROW = JSON.parse(fs.readFileSync(path.join(VIEWER_DIR, 'slate_2026_w11.json'), 'utf8'))[0];

const PARTIAL_SLATE = [
  {
    game_id: '2026_12_KC_BUF',
    matchup: 'KC @ BUF',
    player_id: 'excluded-kc-buf',
    player_display_name: 'Excluded KC WR',
    player_name: 'Excluded KC WR',
    team: 'KC',
    opponent: 'BUF',
    position: 'CONTEXT_MISSING',
    context_status: 'CONTEXT_MISSING',
    context_missing_fields: ['weather_observed_at', 'weather_values'],
    reason: 'CONTEXT_MISSING: weather_observed_at, weather_values',
    model_prob: null,
    fair_line: null,
    fair_line_yards: null,
    fair_line_rec: null,
    proj_yards: null,
    proj_rec: null,
    actionable: false,
    actionable_stake: 0.0,
    stake: 0.0,
    can_execute: false,
  },
  {
    ...REAL_W11_ROW,
    game_id: '2026_12_DEN_PIT',
    player_display_name: 'Courtland Sutton',
    team: 'DEN',
    opponent: 'PIT',
    context_status: 'READY',
  },
];

const PARTIAL_GAMES = [
  { game_id: '2026_12_KC_BUF', away_team: 'KC', home_team: 'BUF', gameday: '2026-11-26', gametime: '20:20' },
  { game_id: '2026_12_DEN_PIT', away_team: 'DEN', home_team: 'PIT', gameday: '2026-11-29', gametime: '13:00' },
];

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/opt/pw-browsers/chromium',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p) && fs.statSync(p).isFile()) || null;
}

test('PARTIAL NFL slate renders excluded game banner and never renders blank/zero price rows for CONTEXT_MISSING', async (t) => {
  const executablePath = findChrome();
  if (!executablePath) {
    t.skip('No local Chrome/Chromium executable available in this CI environment');
    return;
  }

  const server = http.createServer((req, res) => {
    const urlPath = (req.url || '/').split('?')[0];
    const json = (body) => {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    if (urlPath === '/feeds/slate-index.json') {
      return json({
        schema_version: 'slate-index.v1',
        generated_at: '2026-11-24T15:00:00Z',
        sports: {
          nfl: {
            default_slate: '2026_w12',
            slates: {
              '2026_w12': {
                season: 2026,
                week: 12,
                shortLabel: 'W12',
                slate: 'slate_2026_w12.json',
                games: 'games_2026_w12.json',
                picks: null,
                livePicks: null,
                injuries: null,
                odds: null,
                snapshots: null,
                historical: false,
              },
            },
          },
        },
      });
    }
    if (urlPath === '/slate_2026_w12.json') return json(PARTIAL_SLATE);
    if (urlPath === '/games_2026_w12.json') return json(PARTIAL_GAMES);

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
  const { port } = server.address();
  const browser = await chromium.launch({ headless: true, executablePath });

  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
    await context.addInitScript(() => {
      window.localStorage.setItem('propwire_active_sport', 'nfl');
    });
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-context-missing-banner="1"]', { timeout: 6000 });

    const state = await page.evaluate(() => {
      const banners = Array.from(document.querySelectorAll('[data-context-missing-banner="1"]'))
        .map((el) => el.textContent?.trim() || '');
      const bodyText = document.body.innerText;
      return { banners, bodyText };
    });

    assert.ok(state.banners.length >= 1, 'Expected context-missing banner');
    assert.ok(
      state.banners.some((b) => /1 game unpriced: context missing/i.test(b) && /KC @ BUF|2026_12_KC_BUF/.test(b)),
      `Banner did not name excluded game: ${JSON.stringify(state.banners)}`,
    );
    assert.ok(state.bodyText.includes('Courtland Sutton'), 'Expected READY game player to render');
    assert.ok(!state.bodyText.includes('Excluded KC WR'), 'Excluded game player must not render as a priced/blank row');
  } finally {
    await browser.close();
    server.close();
  }
});
