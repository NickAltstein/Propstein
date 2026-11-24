#!/usr/bin/env node
// Evidence harness for the viewer redesign PRs: screenshots + layout assertions + axe.
//
//   node scripts/visual/redesign-gates.mjs <outDir> [--data-ref <sha>] [--axe <path/to/axe.min.js>]
//
// - Page code comes from the working tree; every .json/.csv comes from ONE pinned
//   commit (default: scripts/visual/redesign-data-ref) so before/after shots and
//   assertions use identical data, never the live slate.
// - Date.now() is fixed to that commit's live_odds_feed.json updated_at + 15 min.
// - Cross-origin images get a transparent pixel (headshot fallbacks are used).
// - Dark mode, reduced motion, service worker blocked.
//
// Scenarios: home slate, expanded player, Line Pricer, PARTIAL slate, no-quote card.
// Viewports: 375, 390, 430, 768, 1440.
// Assertions (written to <outDir>/report.json; exit code 1 if any fail):
//   overflow     no horizontal page overflow
//   bottomBar    the fixed bottom bar never covers the last content at max scroll
//   bookOneLine  every list book price (line + price) renders on one line
//   numberOnce   within each collapsed list card, no displayed number repeats
//   axe          no serious/critical axe violations (incl. color-contrast) — only with --axe
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const VIEWER = path.join(ROOT, 'viewer');
const args = process.argv.slice(2);
const flag = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : null);
const outDir = path.resolve(ROOT, args[0] && !args[0].startsWith('--') ? args[0] : '.visual/gates');
const DATA_REF = flag('--data-ref') || fs.readFileSync(path.join(ROOT, 'scripts/visual/redesign-data-ref'), 'utf8').trim();
const AXE = flag('--axe');
const ONLY = flag('--only');

const show = (rel) => {
  try { return execFileSync('git', ['show', `${DATA_REF}:viewer/${rel}`], { cwd: ROOT, maxBuffer: 64 << 20, stdio: ['ignore', 'pipe', 'ignore'] }); } catch { return null; }
};
const feed = JSON.parse(show('live_odds_feed.json') || '{}');
const FIXED_TIME = new Date(Date.parse(feed.updated_at || '2026-11-24T21:00:00Z') + 15 * 60000).toISOString();

// PARTIAL slate fixture (same shape as test/partial-slate-viewer.test.mjs).
const W11 = JSON.parse(fs.readFileSync(path.join(VIEWER, 'slate_2026_w11.json'), 'utf8'))[0];
const PARTIAL = {
  index: { schema_version: 'slate-index.v1', generated_at: FIXED_TIME, sports: { nfl: { default_slate: '2026_w12', slates: { '2026_w12': {
    season: 2026, week: 12, shortLabel: 'W12', slate: 'slate_2026_w12.json', games: 'games_2026_w12.json',
    picks: null, livePicks: null, injuries: null, odds: null, snapshots: null, historical: false } } } } },
  slate: [
    { game_id: '2026_12_KC_BUF', matchup: 'KC @ BUF', player_id: 'excluded-kc-buf', player_display_name: 'Excluded KC WR',
      team: 'KC', opponent: 'BUF', position: 'CONTEXT_MISSING', context_status: 'CONTEXT_MISSING',
      context_missing_fields: ['weather_observed_at', 'weather_values'], reason: 'CONTEXT_MISSING: weather_observed_at, weather_values',
      fair_line_yards: null, fair_line_rec: null, proj_yards: null, proj_rec: null, actionable: false },
    { ...W11, game_id: '2026_12_DEN_PIT', player_display_name: 'Courtland Sutton', team: 'DEN', opponent: 'PIT', context_status: 'READY' },
  ],
  games: [
    { game_id: '2026_12_KC_BUF', away_team: 'KC', home_team: 'BUF', gameday: '2026-11-26', gametime: '20:20' },
    { game_id: '2026_12_DEN_PIT', away_team: 'DEN', home_team: 'PIT', gameday: '2026-11-29', gametime: '13:00' },
  ],
};

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.csv': 'text/csv', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp',
  '.avif': 'image/avif', '.jpg': 'image/jpeg', '.ico': 'image/x-icon', '.webmanifest': 'application/manifest+json', '.woff2': 'font/woff2' };
let partialMode = false;
const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const ext = path.extname(urlPath).toLowerCase();
  if (partialMode) {
    const send = (body) => res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(body));
    if (urlPath === '/feeds/slate-index.json') return send(PARTIAL.index);
    if (urlPath === '/slate_2026_w12.json') return send(PARTIAL.slate);
    if (urlPath === '/games_2026_w12.json') return send(PARTIAL.games);
  }
  if (ext === '.json' || ext === '.csv') {
    const body = show(urlPath.replace(/^\/+/, ''));
    if (!body) { res.writeHead(404).end(); return; }
    res.writeHead(200, { 'content-type': TYPES[ext], 'cache-control': 'no-store' }).end(body);
    return;
  }
  let p = { '/': '/index.html' }[urlPath] || urlPath;
  if (p.endsWith('/')) p += 'index.html';
  const abs = path.join(VIEWER, p);
  if (!abs.startsWith(VIEWER) || !fs.existsSync(abs) || !fs.statSync(abs).isFile()) { res.writeHead(404).end(); return; }
  res.writeHead(200, { 'content-type': TYPES[path.extname(abs).toLowerCase()] || 'application/octet-stream', 'cache-control': 'no-store' });
  fs.createReadStream(abs).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const ORIGIN = `http://127.0.0.1:${server.address().port}`;

const VIEWPORTS = [375, 390, 430, 768, 1440];
const BLANK_PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');
const settle = async (page) => { await page.waitForLoadState('networkidle'); await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(500); };
const scrollTo = (sel) => async (page) => { await page.evaluate((s) => { const el = document.querySelector(s); if (el) el.scrollIntoView({ block: 'start' }); window.scrollBy(0, -64); }, sel); };

const SCENARIOS = [
  { name: 'home', shot: 'full' },
  { name: 'expanded', before: async (page) => {
      await page.evaluate(() => {
        const card = document.querySelector('#cardView [data-scan-card]');
        const details = card && card.querySelector('details');
        if (details) details.open = true;
        card?.scrollIntoView({ block: 'start' });
        window.scrollBy(0, -64);
      });
    } },
  { name: 'line-pricer', before: scrollTo('#pickFlow') },
  { name: 'partial', partial: true, before: async (page) => {
      await page.waitForSelector('[data-context-missing-banner="1"]', { timeout: 6000 }).catch(() => {});
    }, shot: 'full' },
  { name: 'no-quote', before: async (page) => {
      await page.evaluate(() => {
        const card = [...document.querySelectorAll('#cardView [data-scan-card]')].find((c) => /No quote/.test(c.textContent));
        card?.scrollIntoView({ block: 'start' });
        window.scrollBy(0, -64);
      });
    } },
];

async function assertions(page) {
  return page.evaluate(() => {
    const out = {};
    const doc = document.documentElement;
    out.overflow = { pass: doc.scrollWidth <= window.innerWidth + 1, scrollWidth: doc.scrollWidth, innerWidth: window.innerWidth };

    const books = [...document.querySelectorAll('#cardView .ps-scan-metric-book .ps-scan-value, #cardView [data-book-price]')]
      .filter((el) => el.offsetParent !== null && el.textContent.trim() && el.textContent.trim() !== '—');
    const wrapped = books.filter((el) => {
      const lh = parseFloat(getComputedStyle(el).lineHeight) || parseFloat(getComputedStyle(el).fontSize) * 1.3;
      return el.getBoundingClientRect().height > lh * 1.5;
    }).map((el) => el.textContent.trim());
    out.bookOneLine = { pass: wrapped.length === 0, checked: books.length, wrapped: wrapped.slice(0, 5) };

    const cards = [...document.querySelectorAll('#cardView [data-scan-card]')].slice(0, 25);
    const repeats = [];
    for (const card of cards) {
      // Visible text only: closed <details> bodies are not displayed.
      const text = card.innerText || '';
      const nums = (text.match(/\d+\.\d+|\d{2,}/g) || []);
      const seen = new Map();
      nums.forEach((n) => seen.set(n, (seen.get(n) || 0) + 1));
      const dup = [...seen].filter(([, c]) => c > 1).map(([n, c]) => `${n}×${c}`);
      if (dup.length) repeats.push(`${(card.querySelector('.ps-scan-player, [data-player-name]')?.textContent || '?').trim()}: ${dup.join(', ')}`);
    }
    out.numberOnce = { pass: repeats.length === 0, checked: cards.length, repeats: repeats.slice(0, 5) };
    return out;
  });
}

async function bottomBarCheck(page) {
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForTimeout(300);
  return page.evaluate(() => {
    const bars = [...document.querySelectorAll('.mobile-bottom-nav, [data-bottom-bar]')].filter((el) => {
      const cs = getComputedStyle(el); return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.position === 'fixed';
    });
    if (!bars.length) return { pass: true, note: 'no fixed bottom bar at this width' };
    const barTop = Math.min(...bars.map((b) => b.getBoundingClientRect().top));
    // The last visible content block in normal flow.
    const blocks = [...document.querySelectorAll('main > *, body > footer, .terminal-compliance-footer')]
      .filter((el) => el.offsetParent !== null && getComputedStyle(el).position !== 'fixed');
    const last = blocks.reduce((acc, el) => (el.getBoundingClientRect().bottom > (acc?.getBoundingClientRect().bottom ?? -1) ? el : acc), null);
    // Find the last line of real text inside it.
    let textBottom = 0;
    if (last) {
      const walker = document.createTreeWalker(last, NodeFilter.SHOW_TEXT);
      const range = document.createRange();
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (!n.textContent.trim() || !n.parentElement || n.parentElement.offsetParent === null) continue;
        range.selectNodeContents(n);
        const r = range.getBoundingClientRect();
        if (r.height) textBottom = Math.max(textBottom, r.bottom);
      }
    }
    return { pass: textBottom <= barTop + 0.5, barTop: Math.round(barTop), lastTextBottom: Math.round(textBottom) };
  });
}

fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ channel: 'chromium', args: ['--force-color-profile=srgb', '--font-render-hinting=none'] });
const report = { dataRef: DATA_REF, clock: FIXED_TIME, results: [] };
let failures = 0;
try {
  for (const s of SCENARIOS) {
    for (const width of VIEWPORTS) {
      const name = `${s.name}--${width}`;
      if (ONLY && !name.includes(ONLY)) continue;
      partialMode = Boolean(s.partial);
      const mobile = width < 768;
      const context = await browser.newContext({ viewport: { width, height: width >= 1024 ? 900 : mobile ? 844 : 1024 },
        deviceScaleFactor: 2, isMobile: mobile, hasTouch: mobile, colorScheme: 'dark', reducedMotion: 'reduce',
        serviceWorkers: 'block', locale: 'en-US', timezoneId: 'America/New_York' });
      await context.route('**/*', (route) => {
        const req = route.request();
        if (req.url().startsWith(ORIGIN)) return route.continue();
        if (req.resourceType() === 'image') return route.fulfill({ status: 200, contentType: 'image/png', body: BLANK_PNG });
        if (req.resourceType() === 'stylesheet') return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
        return route.abort();
      });
      await context.addInitScript(() => { try { localStorage.setItem('pw_swipe_hint_seen', 'true'); localStorage.setItem('propwire_active_sport', 'nfl'); } catch (_) { /* blocked */ } });
      const page = await context.newPage();
      await page.clock.setFixedTime(new Date(FIXED_TIME));
      await page.goto(ORIGIN + '/', { waitUntil: 'load' });
      await settle(page);
      if (s.before) { await s.before(page); await page.waitForTimeout(300); }
      await page.mouse.move(0, 0);
      const file = path.join(outDir, `${name}.png`);
      if (s.shot === 'full') {
        const h = await page.evaluate(() => document.documentElement.scrollHeight);
        await page.screenshot({ path: file, fullPage: true, clip: { x: 0, y: 0, width, height: Math.min(h, 3600) } });
      } else {
        await page.screenshot({ path: file });
      }
      const result = { name, ...(await assertions(page)) };
      if (s.name === 'home') {
        result.bottomBar = await bottomBarCheck(page);
        if (AXE) {
          await page.evaluate(() => window.scrollTo(0, 0));
          await page.addScriptTag({ path: AXE });
          const axe = await page.evaluate(async () => {
            const r = await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } });
            return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes.slice(0, 2).map((n) => n.target.join(' ')) }));
          });
          const serious = axe.filter((v) => v.impact === 'serious' || v.impact === 'critical');
          result.axe = { pass: serious.length === 0, serious, minor: axe.filter((v) => !serious.includes(v)).map((v) => `${v.id}(${v.impact})×${v.nodes}`) };
        }
      }
      for (const [k, v] of Object.entries(result)) if (v && typeof v === 'object' && v.pass === false) { failures += 1; console.log(`  FAIL ${name} ${k}: ${JSON.stringify(v).slice(0, 300)}`); }
      report.results.push(result);
      await context.close();
      process.stdout.write(`  captured ${name}\n`);
    }
  }
} finally {
  await browser.close();
  server.close();
}
fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
console.log(`${report.results.length} scenarios -> ${path.relative(ROOT, outDir)} (data @ ${DATA_REF.slice(0, 9)}, clock ${FIXED_TIME}); ${failures} failing assertions`);
process.exitCode = failures ? 1 : 0;
