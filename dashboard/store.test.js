import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from './store.js';
import * as paths from './schema.js';

function makeFakeStorage(initial = {}) {
	const map = new Map(Object.entries(initial));
	return {
		getItem: (k) => (map.has(k) ? map.get(k) : null),
		setItem: (k, v) => { map.set(k, String(v)); },
		_map: map,
	};
}

function makeFakeFb() {
	const calls = { onValue: [], onChildAdded: [], onChildChanged: [], onChildRemoved: [], pushed: [], set: [], updated: [], unsubs: [] };
	function recorder(kind) {
		return (path, cb) => {
			const entry = { path, cb };
			calls[kind].push(entry);
			const unsub = () => { calls.unsubs.push({ kind, path }); };
			entry.unsub = unsub;
			return unsub;
		};
	}
	return {
		calls,
		onAuth: (cb) => { calls.onAuth = cb; return () => {}; },
		signIn: () => { calls.signInCount = (calls.signInCount || 0) + 1; return Promise.resolve(); },
		onValue: recorder('onValue'),
		onChildAdded: recorder('onChildAdded'),
		onChildChanged: recorder('onChildChanged'),
		onChildRemoved: recorder('onChildRemoved'),
		pushValue: (path, value) => { calls.pushed.push({ path, value }); return Promise.resolve(); },
		setValue: (path, value) => { calls.set.push({ path, value }); return Promise.resolve(); },
		updateValue: (path, value) => { calls.updated.push({ path, value }); return Promise.resolve(); },
		nowIso: () => '2026-06-15T12:00:00.000Z',
	};
}

function makeStore(overrides = {}) {
	const fb = overrides.fb || makeFakeFb();
	const storage = overrides.storage || makeFakeStorage(overrides.storageInit || {});
	const nowMs = overrides.nowMs || (() => 1000);
	const store = createStore({ fb, paths, storage, nowMs });
	return { store, fb, storage };
}

test('initialState shape is exactly the contract', () => {
	const { store } = makeStore();
	const s = store.getState();
	assert.deepEqual(s, {
		authed: false,
		authError: null,
		globalAway: false,
		openConversationId: null,
		wslAvailable: false,
		conversations: {},
		adminNotifications: {},
		selectedConversationId: null,
		pendingsFlat: [],
		messageTimestampResolver: {},
		health: { reachable: false, healthy: false, totalAnswered: null },
		ui: { leftCollapsed: false, rightCollapsed: false, awayOffDialogOpen: false },
		paneErrors: {},
	});
});

test('initialState reads collapse flags from storage', () => {
	const { store } = makeStore({ storageInit: { 'sb.leftCollapsed': 'true', 'sb.rightCollapsed': 'false' } });
	const s = store.getState();
	assert.equal(s.ui.leftCollapsed, true);
	assert.equal(s.ui.rightCollapsed, false);
});

test('setAuthed sets authed and clears authError', () => {
	const { store } = makeStore();
	store.setAuthError('boom');
	store.setAuthed(true, { uid: 'u1' });
	assert.equal(store.getState().authed, true);
	assert.equal(store.getState().authError, null);
});

test('setAuthError sets the message', () => {
	const { store } = makeStore();
	store.setAuthError('denied');
	assert.equal(store.getState().authError, 'denied');
});

test('retrySignIn clears authError then calls fb.signIn', () => {
	const { store, fb } = makeStore();
	store.setAuthError('was broken');
	store.retrySignIn();
	assert.equal(store.getState().authError, null);
	assert.equal(fb.calls.signInCount, 1);
});

test('setGlobalAway, setOpenConversationId, setWslAvailable update their slices', () => {
	const { store } = makeStore();
	store.setGlobalAway(true);
	store.setOpenConversationId('c5');
	store.setWslAvailable(true);
	const s = store.getState();
	assert.equal(s.globalAway, true);
	assert.equal(s.openConversationId, 'c5');
	assert.equal(s.wslAvailable, true);
});

test('subscribe is notified on state change and unsub stops it', () => {
	const { store } = makeStore();
	let count = 0;
	const unsub = store.subscribe(() => { count += 1; });
	store.setGlobalAway(true);
	assert.equal(count, 1);
	unsub();
	store.setGlobalAway(false);
	assert.equal(count, 1);
});
test('toggleLeftCollapsed flips ui.leftCollapsed and persists to storage', () => {
	const { store, storage } = makeStore();
	store.toggleLeftCollapsed();
	assert.equal(store.getState().ui.leftCollapsed, true);
	assert.equal(storage.getItem('sb.leftCollapsed'), 'true');
	store.toggleLeftCollapsed();
	assert.equal(store.getState().ui.leftCollapsed, false);
	assert.equal(storage.getItem('sb.leftCollapsed'), 'false');
});

test('toggleRightCollapsed flips ui.rightCollapsed and persists to storage', () => {
	const { store, storage } = makeStore();
	store.toggleRightCollapsed();
	assert.equal(store.getState().ui.rightCollapsed, true);
	assert.equal(storage.getItem('sb.rightCollapsed'), 'true');
});

test('setAwayOffDialogOpen toggles ui.awayOffDialogOpen', () => {
	const { store } = makeStore();
	store.setAwayOffDialogOpen(true);
	assert.equal(store.getState().ui.awayOffDialogOpen, true);
	store.setAwayOffDialogOpen(false);
	assert.equal(store.getState().ui.awayOffDialogOpen, false);
});

test('setHealth replaces the health slice', () => {
	const { store } = makeStore();
	store.setHealth({ reachable: true, healthy: true, totalAnswered: 42 });
	assert.deepEqual(store.getState().health, { reachable: true, healthy: true, totalAnswered: 42 });
});

test('setPaneError sets and clears a pane error', () => {
	const { store } = makeStore();
	store.setPaneError('detail', 'cannot read');
	assert.equal(store.getState().paneErrors.detail, 'cannot read');
	store.setPaneError('detail', null);
	assert.equal('detail' in store.getState().paneErrors, false);
});

test('upsertConversationMeta and removeConversation manage the conversations map', () => {
	const { store } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active', title: 'X' });
	assert.deepEqual(store.getState().conversations.c1.meta, { state: 'active', title: 'X' });
	store.removeConversation('c1');
	assert.equal('c1' in store.getState().conversations, false);
});

test('upsertAdminNotification stores by key', () => {
	const { store } = makeStore();
	store.upsertAdminNotification('k1', { text: 'hi', type: 'notify' });
	assert.deepEqual(store.getState().adminNotifications.k1, { text: 'hi', type: 'notify' });
});

test('mergeConversationMessages updates messageTimestampResolver for the selected conv', () => {
	const { store } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active' });
	store.selectConversation('c1');
	store.mergeConversationMessages('c1', { m1: { timestamp: '2026-06-15T00:00:00.000Z' } });
	assert.equal(store.getState().messageTimestampResolver.m1, '2026-06-15T00:00:00.000Z');
});
test('selectConversation attaches three onValue listeners and detaches prior ones on switch', () => {
	const { store, fb } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active' });
	store.upsertConversationMeta('c2', { state: 'active' });
	store.selectConversation('c1');
	const afterFirst = fb.calls.onValue.length;
	const selectionPaths = fb.calls.onValue.slice(afterFirst - 3).map((e) => e.path);
	assert.deepEqual(selectionPaths, [
		'conversations/c1/messages',
		'conversations/c1/members_active',
		'conversations/c1/agent_status',
	]);
	const unsubsBefore = fb.calls.unsubs.length;
	store.selectConversation('c2');
	const detached = fb.calls.unsubs.slice(unsubsBefore).map((u) => u.path);
	assert.deepEqual(detached, [
		'conversations/c1/messages',
		'conversations/c1/members_active',
		'conversations/c1/agent_status',
	]);
	assert.equal(store.getState().selectedConversationId, 'c2');
});

test('startGlobalListeners wires conversation/global/admin listeners', () => {
	const { store, fb } = makeStore();
	store.startGlobalListeners();
	const addedPaths = fb.calls.onChildAdded.map((e) => e.path);
	assert.ok(addedPaths.includes('conversations'));
	assert.ok(addedPaths.includes('admin_notifications'));
	const valuePaths = fb.calls.onValue.map((e) => e.path);
	assert.ok(valuePaths.includes('global_settings/away_mode'));
	assert.ok(valuePaths.includes('global_settings/open_conversation_id'));
	assert.ok(valuePaths.includes('global_settings/wsl_available'));
});

test('an active conversation meta attaches a pending_questions listener', () => {
	const { store, fb } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active' });
	const pendingPaths = fb.calls.onValue.map((e) => e.path);
	assert.ok(pendingPaths.includes('conversations/c1/pending_questions'));
});

test('ending an active conversation detaches its pending listener', () => {
	const { store, fb } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active' });
	const unsubsBefore = fb.calls.unsubs.length;
	store.upsertConversationMeta('c1', { state: 'ended' });
	const detached = fb.calls.unsubs.slice(unsubsBefore).map((u) => u.path);
	assert.ok(detached.includes('conversations/c1/pending_questions'));
});

test('mergeConversationPending builds pendingsFlat from camelCase keys, dropping cancelled', () => {
	const { store } = makeStore({ nowMs: () => 5000 });
	store.upsertConversationMeta('c1', { state: 'active' });
	store.mergeConversationPending('c1', {
		r1: { sender: 'John', questionText: 'q?', msgId: 'm1', cancelled: false, suggestions: ['a'] },
		r2: { sender: 'John', questionText: 'gone', msgId: 'm2', cancelled: true, suggestions: null },
	});
	const flat = store.getState().pendingsFlat;
	assert.equal(flat.length, 1);
	assert.deepEqual(flat[0], {
		convId: 'c1',
		requestId: 'r1',
		sender: 'John',
		questionText: 'q?',
		suggestions: ['a'],
		msgId: 'm1',
		firstObservedMs: 5000,
	});
});

test('pendingsFlat firstObservedMs is stable across re-emits of the same (convId,requestId)', () => {
	let clock = 5000;
	const { store } = makeStore({ nowMs: () => clock });
	store.upsertConversationMeta('c1', { state: 'active' });
	store.mergeConversationPending('c1', { r1: { sender: 'John', questionText: 'q?', msgId: 'm1', cancelled: false } });
	const firstStamp = store.getState().pendingsFlat[0].firstObservedMs;
	clock = 9000;
	store.mergeConversationPending('c1', {
		r1: { sender: 'John', questionText: 'q? edited', msgId: 'm1', cancelled: false },
		r2: { sender: 'John', questionText: 'new', msgId: 'm2', cancelled: false },
	});
	const flat = store.getState().pendingsFlat;
	const r1 = flat.find((p) => p.requestId === 'r1');
	const r2 = flat.find((p) => p.requestId === 'r2');
	assert.equal(r1.firstObservedMs, firstStamp);
	assert.equal(r2.firstObservedMs, 9000);
});
