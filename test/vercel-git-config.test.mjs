// vercel.json git.deploymentEnabled: Vercel creates no deployments from git.
//
// Every push used to create a deployment: previews for branches with a "/"
// (a "*" rule never matched them) and a production deployment for every
// data-bot merge to main, most cancelled by scripts/vercel_ignore.sh but
// still counted against the Hobby limit of 100 per day. Production deploys
// now go only through the deploy gate (.github/workflows/deploy_gate.yml),
// which calls a deploy hook when served files change.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..');
const config = JSON.parse(fs.readFileSync(path.join(ROOT, 'vercel.json'), 'utf8'));

test('automatic git deployments are off for every branch', () => {
  assert.equal(config.git.deploymentEnabled, false);
});

test('the deploy gate is what deploys production', () => {
  const gate = fs.readFileSync(path.join(ROOT, '.github/workflows/deploy_gate.yml'), 'utf8');
  assert.match(gate, /branches: \[main\]/);
  assert.match(gate, /secrets\.VERCEL_DEPLOY_HOOK_URL/);
});

test('the deprecated github.enabled key is not used', () => {
  // Vercel's deploy-hook docs: hooks do not fire with github.enabled = false.
  assert.ok(!('github' in config), 'use git.deploymentEnabled, never github.enabled');
});
