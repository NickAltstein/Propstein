// vercel.json git.deploymentEnabled: only main may create deployments.
//
// Vercel matches these keys with minimatch, where "*" does not cross "/".
// The old "*": false rule therefore left every branch with a slash
// (feat/..., fix/..., data/..., claude/...) creating a preview deployment:
// 28 of 81 deployments in one day, all cancelled by scripts/vercel_ignore.sh
// but still counted against the Hobby limit of 100 per day.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..');
const rules = JSON.parse(fs.readFileSync(path.join(ROOT, 'vercel.json'), 'utf8')).git.deploymentEnabled;

test('the catch-all rule uses "**" so it matches branch names with slashes', () => {
  assert.equal(rules['**'], false);
  assert.ok(!('*' in rules), '"*" does not match feat/x or data/x; use "**"');
});

test('main is the only branch allowed to deploy', () => {
  // Vercel: a branch matching several rules deploys if any rule is true.
  assert.equal(rules.main, true);
  const enabled = Object.entries(rules).filter(([, on]) => on === true).map(([k]) => k);
  assert.deepEqual(enabled, ['main']);
});
