import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const viewer = path.resolve('viewer');
const scriptTags = (html) => [...html.matchAll(/<script\b[^>]*>/gi)].map(([tag]) => tag);
const sources = (html) => scriptTags(html).map((tag) => /\bsrc=["']([^"']+)/i.exec(tag)?.[1]?.split('?')[0]).filter(Boolean);

function htmlFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? htmlFiles(file) : entry.name.endsWith('.html') ? [file] : [];
  });
}

function assertOrder(file, html, reference) {
  const scripts = sources(html);
  if (!scripts.includes('/app.js')) return;
  const required = reference.slice(0, reference.indexOf('/app.js') + 1);
  assert.deepEqual(scripts.slice(0, required.length), required, `${path.relative(viewer, file)} app prerequisites differ from index.html`);
  assert.equal(scripts.filter((script) => script === '/app.js').length, 1, `${file} loads app.js more than once`);
  for (const tag of scriptTags(html).filter((tag) => /\bsrc=/.test(tag))) {
    assert.match(tag, /\bdefer\b/, `${file} must preserve ordered deferred execution`);
  }
}

test('every viewer HTML file that loads app.js loads index.html prerequisites first', () => {
  const reference = sources(fs.readFileSync(path.join(viewer, 'index.html'), 'utf8'));
  assert.ok(reference.includes('/js/quant-math.js'));
  assert.ok(reference.indexOf('/js/quant-math.js') < reference.indexOf('/app.js'));
  for (const file of htmlFiles(viewer)) assertOrder(file, fs.readFileSync(file, 'utf8'), reference);
});

test('static order check rejects a removed quant-math prerequisite', () => {
  const file = path.join(viewer, 'audit.html');
  const html = fs.readFileSync(file, 'utf8');
  const reference = sources(fs.readFileSync(path.join(viewer, 'index.html'), 'utf8'));
  assert.throws(() => assertOrder(file, html.replace(/\s*<script src="\/js\/quant-math\.js[^>]*><\/script>/, ''), reference));
});
