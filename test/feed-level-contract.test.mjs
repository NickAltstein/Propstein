// Contract: the feed levels scripts/feed_age.py can write must exactly match the
// levels viewer/r/tailcheck.js knows about (FEED_LEVELS: healthy, paused or
// error). The tail check already fails closed on any unknown level, so a new or
// renamed level would silently hide every quote as an "error". This test turns
// that into a loud failure: whoever adds or renames a level must decide here
// whether it serves quotes, pauses them, or is an error.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const ROOT = path.resolve(import.meta.dirname, '..');

function loadTailCheck() {
  const win = { PropSteinMath: null, PropsteinTail: null, fetch: async () => { throw new Error('no network'); } };
  const ctx = vm.createContext({
    window: win, fetch: win.fetch, Date, Math, Number, String, Array, Object, Set, Map, Promise, Error, RegExp, JSON, console,
  });
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'viewer/js/quant-math.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'viewer/r/tailcheck.js'), 'utf8'), ctx);
  return win.PropsteinTail;
}

function levelsWrittenByFeedAge() {
  const src = fs.readFileSync(path.join(ROOT, 'scripts/feed_age.py'), 'utf8');
  return [...new Set([...src.matchAll(/\blevel\s*=\s*"([a-z_]+)"/g)].map((m) => m[1]))].sort();
}

test('feed_age.py writes exactly the levels the tail check knows about', () => {
  const written = levelsWrittenByFeedAge();
  // Guard against the regex silently finding nothing after a refactor.
  assert.ok(written.includes('ok') && written.length >= 4, `parsed levels from feed_age.py: ${written}`);
  const known = Object.keys(loadTailCheck().FEED_LEVELS).sort();
  assert.deepEqual(known, written,
    'feed_age.py levels and tailcheck FEED_LEVELS differ: add the level to FEED_LEVELS as healthy, paused or error');
});

test('only `ok` serves quotes; every level is healthy, paused or error', () => {
  const levels = loadTailCheck().FEED_LEVELS;
  for (const [level, kind] of Object.entries(levels)) {
    assert.ok(['healthy', 'paused', 'error'].includes(kind), `${level}: unknown kind ${kind}`);
  }
  assert.deepEqual(Object.keys(levels).filter((l) => levels[l] === 'healthy'), ['ok']);
  assert.equal(levels.degraded, 'paused');
  assert.equal(levels.suppressed, 'paused');
  assert.equal(levels.critical, 'error');
  assert.ok(!('live' in levels) && !('healthy' in levels), 'levels nothing writes must not be accepted');
});
