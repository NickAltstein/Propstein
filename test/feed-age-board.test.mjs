import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSandbox, loadApp } from './helpers/sandbox.mjs';

test('critical feed status hides edges before prices are shown as current', async () => {
  const { sandbox, registry } = buildSandbox();
  const classes = new Set();
  sandbox.document.body.classList = {
    add(name) { classes.add(name); },
    remove(name) { classes.delete(name); },
    toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
    contains(name) { return classes.has(name); },
  };
  const original = sandbox.fetch;
  sandbox.fetch = async (raw) => {
    const clean = String(raw).split('?')[0];
    if (clean.endsWith('feeds/feed_status.json')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          sports: { nfl: { fail_closed: true, stale_since: '2026-11-07T19:52:16Z', show_edges: false } },
        }),
      };
    }
    return original(raw);
  };
  const app = loadApp(sandbox);
  await app.loadSlate(app.defaultBootSlate());
  assert.equal(app.state.feedFailClosed, true);
  assert.ok(classes.has('feed-fail-closed'));
  assert.match(registry.get('#boardPageStatus').textContent, /NFL prices stale since 2026-11-07T19:52:16Z/);
  const list = registry.get('#homeEdgeList').innerHTML;
  assert.match(list, /No edges/);
  assert.doesNotMatch(list, /%\s*EV|verified edge/i);
  assert.ok((app.state.odds.quotes || []).every((quote) => !quote.evaluation));
});
