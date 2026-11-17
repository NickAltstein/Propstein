import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const root = path.resolve(import.meta.dirname, '..');
const require = createRequire(import.meta.url);
const { deriveMarketStatus } = require('../viewer/js/market-status.js');
const windows = JSON.parse(fs.readFileSync(path.join(root, 'viewer/verification_windows.json'), 'utf8'));
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/status-pr1-feed.json'), 'utf8'));
const gameStates = { '2026_10_LAC_BAL': { lifecycle: 'started', startAt: '2026-11-17T01:15:00Z' } };
const market = 'receiving_yards';
const policy = (extra = {}) => ({ verificationWindows: windows, gameStates, market, allCount: 21,
  qualifications: { '00-0039064::receiving_yards': true, '00-0038544::receiving_yards': true }, ...extra });
const quote = (overrides = {}) => ({ player_id: 'p1', game_id: 'g1', market,
  quoted_at: '2026-11-17T00:00:00Z', observed_at: '2026-11-17T00:00:00Z',
  verified: true, verification_status: 'VERIFIED', verification_receipt_id: 'r1', ...overrides });

test('real 9:50 PM fixture resolves to event started and zero opportunities', () => {
  const result = deriveMarketStatus(fixture, Date.parse('2026-11-17T02:50:00Z'), policy());
  assert.equal(result.presentation, 'started');
  assert.equal(result.label, 'Event started · pregame research archived');
  assert.equal(result.verifiedCount, 0);
  assert.equal(result.topCount, 21);
  assert.equal(result.allCount, 21);
  assert.ok(result.offers.every((offer) => !offer.opportunity));
});

test('Top reports ranked rows and never exceeds All', () => {
  const now = Date.parse('2026-11-17T02:50:00Z');
  assert.equal(deriveMarketStatus(fixture, now, policy({ topCount: 5 })).topCount, 5);
  assert.equal(deriveMarketStatus(fixture, now, policy({ topCount: 40 })).topCount, 21);
});

test('pre-kickoff clock resolves the same captured feed to snapshot only', () => {
  const result = deriveMarketStatus(fixture, Date.parse('2026-11-16T23:30:00Z'),
    policy({ gameStates: { '2026_10_LAC_BAL': { lifecycle: 'upcoming', startAt: '2026-11-17T01:15:00Z' } } }));
  assert.equal(result.presentation, 'snapshot');
  assert.equal(result.verifiedCount, 0);
  assert.match(result.label, /Snapshot only · Nov 16, 3:55 PM ET/);
  assert.equal(result.relativeAge, '2h ago');
  assert.match(result.label, /3:55 PM ET · 2h ago/);
  assert.ok(result.offers.every((offer) => offer.freshness === 'unknown'));
});

test('fresh verified qualifying quote is counted only for an upcoming game', () => {
  const payload = { updated_at: '2026-11-17T00:00:00Z', quotes: [quote()] };
  const p = policy({ gameStates: { g1: { lifecycle: 'upcoming', startAt: '2026-11-17T03:15:00Z' } },
    qualifications: { 'p1::receiving_yards': true }, allCount: 1 });
  const result = deriveMarketStatus(payload, Date.parse('2026-11-17T00:10:00Z'), p);
  assert.equal(result.verifiedCount, 1);
  assert.equal(result.presentation, 'opportunities');
  assert.equal(result.offers[0].freshness, 'current');
});

test('stale, unknown-time, and receipt-only quotes remain inspectable but not counted', () => {
  const payload = { updated_at: '2026-11-17T00:00:00Z', quotes: [
    quote({ player_id: 'stale', quoted_at: '2026-11-16T20:00:00Z' }),
    quote({ player_id: 'unknown', quoted_at: undefined }),
    quote({ player_id: 'matched', verified: false, verification_status: 'OBSERVED' })
  ] };
  const p = policy({ gameStates: { g1: { lifecycle: 'upcoming', startAt: '2026-11-17T03:15:00Z' } },
    qualifications: Object.fromEntries(['stale', 'unknown', 'matched'].map((id) => [`${id}::receiving_yards`, true])) });
  const result = deriveMarketStatus(payload, Date.parse('2026-11-17T00:10:00Z'), p);
  assert.equal(result.verifiedCount, 0);
  assert.equal(result.offers.length, 3);
  assert.equal(result.offers[0].freshness, 'stale');
  assert.equal(result.offers[1].freshness, 'unknown');
  assert.equal(result.offers[2].verification, 'unverified');
});

test('mixed current and stale offers have per-offer status and no global all-clear', () => {
  const payload = { updated_at: '2026-11-17T00:00:00Z', quotes: [
    quote(), quote({ player_id: 'p2', quoted_at: '2026-11-16T20:00:00Z' })
  ] };
  const p = policy({ gameStates: { g1: { lifecycle: 'upcoming', startAt: '2026-11-17T03:15:00Z' } },
    qualifications: { 'p1::receiving_yards': true, 'p2::receiving_yards': true } });
  const result = deriveMarketStatus(payload, Date.parse('2026-11-17T00:10:00Z'), p);
  assert.equal(result.verifiedCount, 1);
  assert.deepEqual(result.offers.map((offer) => offer.freshness), ['current', 'stale']);
  assert.equal(result.presentation, 'snapshot');
});

test('ingestion and validation times never replace provider quote time', () => {
  const original = quote({ quoted_at: '2026-11-16T20:00:00Z' });
  const payload = { updated_at: '2026-11-16T20:00:00Z', quotes: [original] };
  const p = policy({ gameStates: { g1: { lifecycle: 'upcoming', startAt: '2026-11-17T03:15:00Z' } },
    qualifications: { 'p1::receiving_yards': true } });
  const first = deriveMarketStatus(payload, Date.parse('2026-11-16T20:10:00Z'), p);
  assert.equal(first.offers[0].freshness, 'current');
  payload.updated_at = '2026-11-17T00:10:00Z'; // unchanged payload re-served later
  const result = deriveMarketStatus(payload, Date.parse('2026-11-17T00:10:00Z'), p);
  assert.equal(original.quoted_at, '2026-11-16T20:00:00Z');
  assert.equal(result.offers[0].freshness, 'stale');
  assert.equal(result.offers[0].quotedAt, original.quoted_at);
});

test('started, unavailable, checking, snapshot, verified, none precedence is singular', () => {
  const base = { updated_at: '2026-11-17T00:00:00Z', quotes: [quote()] };
  const now = Date.parse('2026-11-17T00:10:00Z');
  const upcoming = { g1: { lifecycle: 'upcoming', startAt: '2026-11-17T03:15:00Z' } };
  const started = { g1: { lifecycle: 'started', startAt: '2026-11-16T23:00:00Z' } };
  const common = { qualifications: { 'p1::receiving_yards': true } };
  assert.equal(deriveMarketStatus(base, now, policy({ ...common, gameStates: started, feedUnavailable: true, checking: true })).presentation, 'started');
  assert.equal(deriveMarketStatus(base, now, policy({ ...common, gameStates: upcoming, feedUnavailable: true, checking: true })).presentation, 'unavailable');
  assert.equal(deriveMarketStatus(base, now, policy({ ...common, gameStates: upcoming, checking: true })).presentation, 'checking');
  assert.equal(deriveMarketStatus({ ...base, updated_at: '2026-11-16T18:00:00Z' }, now,
    policy({ ...common, gameStates: upcoming })).presentation, 'snapshot');
  assert.equal(deriveMarketStatus(base, now, policy({ ...common, gameStates: upcoming })).presentation, 'opportunities');
  assert.equal(deriveMarketStatus(base, now, policy({ gameStates: upcoming })).presentation, 'none');
});

test('viewer derived state never renders post-start quote as actionable opportunity', () => {
  // 1. Explicit pre_start: false
  const qPost = quote({ player_id: 'p1', pre_start: false });
  const payload1 = { updated_at: '2026-11-17T00:00:00Z', quotes: [qPost] };
  const p1 = policy({
    gameStates: { g1: { lifecycle: 'upcoming', startAt: '2026-11-17T03:15:00Z' } },
    qualifications: { 'p1::receiving_yards': true },
    allCount: 1
  });
  const res1 = deriveMarketStatus(payload1, Date.parse('2026-11-17T00:10:00Z'), p1);
  assert.equal(res1.verifiedCount, 0);
  assert.equal(res1.offers[0].opportunity, false);
  assert.equal(res1.presentation, 'none');

  // 2. Quote timestamp after game start timestamp
  const qLate = quote({
    player_id: 'p2',
    quoted_at: '2026-11-17T03:30:00Z',
    observed_at: '2026-11-17T03:30:00Z',
  });
  const payload2 = { updated_at: '2026-11-17T03:30:00Z', quotes: [qLate] };
  const p2 = policy({
    gameStates: { g1: { lifecycle: 'upcoming', startAt: '2026-11-17T03:15:00Z' } },
    qualifications: { 'p2::receiving_yards': true },
    allCount: 1
  });
  const res2 = deriveMarketStatus(payload2, Date.parse('2026-11-17T03:31:00Z'), p2);
  assert.equal(res2.verifiedCount, 0);
  assert.equal(res2.offers[0].opportunity, false);
});
