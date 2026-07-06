import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memberState, isActive, pendingCountFor, globalPendingCount, oldestPendingAgeSeconds, predecessorTitle, ringForMember, ringSeverity } from './derive.js';
import * as derive from './derive.js';

test('memberState: alive member is alive', () => {
	assert.equal(memberState({ alive: true, session_lost_permanently: false }), 'alive');
});

test('memberState: not alive and not lost is dormant', () => {
	assert.equal(memberState({ alive: false, session_lost_permanently: false }), 'dormant');
});

test('memberState: lost wins even when alive is false', () => {
	assert.equal(memberState({ alive: false, session_lost_permanently: true }), 'lost');
});

test('memberState: lost wins even when alive is true', () => {
	assert.equal(memberState({ alive: true, session_lost_permanently: true }), 'lost');
});

test('isActive: state active is true', () => {
	assert.equal(isActive({ state: 'active' }), true);
});

test('isActive: state ended is false', () => {
	assert.equal(isActive({ state: 'ended' }), false);
});

test('isActive: missing meta is false', () => {
	assert.equal(isActive(null), false);
	assert.equal(isActive(undefined), false);
});

test('pendingCountFor: counts non-cancelled children', () => {
	const map = {
		r1: { cancelled: false },
		r2: { cancelled: true },
		r3: { cancelled: false },
	};
	assert.equal(pendingCountFor(map), 2);
});

test('pendingCountFor: empty or null map is 0', () => {
	assert.equal(pendingCountFor({}), 0);
	assert.equal(pendingCountFor(null), 0);
});

test('globalPendingCount: sums non-cancelled pendings across active convs only', () => {
	const convs = {
		a: { meta: { state: 'active' }, pending: { r1: { cancelled: false }, r2: { cancelled: false } } },
		b: { meta: { state: 'ended' }, pending: { r3: { cancelled: false } } },
		c: { meta: { state: 'active' }, pending: { r4: { cancelled: true }, r5: { cancelled: false } } },
	};
	assert.equal(globalPendingCount(convs), 3);
});

test('globalPendingCount: empty convs is 0', () => {
	assert.equal(globalPendingCount({}), 0);
});

test('oldestPendingAgeSeconds: resolves msgId to message timestamp', () => {
	const nowMs = Date.parse('2026-06-15T00:00:30.000Z');
	const resolver = { m1: '2026-06-15T00:00:00.000Z' };
	const pendings = [{ convId: 'a', requestId: 'r1', msgId: 'm1', firstObservedMs: nowMs }];
	assert.equal(oldestPendingAgeSeconds(pendings, resolver, nowMs), 30);
});

test('oldestPendingAgeSeconds: unresolved msgId falls back to firstObservedMs', () => {
	const nowMs = Date.parse('2026-06-15T00:00:30.000Z');
	const firstObservedMs = Date.parse('2026-06-15T00:00:20.000Z');
	const resolver = {};
	const pendings = [{ convId: 'a', requestId: 'r1', msgId: 'mX', firstObservedMs }];
	assert.equal(oldestPendingAgeSeconds(pendings, resolver, nowMs), 10);
});

test('oldestPendingAgeSeconds: returns the largest age across pendings', () => {
	const nowMs = Date.parse('2026-06-15T00:01:00.000Z');
	const resolver = { m1: '2026-06-15T00:00:50.000Z', m2: '2026-06-15T00:00:10.000Z' };
	const pendings = [
		{ convId: 'a', requestId: 'r1', msgId: 'm1', firstObservedMs: nowMs },
		{ convId: 'a', requestId: 'r2', msgId: 'm2', firstObservedMs: nowMs },
	];
	assert.equal(oldestPendingAgeSeconds(pendings, resolver, nowMs), 50);
});

test('oldestPendingAgeSeconds: null when no pendings', () => {
	assert.equal(oldestPendingAgeSeconds([], {}, Date.now()), null);
});

test('predecessorTitle: no continued_from pointer yields null', () => {
	const current = { meta: { state: 'active' } };
	assert.equal(predecessorTitle(current, { 'conv-2': current }), null);
});

test('predecessorTitle: predecessor present yields its title', () => {
	const predecessor = { meta: { title: 'Original work', state: 'ended' } };
	const current = { meta: { state: 'active', continued_from: 'conv-1' } };
	const convs = { 'conv-1': predecessor, 'conv-2': current };
	assert.equal(predecessorTitle(current, convs), 'Original work');
});

test('predecessorTitle: predecessor absent from map yields null so banner hides', () => {
	const current = { meta: { state: 'active', continued_from: 'conv-gone' } };
	assert.equal(predecessorTitle(current, { 'conv-2': current }), null);
});

test('ringForMember: matches by cli_session_id', () => {
	const rings = { 's1': { pct: 0.4 }, 's2': { pct: 0.9 } };
	assert.deepEqual(ringForMember({ cli_session_id: 's2' }, rings), { pct: 0.9 });
});

test('ringForMember: no match returns null', () => {
	assert.equal(ringForMember({ cli_session_id: 'nope' }, { s1: { pct: 0.4 } }), null);
});

test('ringForMember: missing member or rings returns null', () => {
	assert.equal(ringForMember(null, { s1: {} }), null);
	assert.equal(ringForMember({ cli_session_id: 's1' }, null), null);
	assert.equal(ringForMember({}, { s1: {} }), null);
});

test('ringSeverity: matches Watchtower thresholds', () => {
	assert.equal(ringSeverity(0.85), 'red');   // > 0.80
	assert.equal(ringSeverity(0.80), 'amber');  // >= 0.50, not > 0.80
	assert.equal(ringSeverity(0.50), 'amber');
	assert.equal(ringSeverity(0.49), 'green');
	assert.equal(ringSeverity(null), 'cold');
});

test('sessionChip maps states to labels and classes', () => {
	assert.deepEqual(derive.sessionChip({ state: 'active' }), { label: 'active', cls: 'chip-active' });
	assert.deepEqual(derive.sessionChip({ state: 'awaiting_human' }), { label: 'needs you', cls: 'chip-awaiting-human' });
	assert.deepEqual(derive.sessionChip({ state: 'awaiting_agent' }), { label: 'waiting on agent', cls: 'chip-awaiting-agent' });
	assert.deepEqual(derive.sessionChip({ state: 'idle' }), { label: 'idle', cls: 'chip-idle' });
	assert.deepEqual(derive.sessionChip({ state: 'ended' }), { label: 'ended', cls: 'chip-ended' });
	assert.deepEqual(derive.sessionChip({ state: 'lost' }), { label: 'lost', cls: 'chip-lost' });
	assert.deepEqual(derive.sessionChip({ state: 'weird' }), { label: 'weird', cls: 'chip-idle' });
});

test('projectTail takes the last segment of either slash style', () => {
	assert.equal(derive.projectTail('C:\\Work\\Switchboard'), 'Switchboard');
	assert.equal(derive.projectTail('/home/john/work/x'), 'x');
	assert.equal(derive.projectTail(''), '');
});

test('sessionAgeSeconds and formatAge', () => {
	const nowMs = Date.parse('2026-07-06T12:10:00Z');
	assert.equal(derive.sessionAgeSeconds({ last_event_at: '2026-07-06T12:00:00+00:00' }, nowMs), 600);
	assert.equal(derive.sessionAgeSeconds({ last_event_at: 'garbage' }, nowMs), null);
	assert.equal(derive.formatAge(45), '45s');
	assert.equal(derive.formatAge(600), '10m');
	assert.equal(derive.formatAge(7200), '2h');
	assert.equal(derive.formatAge(259200), '3d');
});

test('sortSessionEntries orders newest-first by last_event_at', () => {
	const entries = derive.sortSessionEntries({
		a: { last_event_at: '2026-07-06T10:00:00+00:00' },
		b: { last_event_at: '2026-07-06T12:00:00+00:00' },
	});
	assert.deepEqual(entries.map((e) => e.id), ['b', 'a']);
});

test('sensorOffline is true when pushed_at is absent or stale', () => {
	const nowMs = Date.parse('2026-07-06T12:10:00Z');
	assert.equal(derive.sensorOffline(null, nowMs), true);
	assert.equal(derive.sensorOffline('2026-07-06T12:09:30Z', nowMs), false);
	assert.equal(derive.sensorOffline('2026-07-06T11:00:00Z', nowMs), true);
});
