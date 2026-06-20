import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memberState, isActive, pendingCountFor, globalPendingCount, oldestPendingAgeSeconds, predecessorTitle } from './derive.js';

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
