import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

test('viewer filters CONTEXT_MISSING rows from state.slate and renders unpriced banner', () => {
  const appJs = fs.readFileSync(path.resolve('viewer/app.js'), 'utf8');
  assert.match(appJs, /state\.contextMissingGames\s*=\s*Array\.isArray\(slate\)/);
  assert.match(appJs, /row\.context_status\s*!==\s*'CONTEXT_MISSING'/);
  assert.match(appJs, /data-context-missing-banner/);
  assert.match(appJs, /unpriced: context missing/);
});
