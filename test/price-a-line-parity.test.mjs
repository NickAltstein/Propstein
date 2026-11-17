import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { buildSandbox, loadApp } from './helpers/sandbox.mjs';

const ROOT = path.resolve(import.meta.dirname, '..');
const fixturePath = path.join(ROOT, 'test/fixtures/price-a-line-parity.json');
const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

function extractMetrics(pickResultEl, pickLineEl) {
  const html = pickResultEl.innerHTML;
  const fairLineMatch = html.match(/<dt>Fair line<\/dt>\s*<dd>([^<]+)<\/dd>/i);
  const projVolMatch = html.match(/<dt>Projected volume<\/dt>\s*<dd>([^<]+)<\/dd>/i);
  const overMatch = html.match(/<dt>Over<\/dt>\s*<dd>([0-9.]+%)\s*<span>([^<]+)<\/span><\/dd>/i);
  const underMatch = html.match(/<dt>Under<\/dt>\s*<dd>([0-9.]+%)\s*<span>([^<]+)<\/span><\/dd>/i);
  const breakEvenMatch = html.match(/<dt>Break-even<\/dt>\s*<dd>([0-9.]+)%<\/dd>/i);
  const caveatMatch = html.match(/<p class="pick-caveat">([^<]+)<\/p>/i);
  const bookMatch = html.match(/<p class="pick-book">([^<]+)<\/p>/i);

  return {
    line: pickLineEl.value,
    fairLine: fairLineMatch ? fairLineMatch[1].trim() : null,
    projectedVolume: projVolMatch ? projVolMatch[1].trim() : null,
    overProb: overMatch ? overMatch[1].trim() : null,
    overVsBreakEven: overMatch ? overMatch[2].trim() : null,
    underProb: underMatch ? underMatch[1].trim() : null,
    underVsBreakEven: underMatch ? underMatch[2].trim() : null,
    breakEven: breakEvenMatch ? breakEvenMatch[1].trim() : null,
    bookLine: bookMatch ? bookMatch[1].trim() : null,
    caveat: caveatMatch ? caveatMatch[1].trim() : null,
  };
}

test('Price-a-Line panel outputs are byte-identical across fixture scenarios', async () => {
  const { sandbox, registry } = buildSandbox();
  const P = loadApp(sandbox);
  await P.loadSlate(fixture.slate || '2026_w7');

  const pickGame = registry.get('#pickGame');
  const pickPlayer = registry.get('#pickPlayer');
  const pickMarket = registry.get('#pickMarket');
  const pickLine = registry.get('#pickLine');
  const pickBreakeven = registry.get('#pickBreakeven');
  const pickResult = registry.get('#pickResult');

  for (const c of fixture.cases) {
    if (c.inputs.game) pickGame.value = c.inputs.game;
    if (c.inputs.player) pickPlayer.value = c.inputs.player;
    if (c.inputs.market) pickMarket.value = c.inputs.market;
    if (c.inputs.breakEven) pickBreakeven.value = c.inputs.breakEven;
    if (c.inputs.line !== undefined) pickLine.value = c.inputs.line;
    if (c.inputs.lineTouched !== undefined) P.state.pickLineTouched = c.inputs.lineTouched;

    P.renderPickFlow();
    const actual = extractMetrics(pickResult, pickLine);

    assert.deepEqual(
      actual,
      c.expectedOutputs,
      `Scenario "${c.name}" must produce byte-identical outputs`
    );
  }
});
