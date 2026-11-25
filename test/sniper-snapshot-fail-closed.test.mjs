import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { buildSandbox, loadApp, VIEWER } from './helpers/sandbox.mjs';

// Issue 312: viewer/feeds/sniper_terminal_snapshot.json was static demo output
// (invented liquidity and edges). With /api/sniper/feed down, the market-alerts
// data loader must fail closed instead of falling back to any static file.

test('the demo sniper snapshot is gone from the viewer', () => {
  assert.equal(fs.existsSync(path.join(VIEWER, 'feeds', 'sniper_terminal_snapshot.json')), false);
  const app = fs.readFileSync(path.join(VIEWER, 'app.js'), 'utf8');
  assert.doesNotMatch(app, /sniper_terminal_snapshot/);
});

test('with the sniper API down, loadSniperTerminalSnapshot returns null and requests no snapshot', async () => {
  const { sandbox } = buildSandbox();
  const app = loadApp(sandbox);
  const requested = [];
  // Even if a snapshot file reappeared, nothing may fetch it: serve demo data for it
  // and 404 the API, then check it was never asked for.
  sandbox.fetch = async (url) => {
    requested.push(String(url));
    if (String(url).includes('sniper_terminal_snapshot')) {
      return { ok: true, status: 200, json: async () => ({ sniper_alerts: [{ player_name: 'Demo', edge_pct: 40.8 }] }) };
    }
    return { ok: false, status: 404, json: async () => { throw new Error('not found'); } };
  };
  app.state.sniperTerminalData = null;
  const data = await app.loadSniperTerminalSnapshot();
  assert.equal(data, null);
  assert.equal(app.state.sniperTerminalData, null);
  assert.deepEqual(requested, ['/api/sniper/feed']);
});

test('S, P and E no longer open the market-alerts dialog; Shift+L still logs a bet', () => {
  const app = fs.readFileSync(path.join(VIEWER, 'app.js'), 'utf8');
  const start = app.indexOf('const handleKeyNav');
  const body = app.slice(start, app.indexOf("window.addEventListener('keydown', handleKeyNav)", start));
  assert.ok(start > 0 && body.length > 0, 'keyboard handler found');
  assert.doesNotMatch(body, /openSniperDialog/);
  assert.doesNotMatch(body, /case '[sSpPeE]':/);
  assert.match(body, /if \(!e\.shiftKey\) break;[\s\S]*logBet\(playerId\)/);
});
