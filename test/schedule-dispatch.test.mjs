// Vercel Schedules backstop: api/schedules/_core.mjs dispatch logic and the
// vercel.json / .vercelignore wiring it depends on. No network access.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import {
  SCHEDULE_WORKFLOWS, MAX_LATENESS_MS, SLOT_SLACK_MS, DispatchError, dispatchWorkflow, runSchedule, sendAlarm,
} from '../api/schedules/_core.mjs';

const ROOT = path.resolve(import.meta.dirname, '..');
const vercel = JSON.parse(fs.readFileSync(path.join(ROOT, 'vercel.json'), 'utf8'));
const quiet = { log() {}, warn() {}, error() {} };
const NOW = new Date('2026-11-24T19:07:30Z');

// Fake GitHub + webhook. `runs` answers the run lookup; `dispatch` is the
// status of the workflow_dispatch POST; `hook` is the webhook's status.
function fakeFetch(dispatch = 204, body = '', { runs = [], lookup = 200, hook = 204 } = {}) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    if (url.startsWith('https://hooks.example/')) return { status: hook, text: async () => '' };
    if (url.includes('/runs?')) {
      return { status: lookup, json: async () => ({ workflow_runs: runs }), text: async () => body };
    }
    return { status: dispatch, text: async () => body };
  };
  const of = (kind) => calls.filter((c) => (kind === 'hook' ? c.url.startsWith('https://hooks.example/')
    : kind === 'lookup' ? c.url.includes('/runs?') : c.url.endsWith('/dispatches')));
  return { calls, impl, of };
}
const ENV = { GITHUB_DISPATCH_TOKEN: 'tok', ALARM_WEBHOOK_URL: 'https://hooks.example/abc' };

function meta(name, scheduledAt = '2026-11-24T19:07:00Z') {
  return { name, scheduledAt: new Date(scheduledAt), executionId: 'exec-1', scheduleId: 's', namespace: 'default' };
}

test('each schedule dispatches its workflow on main with a scoped token', async () => {
  const f = fakeFetch();
  const result = await runSchedule(meta('odds-freshness'), {
    env: ENV, now: NOW, fetchImpl: f.impl, log: quiet,
  });
  assert.equal(result.status, 'dispatched');
  const [post] = f.of('dispatch');
  assert.equal(f.of('dispatch').length, 1);
  assert.equal(post.url,
    'https://api.github.com/repos/NickAltstein/Propstein/actions/workflows/odds_freshness.yml/dispatches');
  assert.equal(post.init.method, 'POST');
  assert.equal(post.init.headers.Authorization, 'Bearer tok');
  assert.deepEqual(JSON.parse(post.init.body), { ref: 'main' });
  assert.equal(f.of('hook').length, 0, 'a successful dispatch pages nobody');
});

test('a missing token fails the run and pages the alarm webhook', async () => {
  const f = fakeFetch();
  await assert.rejects(
    runSchedule(meta('odds-freshness'), {
      env: { ALARM_WEBHOOK_URL: ENV.ALARM_WEBHOOK_URL }, now: NOW, fetchImpl: f.impl, log: quiet,
    }),
    /GITHUB_DISPATCH_TOKEN is not set/);
  assert.equal(f.of('dispatch').length, 0);
  const [hook] = f.of('hook');
  const payload = JSON.parse(hook.init.body);
  assert.match(payload.content, /SCHEDULE DISPATCH FAILED: odds-freshness -> odds_freshness\.yml/);
  assert.equal(payload.text, payload.content);
});

for (const status of [401, 403, 404, 422, 500]) {
  test(`a GitHub ${status} on dispatch pages the webhook without the token or URL`, async () => {
    const f = fakeFetch(status, '{"message":"Bad credentials"}');
    await assert.rejects(runSchedule(meta('close-quality-capture'), {
      env: { ...ENV, GITHUB_DISPATCH_TOKEN: 'secret-token' }, now: NOW, fetchImpl: f.impl, log: quiet,
    }), new RegExp(`HTTP ${status}`));
    const [hook] = f.of('hook');
    assert.ok(hook, 'webhook was not called');
    assert.ok(!hook.init.body.includes('secret-token'));
    assert.ok(!hook.init.body.includes('hooks.example'));
  });
}

test('an expired token fails at the run lookup and still pages', async () => {
  const f = fakeFetch(204, '{"message":"Bad credentials"}', { lookup: 401 });
  await assert.rejects(runSchedule(meta('odds-freshness'), {
    env: ENV, now: NOW, fetchImpl: f.impl, log: quiet,
  }), /run lookup for odds_freshness\.yml failed: HTTP 401/);
  assert.equal(f.of('dispatch').length, 0);
  assert.equal(f.of('hook').length, 1);
});

test('a broken webhook never masks the dispatch failure', async () => {
  const f = fakeFetch(403, '', { hook: 500 });
  await assert.rejects(runSchedule(meta('odds-freshness'), {
    env: ENV, now: NOW, fetchImpl: f.impl, log: quiet,
  }), /HTTP 403/);
  const errors = [];
  assert.equal(await sendAlarm('x', { env: {}, fetchImpl: f.impl, log: { error: (m) => errors.push(m) } }), false);
  assert.match(errors[0], /ALARM_WEBHOOK_URL is not set/);
});

test('a slot already covered by a queued, running or successful run is not dispatched again', async () => {
  for (const run of [
    { id: 1, status: 'queued', event: 'schedule' },
    { id: 2, status: 'in_progress', event: 'schedule' },
    { id: 3, status: 'completed', conclusion: 'success', event: 'schedule' },
  ]) {
    const f = fakeFetch(204, '', { runs: [run] });
    const result = await runSchedule(meta('game-context-daily', '2026-11-24T10:30:00Z'), {
      env: ENV, now: new Date('2026-11-24T10:44:00Z'), fetchImpl: f.impl, log: quiet,
    });
    assert.equal(result.status, 'skipped_covered', run.status);
    assert.equal(result.run_id, run.id);
    assert.equal(f.of('dispatch').length, 0);
  }
});

test('a failed or cancelled run in the slot does not cover it', async () => {
  const f = fakeFetch(204, '', { runs: [
    { id: 4, status: 'completed', conclusion: 'failure' },
    { id: 5, status: 'completed', conclusion: 'cancelled' },
  ] });
  const result = await runSchedule(meta('game-context-daily', '2026-11-24T10:30:00Z'), {
    env: ENV, now: new Date('2026-11-24T10:44:00Z'), fetchImpl: f.impl, log: quiet,
  });
  assert.equal(result.status, 'dispatched');
});

test('the run lookup is anchored to the slot time, not to delivery time', async () => {
  // Hobby jitter delivers the 10:30 slot at 10:44; a GitHub cron run created
  // at 10:31 must still be found, and the previous slot's runs must not.
  const f = fakeFetch();
  await runSchedule(meta('game-context-daily', '2026-11-24T10:30:00Z'), {
    env: ENV, now: new Date('2026-11-24T10:44:00Z'), fetchImpl: f.impl, log: quiet,
  });
  const lookup = new URL(f.of('lookup')[0].url);
  const since = new Date(new Date('2026-11-24T10:30:00Z').getTime() - SLOT_SLACK_MS);
  assert.equal(lookup.searchParams.get('created'), `>=${since.toISOString().replace('.000Z', 'Z')}`);
  assert.ok(SLOT_SLACK_MS < 15 * 60 * 1000, 'slack must stay well under the shortest cadence');
});

test('a GitHub rejection fails the run and never echoes the token', async () => {
  const f = fakeFetch(403, '{"message":"Resource not accessible by personal access token"}');
  await assert.rejects(
    dispatchWorkflow('odds_freshness.yml', { token: 'secret-token', fetchImpl: f.impl }),
    (err) => err instanceof DispatchError && /HTTP 403/.test(err.message) && !err.message.includes('secret-token'));
  assert.equal(f.of('dispatch').length, 1);
});

test('an unknown schedule name fails loudly', async () => {
  await assert.rejects(
    runSchedule(meta('mystery'), { env: ENV, now: NOW, fetchImpl: fakeFetch().impl, log: quiet }),
    /unknown schedule/);
});

test('occurrences delivered late after an outage are skipped, not replayed', async () => {
  const f = fakeFetch();
  const late = new Date(NOW.getTime() - MAX_LATENESS_MS - 60_000).toISOString();
  const result = await runSchedule(meta('close-quality-capture', late), {
    env: ENV, now: NOW, fetchImpl: f.impl, log: quiet,
  });
  assert.equal(result.status, 'skipped_stale');
  assert.equal(f.calls.length, 0);
  // Within the jitter window still dispatches.
  const onTime = new Date(NOW.getTime() - 14 * 60_000).toISOString();
  const ok = await runSchedule(meta('close-quality-capture', onTime), {
    env: ENV, now: NOW, fetchImpl: f.impl, log: quiet,
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

test('the deploy gate sweep dispatches deploy_gate.yml on main with sweep=true', async () => {
  const f = fakeFetch();
  const result = await runSchedule(meta('deploy-gate-sweep', '2026-11-24T19:41:00Z'), {
    env: ENV, now: new Date('2026-11-24T19:44:00Z'), fetchImpl: f.impl, log: quiet,
  });
  assert.equal(result.status, 'dispatched');
  const [post] = f.of('dispatch');
  assert.match(post.url, /\/workflows\/deploy_gate\.yml\/dispatches$/);
  assert.deepEqual(JSON.parse(post.init.body), { ref: 'main', inputs: { sweep: 'true' } });
  assert.equal(vercel.schedules['deploy-gate-sweep'].schedule, '41 * * * *');
});

test('a push-triggered gate run in the slot does not stand in for the sweep', async () => {
  const f = fakeFetch(204, '', { runs: [{ id: 7, status: 'completed', conclusion: 'success', event: 'push' }] });
  const result = await runSchedule(meta('deploy-gate-sweep', '2026-11-24T19:41:00Z'), {
    env: ENV, now: new Date('2026-11-24T19:44:00Z'), fetchImpl: f.impl, log: quiet,
  });
  assert.equal(result.status, 'dispatched');
});

test('a GitHub cron sweep already in the slot covers it', async () => {
  const f = fakeFetch(204, '', { runs: [{ id: 8, status: 'in_progress', event: 'schedule' }] });
  const result = await runSchedule(meta('deploy-gate-sweep', '2026-11-24T19:41:00Z'), {
    env: ENV, now: new Date('2026-11-24T19:44:00Z'), fetchImpl: f.impl, log: quiet,
  });
  assert.equal(result.status, 'skipped_covered');
});

test('the deploy gate workflow turns the sweep input into --sweep', () => {
  const yml = fs.readFileSync(path.join(ROOT, '.github/workflows/deploy_gate.yml'), 'utf8');
  assert.match(yml, /sweep:\n\s+description:/);
  assert.match(yml, /SWEEP: \$\{\{ github\.event_name == 'workflow_dispatch' && inputs\.sweep \}\}/);
  assert.match(yml, /\[ "\$SWEEP" = "true" \]; then\n\s+python3 scripts\/deploy_gate\.py --sweep/);
});
