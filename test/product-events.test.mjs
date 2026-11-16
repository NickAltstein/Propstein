import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test('product events aggregate without identifiers or URLs', () => {
  const dir = mkdtempSync(join(tmpdir(), 'propstein-events-'));
  try {
    const code = `
import importlib.util,sqlite3,json,os
s=importlib.util.spec_from_file_location('events','src/observability/product_events.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.record_event('widget_load','research');m.record_event('widget_load','research')
for event,operator in [('fake_event',''),('price_rail_click','unapproved')]:
 try:m.record_event(event,'research',operator)
 except ValueError:pass
 else:raise AssertionError('invalid event accepted')
with sqlite3.connect(os.environ['PROPSTEIN_PRODUCT_EVENTS_DB']) as db:
 rows=db.execute('select event,tier,operator,count from daily_counts').fetchall()
print(json.dumps(rows))`;
    const out = execFileSync('python3', ['-c', code], {
      encoding: 'utf8', env: { ...process.env, PROPSTEIN_PRODUCT_EVENTS_DB: join(dir, 'events.sqlite3') }
    });
    assert.deepEqual(JSON.parse(out), [['widget_load', 'research', '', 2]]);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
