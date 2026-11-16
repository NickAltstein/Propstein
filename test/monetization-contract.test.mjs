import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import sharp from 'sharp';
import { fileURLToPath } from 'node:url';

const read = async (path) => readFile(new URL(path, import.meta.url), 'utf8');

test('commercial configuration cannot publish prices or operator links', async () => {
  const policy = JSON.parse(await read('../config/monetization.json'));
  const geo = JSON.parse(await read('../viewer/operator-jurisdictions.json'));
  const html = await read('../viewer/index.html');
  const app = await read('../viewer/app.js');
  assert.equal(policy.checkout_enabled, false);
  assert.deepEqual(geo.states, {});
  assert.deepEqual(geo.operators, {});
  assert.doesNotMatch(html, /data-checkout-tier/);
  assert.match(app, /No operator links are enabled/);
  assert.doesNotMatch(app.slice(app.indexOf('function priceRailMarkup'), app.indexOf('function fairValue')), /href=/);
});

test('widget is framed while the terminal keeps its frame restriction', async () => {
  const config = JSON.parse(await read('../vercel.json'));
  const main = config.headers[0];
  const widget = config.headers[1];
  assert.match(main.source, /\?!widget\/line-pricer/);
  assert.match(main.headers.find(h => h.key === 'Content-Security-Policy').value, /frame-ancestors 'none'/);
  assert.equal(main.headers.find(h => h.key === 'X-Frame-Options').value, 'DENY');
  assert.equal(widget.source, '/widget/line-pricer/');
  assert.match(widget.headers.find(h => h.key === 'Content-Security-Policy').value, /frame-ancestors \*/);
  assert.equal(widget.headers.some(h => h.key === 'X-Frame-Options'), false);
});

test('settled share set includes losses and verifiable-sized PNGs', async () => {
  const manifest = JSON.parse(await read('../viewer/share/audit/manifest.json'));
  assert.equal(manifest.cards.length, manifest.record_count);
  assert.ok(manifest.cards.some(c => c.outcome === 'LOSS'));
  for (const card of manifest.cards) {
    assert.match(card.record_sha256, /^[0-9a-f]{64}$/);
    assert.ok(card.model_range && card.model_range.p10 < card.model_range.p90);
  }
  const image = await sharp(fileURLToPath(new URL('../viewer/share/audit/result-004.png', import.meta.url))).metadata();
  assert.equal(image.width, 1200);
  assert.equal(image.height, 630);
});
