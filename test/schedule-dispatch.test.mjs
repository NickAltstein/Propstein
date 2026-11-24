// Vercel Schedules backstop: api/schedules/_core.mjs dispatch logic and the
// vercel.json / .vercelignore wiring it depends on. No network access.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import {
  SCHEDULE_WORKFLOWS, MAX_LATENESS_MS, DispatchError, dispatchWorkflow, runSchedule,
} from '../api/schedules/_core.mjs';

const ROOT = path.resolve(import.meta.dirname, '..');
const vercel = JSON.parse(fs.readFileSync(path.join(ROOT, 'vercel.json'), 'utf8'));
const quiet = { log() {}, warn() {} };
const NOW = new Date('2026-11-24T19:07:30Z');

function fakeFetch(status = 204, body = '') {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    return { status, text: async () => body };
  };
  return { calls, impl };
}

function meta(name, scheduledAt = '2026-11-24T19:07:00Z') {
  return { name, scheduledAt: new Date(scheduledAt), executionId: 'exec-1', scheduleId: 's', namespace: 'default' };
}

test('each schedule dispatches its workflow on main with a scoped token', async () => {
  const f = fakeFetch();
  const result = await runSchedule(meta('odds-freshness'), {
    env: { GITHUB_DISPATCH_TOKEN: 'tok' }, now: NOW, fetchImpl: f.impl, log: quiet,
  });
  assert.equal(result.status, 'dispatched');
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url,
    'https://api.github.com/repos/NickAltstein/Propstein/actions/workflows/odds_freshness.yml/dispatches');
  assert.equal(f.calls[0].init.method, 'POST');
  assert.equal(f.calls[0].init.headers.Authorization, 'Bearer tok');
  assert.deepEqual(JSON.parse(f.calls[0].init.body), { ref: 'main' });
});

test('a missing token fails the run instead of succeeding quietly', async () => {
  const f = fakeFetch();
  await assert.rejects(
    runSchedule(meta('odds-freshness'), { env: {}, now: NOW, fetchImpl: f.impl, log: quiet }),
    /GITHUB_DISPATCH_TOKEN is not set/);
  assert.equal(f.calls.length, 0);
});

test('a GitHub rejection fails the run and never echoes the token', async () => {
  const f = fakeFetch(403, '{"message":"Resource not accessible by personal access token"}');
  await assert.rejects(
    dispatchWorkflow('odds_freshness.yml', { token: 'secret-token', fetchImpl: f.impl }),
    (err) => err instanceof DispatchError && /HTTP 403/.test(err.message) && !err.message.includes('secret-token'));
});

test('an unknown schedule name fails loudly', async () => {
  await assert.rejects(
    runSchedule(meta('mystery'), { env: { GITHUB_DISPATCH_TOKEN: 't' }, now: NOW, fetchImpl: fakeFetch().impl, log: quiet }),
    /unknown schedule/);
});

test('occurrences delivered late after an outage are skipped, not replayed', async () => {
  const f = fakeFetch();
  const late = new Date(NOW.getTime() - MAX_LATENESS_MS - 60_000).toISOString();
  const result = await runSchedule(meta('close-quality-capture', late), {
    env: { GITHUB_DISPATCH_TOKEN: 't' }, now: NOW, fetchImpl: f.impl, log: quiet,
  });
  assert.equal(result.status, 'skipped_stale');
  assert.equal(f.calls.length, 0);
  // Within the jitter window still dispatches.
  const onTime = new Date(NOW.getTime() - 14 * 60_000).toISOString();
  const ok = await runSchedule(meta('close-quality-capture', onTime), {
    env: { GITHUB_DISPATCH_TOKEN: 't' }, now: NOW, fetchImpl: f.impl, log: quiet,
  });
  assert.equal(ok.status, 'dispatched');
});

test('vercel.json schedules and the dispatch map agree', () => {
  assert.deepEqual(Object.keys(vercel.schedules).sort(), Object.keys(SCHEDULE_WORKFLOWS).sort());
  for (const [name, entry] of Object.entries(vercel.schedules)) {
    assert.equal(entry.function, 'api/schedules/dispatch', name);
    assert.ok(!('path' in entry), `${name} must use a private function target, not a public path`);
    assert.match(entry.schedule, /^[0-9*,\-/ ]+$/, name);
  }
  const fn = vercel.functions['api/schedules/dispatch.mjs'];
  assert.deepEqual(fn.experimentalTriggers, [{ type: 'schedule/v1beta' }]);
});

test('schedules stay legal on Hobby: no two runs closer than the 15-minute jitter', () => {
  const minutesOf = (field) => field.split(',').map(Number);
  for (const [name, { schedule }] of Object.entries(vercel.schedules)) {
    const minuteField = schedule.split(' ')[0];
    if (!/^[0-9,]+$/.test(minuteField)) continue;
    const mins = minutesOf(minuteField).sort((a, b) => a - b);
    if (mins.length < 2) continue;
    const gaps = mins.map((m, i) => ((mins[(i + 1) % mins.length] - m + 60) % 60) || 60);
    assert.ok(Math.min(...gaps) >= 15, `${name} has a gap under 15 minutes: ${schedule}`);
  }
});

test('every dispatched workflow accepts workflow_dispatch', () => {
  for (const file of new Set(Object.values(SCHEDULE_WORKFLOWS))) {
    const yml = fs.readFileSync(path.join(ROOT, '.github/workflows', file), 'utf8');
    assert.match(yml, /^\s*workflow_dispatch:/m, `${file} has no workflow_dispatch trigger`);
    assert.match(yml, /^concurrency:\n\s+group: .+\n\s+cancel-in-progress: false/m,
      `${file} must queue overlapping runs (cancel-in-progress: false)`);
  }
});

test('.vercelignore deploys only the dispatch function from api/', () => {
  const lines = fs.readFileSync(path.join(ROOT, '.vercelignore'), 'utf8').split('\n').map((l) => l.trim());
  // The Python backend stays out of the Vercel upload...
  assert.ok(lines.includes('/api/*.py'));
  assert.ok(lines.includes('/api/__pycache__/'));
  // ...without hiding the dispatch function. .vercelignore does not honour
  // "!" re-includes, so a whole-directory rule would drop it.
  for (const rule of ['/api/', '/api/*', '/api/**', 'api/']) {
    assert.ok(!lines.includes(rule), `${rule} would drop the dispatch function`);
  }
  assert.ok(!lines.some((l) => l.startsWith('/api/schedules')), 'api/schedules/ must not be ignored');
});
