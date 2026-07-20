import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memberState, isActive, isThinking, agentStatusLabel, pendingQuestionText, pendingCountFor, globalPendingCount, oldestPendingAgeSeconds, predecessorTitle, ringForMember, ringSeverity } from './derive.js';
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

test('isThinking: fresh thinking or tool state is true', () => {
	const now = 100000;
	assert.equal(isThinking({ agent1: { state: 'thinking', updatedAt: now - 5000 } }, now), true);
	assert.equal(isThinking({ agent1: { state: 'tool:Bash', updated_at: now - 5000 } }, now), true);
	assert.equal(isThinking({ agent1: { state: 'thinking', updated_at: new Date(now - 5000).toISOString() } }, now), true);
});

test('isThinking: idle or clear or stale state is false', () => {
	const now = 100000;
	assert.equal(isThinking({ agent1: { state: 'idle', updatedAt: now - 5000 } }, now), false);
	assert.equal(isThinking({ agent1: { state: 'clear', updatedAt: now - 5000 } }, now), false);
	assert.equal(isThinking({ agent1: { state: 'thinking', updatedAt: now - (31 * 60 * 1000) } }, now), false);
	assert.equal(isThinking(null, now), false);
});

test('agentStatusLabel: returns state or state:detail when fresh', () => {
	const now = 100000;
	assert.equal(agentStatusLabel({ agent1: { state: 'thinking', updated_at: now - 5000 } }, now), 'thinking');
	assert.equal(agentStatusLabel({ agent1: { state: 'running', detail: 'view_file', updated_at: now - 5000 } }, now), 'running: view_file');
	assert.equal(agentStatusLabel({ agent1: { state: 'idle', updated_at: now - 5000 } }, now), null);
});

test('pendingQuestionText: returns active question text, ignoring cancelled', () => {
	const map = {
		req1: { cancelled: true, question: 'Old cancelled question?' },
		req2: { cancelled: false, question: 'Active question text?' },
	};
	assert.equal(pendingQuestionText(map), 'Active question text?');
	assert.equal(pendingQuestionText(null), null);
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

test('oldestPendingAgeSeconds: age from askedAt', () => {
	const nowMs = Date.parse('2026-06-15T00:00:30.000Z');
	const pendings = [{ convId: 'a', requestId: 'r1', askedAt: '2026-06-15T00:00:00.000Z', firstObservedMs: nowMs }];
	assert.equal(oldestPendingAgeSeconds(pendings, nowMs), 30);
});

test('oldestPendingAgeSeconds: missing askedAt falls back to firstObservedMs', () => {
	const nowMs = Date.parse('2026-06-15T00:00:30.000Z');
	const firstObservedMs = Date.parse('2026-06-15T00:00:20.000Z');
	const pendings = [{ convId: 'a', requestId: 'r1', askedAt: undefined, firstObservedMs }];
	assert.equal(oldestPendingAgeSeconds(pendings, nowMs), 10);
});

test('oldestPendingAgeSeconds: returns the largest age across pendings', () => {
	const nowMs = Date.parse('2026-06-15T00:01:00.000Z');
	const pendings = [
		{ convId: 'a', requestId: 'r1', askedAt: '2026-06-15T00:00:50.000Z', firstObservedMs: nowMs },
		{ convId: 'a', requestId: 'r2', askedAt: '2026-06-15T00:00:10.000Z', firstObservedMs: nowMs },
	];
	assert.equal(oldestPendingAgeSeconds(pendings, nowMs), 50);
});

test('oldestPendingAgeSeconds: null when no pendings', () => {
	assert.equal(oldestPendingAgeSeconds([], Date.now()), null);
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

test('sessionChip: blocked_on_approval overrides state to the needs-approval chip', () => {
	assert.deepEqual(
		derive.sessionChip({ state: 'active', blocked_on_approval: true }),
		{ label: 'needs approval', cls: 'chip-needs-approval' },
	);
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

test('formatAge floors (does not round up) and covers all tiers', () => {
	assert.equal(derive.formatAge(0), '0s');
	assert.equal(derive.formatAge(59), '59s');
	assert.equal(derive.formatAge(90), '1m');      // floor: round would give 2m
	assert.equal(derive.formatAge(119), '1m');
	assert.equal(derive.formatAge(3599), '59m');   // floor: round would give 60m
	assert.equal(derive.formatAge(7199), '1h');
	assert.equal(derive.formatAge(86399), '23h');
	assert.equal(derive.formatAge(86400), '1d');
	assert.equal(derive.formatAge(null), '');
	assert.equal(derive.formatAge(undefined), '');
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

test('sessionLabel: name wins regardless of name_source', () => {
	assert.equal(derive.sessionLabel({ name: 'Claude Win', name_source: 'ai', sender: 'Claude', cwd: '/x/y' }), 'Claude Win');
	assert.equal(derive.sessionLabel({ name: 'Custom Name', name_source: 'custom', sender: 'Claude', cwd: '/x/y' }), 'Custom Name');
});

test('sessionLabel: falls back to sender when name is empty', () => {
	assert.equal(derive.sessionLabel({ name: '', sender: 'Claude Win', cwd: '/x/y' }), 'Claude Win');
	assert.equal(derive.sessionLabel({ sender: 'Claude Win', cwd: '/x/y' }), 'Claude Win');
});

test('sessionLabel: falls back to projectTail(cwd) when name and sender are empty', () => {
	assert.equal(derive.sessionLabel({ cwd: 'C:\\Work\\Switchboard' }), 'Switchboard');
});

test('sessionLabel: falls back to (unknown) when nothing is available', () => {
	assert.equal(derive.sessionLabel({}), '(unknown)');
	assert.equal(derive.sessionLabel({ name: '', sender: '', cwd: '' }), '(unknown)');
});

test('needsAttention: idle with no ack is true', () => {
	assert.equal(derive.needsAttention({ state: 'idle', last_event_at: '2026-07-06T12:00:00Z' }, null), true);
});

test('needsAttention: idle with event after ack is true', () => {
	assert.equal(
		derive.needsAttention({ state: 'idle', last_event_at: '2026-07-06T12:00:00Z' }, '2026-07-06T11:00:00Z'),
		true,
	);
});

test('needsAttention: idle with ack after event is false', () => {
	assert.equal(
		derive.needsAttention({ state: 'idle', last_event_at: '2026-07-06T11:00:00Z' }, '2026-07-06T12:00:00Z'),
		false,
	);
});

test('needsAttention: equal-second timestamps with differing suffixes are not newer', () => {
	assert.equal(
		derive.needsAttention({ state: 'idle', last_event_at: '2026-07-06T12:00:00Z' }, '2026-07-06T12:00:00+00:00'),
		false,
	);
});

test('needsAttention: non-idle state is false', () => {
	assert.equal(derive.needsAttention({ state: 'active', last_event_at: '2026-07-06T12:00:00Z' }, null), false);
});

test('needsAttention: unparseable last_event_at is false', () => {
	assert.equal(derive.needsAttention({ state: 'idle', last_event_at: 'garbage' }, null), false);
});

test('needsAttention: blocked_on_approval is true before the idle-state guard', () => {
	assert.equal(
		derive.needsAttention({ state: 'active', blocked_on_approval: true, last_event_at: '2026-07-06T12:00:00Z' }, null),
		true,
	);
});

test('needsAttention: blocked_on_approval stays true even with a recent ack', () => {
	assert.equal(
		derive.needsAttention(
			{ state: 'active', blocked_on_approval: true, last_event_at: '2026-07-06T12:00:00Z' },
			'2026-07-06T12:00:00Z',
		),
		true,
	);
});

test('wakePathHint maps each state to its hint text', () => {
	assert.equal(derive.wakePathHint({ state: 'awaiting_agent' }), 'wakes instantly');
	assert.equal(derive.wakePathHint({ state: 'awaiting_human' }), 'on next phone answer');
	assert.equal(derive.wakePathHint({ state: 'active' }), 'at end of current turn');
	assert.equal(derive.wakePathHint({ state: 'idle' }), "on John's next prompt");
	assert.equal(derive.wakePathHint({ state: 'ended' }), 'Resume into conversation');
	assert.equal(derive.wakePathHint({ state: 'lost' }), 'Resume into conversation');
});

test('approvalHint: in-tool, old, and no verdict yields the weak hint', () => {
	const nowMs = Date.parse('2026-07-06T12:00:00Z');
	const record = { in_tool: true, last_event_at: '2026-07-06T11:54:00Z', title_state: null };
	assert.equal(derive.approvalHint(record, nowMs), 'possibly waiting on approval');
});

test('approvalHint: a title_state verdict suppresses the hint', () => {
	const nowMs = Date.parse('2026-07-06T12:00:00Z');
	const record = { in_tool: true, last_event_at: '2026-07-06T11:54:00Z', title_state: 'working' };
	assert.equal(derive.approvalHint(record, nowMs), '');
});

test('approvalHint: blocked_on_approval suppresses the hint', () => {
	const nowMs = Date.parse('2026-07-06T12:00:00Z');
	const record = {
		in_tool: true, last_event_at: '2026-07-06T11:54:00Z', title_state: null, blocked_on_approval: true,
	};
	assert.equal(derive.approvalHint(record, nowMs), '');
});

test('approvalHint: fresh session (under 5 minutes) yields no hint', () => {
	const nowMs = Date.parse('2026-07-06T12:00:00Z');
	const record = { in_tool: true, last_event_at: '2026-07-06T11:59:00Z', title_state: null };
	assert.equal(derive.approvalHint(record, nowMs), '');
});

test('approvalHint: not in a tool yields no hint', () => {
	const nowMs = Date.parse('2026-07-06T12:00:00Z');
	const record = { in_tool: false, last_event_at: '2026-07-06T11:54:00Z', title_state: null };
	assert.equal(derive.approvalHint(record, nowMs), '');
});

test('isConvenable: true for active, idle, awaiting_human, awaiting_agent', () => {
	assert.equal(derive.isConvenable({ state: 'active' }), true);
	assert.equal(derive.isConvenable({ state: 'idle' }), true);
	assert.equal(derive.isConvenable({ state: 'awaiting_human' }), true);
	assert.equal(derive.isConvenable({ state: 'awaiting_agent' }), true);
});

test('isConvenable: false for ended and lost without a cwd, true with one', () => {
	assert.equal(derive.isConvenable({ state: 'ended' }), false);
	assert.equal(derive.isConvenable({ state: 'lost' }), false);
	assert.equal(derive.isConvenable({ state: 'ended', cwd: 'C:/Work/X' }), true);
	assert.equal(derive.isConvenable({ state: 'lost', cwd: 'C:/Work/X' }), true);
});
