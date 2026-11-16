import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';

const script = `
import importlib.util, json
from datetime import datetime, timezone
spec = importlib.util.spec_from_file_location('monetization', 'src/services/monetization.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
now = datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)
q = {'book':'Book A','verification_status':'VERIFIED','content_hash':'a'*64,
     'observed_at':'2026-11-16T19:59:00Z',
     'evaluation':{'is_qualified':True,'prob_edge':0.11}}
feed = {'feed_status':'LIVE_VERIFIED','updated_at':'2026-11-16T19:59:00Z',
        'quotes':[q, {**q,'book':'Book B'}]}
cases = {
 'healthy': m.readiness(feed, now=now),
 'stale': m.readiness({**feed,'updated_at':'2026-11-16T19:40:00Z'}, now=now),
 'unverified': m.readiness({**feed,'quotes':[{**q,'verification_status':'OBSERVED'}]}, now=now),
 'bad_hash': m.readiness({**feed,'quotes':[{**q,'content_hash':'bad'}]}, now=now),
 'zero_qualified': m.readiness({**feed,'quotes':[{**q,'evaluation':{'is_qualified':False,'prob_edge':0.11}}]}, now=now),
 'missing': m.readiness({}, now=now),
 'feature_free': m.allowed('methodology','research'),
 'feature_paid': m.allowed('live_board','research'),
 'checkout': m.checkout_ready(),
}
print(json.dumps(cases))
`;

test('monetization readiness fails closed and leaves transparency free', () => {
  const out = execFileSync('python3', ['-c', script], { encoding: 'utf8' });
  const c = JSON.parse(out);
  assert.equal(c.healthy.ready, true);
  for (const key of ['stale', 'unverified', 'bad_hash', 'zero_qualified', 'missing']) {
    assert.equal(c[key].ready, false, key);
  }
  assert.equal(c.feature_free, true);
  assert.equal(c.feature_paid, false);
  assert.equal(c.checkout.ready, false);
  assert.equal(c.checkout.checks.prospective_evidence, false);
  assert.equal(c.checkout.checks.checkout_configured, false);
});
