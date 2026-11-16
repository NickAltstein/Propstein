import test from 'node:test';
import assert from 'node:assert/strict';
import status from '../viewer/js/feed-status.js';

const closed = {
  sports: {
    nba: { fail_closed: true, stale_since: '2026-11-07T19:52:16Z', show_edges: false },
    nfl: { fail_closed: false, stale_since: null, show_edges: true },
  },
};

test('critical NBA state greys the banner and removes edges before render', () => {
  const classes = new Set();
  const banner = { textContent: '' };
  const doc = {
    body: { classList: { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); } } },
    querySelector(sel) { return sel === '#boardPageStatus' ? banner : null; },
  };
  const applied = status.apply(doc, closed, 'nba');
  assert.equal(applied.failClosed, true);
  assert.equal(applied.showEdges, false);
  assert.equal(applied.label, 'NBA prices stale since 2026-11-07T19:52:16Z');
  assert.equal(banner.textContent, applied.label);
  assert.ok(classes.has('feed-fail-closed'));
  const quotes = status.stripEdges([{ line: 14.5, evaluation: { prob_edge: 0.2 } }]);
  assert.equal(quotes[0].line, 14.5);
  assert.equal(quotes[0].evaluation, undefined);
});

test('a healthy sport does not fail closed', () => {
  assert.equal(status.isFailClosed(closed, 'nfl'), false);
  assert.equal(status.staleLabel(closed, 'nfl'), '');
});
