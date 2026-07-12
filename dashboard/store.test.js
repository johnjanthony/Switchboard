import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from './store.js';
import * as paths from './schema.js';
import { pendingCountFor } from './derive.js';
import {
	answerCmd, resumeCmd, combineCmd, forceEndCmd, conveneCmd, ackSessionCmd,
	spawnFreshCmd, awayOnCmd, awayOffCmd, setHiddenCmd,
} from './commands.js';

function makeFakeStorage(initial = {}) {
	const map = new Map(Object.entries(initial));
	return {
		getItem: (k) => (map.has(k) ? map.get(k) : null),
		setItem: (k, v) => { map.set(k, String(v)); },
		_map: map,
	};
}

function makeFakeFb() {
	const calls = { onValue: [], onChildAdded: [], onChildChanged: [], onChildRemoved: [], pushed: [], set: [], unsubs: [] };
	function recorder(kind) {
		return (path, cb, errCb) => {
			const entry = { path, cb, errCb };
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
		nowIso: () => '2026-06-15T12:00:00.000Z',
	};
}

function makeStore(overrides = {}) {
	const fb = overrides.fb || makeFakeFb();
	const storage = overrides.storage || makeFakeStorage(overrides.storageInit || {});
	const nowMs = overrides.nowMs || (() => 1000);
	const requestStatus = overrides.requestStatus || (() => Promise.resolve({ ok: true, status: 200 }));
	const store = createStore({ fb, paths, storage, nowMs, requestStatus });
	return { store, fb, storage };
}

test('initialState shape is exactly the contract', () => {
	const { store } = makeStore();
	const s = store.getState();
	assert.deepEqual(s, {
		authed: false,
		authError: null,
		globalAway: false,
		wslAvailable: false,
		conversations: {},
		sessions: {},
		sessionAcks: {},
		adminNotifications: {},
		widget: { rings: {}, quota: null, status: null, pushedAt: null },
		selectedConversationId: null,
		pendingsFlat: [],
		health: { reachable: false, healthy: false, totalAnswered: null },
		ui: {
			leftCollapsed: false, rightCollapsed: false, leftWidth: 280, awayOffDialogOpen: false,
			sessionsCollapsed: false, selectedSessionIds: [],
		},
		paneErrors: {},
	});
});

test('widget rings listener updates state.widget.rings', () => {
	const { store, fb } = makeStore();
	store.startGlobalListeners();
	const entry = fb.calls.onValue.find((e) => e.path === paths.widgetRings());
	assert.ok(entry, 'widget/rings listener attached');
	entry.cb({ s1: { pct: 0.4 } });
	assert.deepEqual(store.getState().widget.rings, { s1: { pct: 0.4 } });
	entry.cb(null);
	assert.deepEqual(store.getState().widget.rings, {});
});

test('widget status listener updates state.widget.status', () => {
	const { store, fb } = makeStore();
	store.startGlobalListeners();
	const entry = fb.calls.onValue.find((e) => e.path === paths.widgetStatus());
	assert.ok(entry, 'widget/status listener attached');
	entry.cb({ watch_state: 'watching', level: 'major', button: 'stop' });
	assert.deepEqual(store.getState().widget.status, { watch_state: 'watching', level: 'major', button: 'stop' });
	entry.cb(null);
	assert.equal(store.getState().widget.status, null);
});

test('initialState reads and clamps leftWidth from storage', () => {
	assert.equal(makeStore({ storageInit: { 'sb.leftWidth': '420' } }).store.getState().ui.leftWidth, 420);
	// Out-of-range and non-numeric values clamp / fall back to defaults.
	assert.equal(makeStore({ storageInit: { 'sb.leftWidth': '50' } }).store.getState().ui.leftWidth, 180);
	assert.equal(makeStore({ storageInit: { 'sb.leftWidth': '9999' } }).store.getState().ui.leftWidth, 560);
	assert.equal(makeStore({ storageInit: { 'sb.leftWidth': 'nope' } }).store.getState().ui.leftWidth, 280);
});

test('setLeftWidth clamps to range, persists, and notifies', () => {
	const { store, storage } = makeStore();
	let notified = 0;
	store.subscribe(() => { notified += 1; });

	store.setLeftWidth(360);
	assert.equal(store.getState().ui.leftWidth, 360);
	assert.equal(storage.getItem('sb.leftWidth'), '360');
	assert.equal(notified, 1);

	store.setLeftWidth(40); // below min
	assert.equal(store.getState().ui.leftWidth, 180);
	store.setLeftWidth(5000); // above max
	assert.equal(store.getState().ui.leftWidth, 560);
	assert.equal(storage.getItem('sb.leftWidth'), '560');
});

test('setLeftCollapsed is idempotent, persists, and backs the toggle', () => {
	const { store, storage } = makeStore();
	store.setLeftCollapsed(true);
	assert.equal(store.getState().ui.leftCollapsed, true);
	assert.equal(storage.getItem('sb.leftCollapsed'), 'true');
	// Idempotent: setting true again keeps it true (drag emits many move events).
	store.setLeftCollapsed(true);
	assert.equal(store.getState().ui.leftCollapsed, true);
	// The toggle is defined in terms of it.
	store.toggleLeftCollapsed();
	assert.equal(store.getState().ui.leftCollapsed, false);
	assert.equal(storage.getItem('sb.leftCollapsed'), 'false');
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

test('setGlobalAway, setWslAvailable update their slices', () => {
	const { store } = makeStore();
	store.setGlobalAway(true);
	store.setWslAvailable(true);
	const s = store.getState();
	assert.equal(s.globalAway, true);
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

test('selectConversation attaches three onValue listeners and detaches prior ones on switch', () => {
	const { store, fb } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active' });
	store.upsertConversationMeta('c2', { state: 'active' });
	store.selectConversation('c1');
	const afterFirst = fb.calls.onValue.length;
	const selectionPaths = fb.calls.onValue.slice(afterFirst - 3).map((e) => e.path);
	assert.deepEqual(selectionPaths, [
		'messages/c1',
		'conversations/c1/members_active',
		'conversations/c1/agent_status',
	]);
	const unsubsBefore = fb.calls.unsubs.length;
	store.selectConversation('c2');
	const detached = fb.calls.unsubs.slice(unsubsBefore).map((u) => u.path);
	assert.deepEqual(detached, [
		'messages/c1',
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
	assert.ok(valuePaths.includes('global_settings/wsl_available'));
});

test('a denied global read routes back to the sign-in gate with an error (M4)', () => {
	// A wrong/unauthorized Google account yields an authed session where every
	// RTDB read is denied. Without an error callback the listeners silently
	// detach and the dashboard blanks. The global listeners must register an
	// error callback that drops back to the sign-in gate with a message.
	const { store, fb } = makeStore();
	store.setAuthed(true, { email: 'wrong@example.com' });
	store.startGlobalListeners();

	const entry = fb.calls.onChildAdded.find((e) => e.path === 'conversations');
	assert.ok(entry, 'conversations listener attached');
	assert.equal(typeof entry.errCb, 'function', 'global listener must register an error callback');

	entry.errCb(new Error('PERMISSION_DENIED'));
	const s = store.getState();
	assert.equal(s.authed, false, 'a denied global read returns to the sign-in gate');
	assert.ok(s.authError && s.authError.length > 0, 'an explanatory auth error is set');
});

test('a denied conversation read surfaces a detail pane error (M4)', () => {
	const { store, fb } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active' });
	store.selectConversation('c1');

	const entry = fb.calls.onValue.find((e) => e.path === 'messages/c1');
	assert.ok(entry, 'selection listener attached');
	assert.equal(typeof entry.errCb, 'function', 'selection listener must register an error callback');

	entry.errCb(new Error('PERMISSION_DENIED'));
	assert.ok(store.getState().paneErrors.detail, 'the detail pane error is set on a denied read');
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

test('ending an active conversation clears its stale pending from local state and pendingsFlat', () => {
	const { store } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active' });
	store.mergeConversationPending('c1', {
		r1: { sender: 'John', questionText: 'still answerable?', msgId: 'm1', cancelled: false },
	});
	assert.equal(store.getState().pendingsFlat.length, 1);
	assert.equal(store.getState().pendingsFlat[0].convId, 'c1');

	// Transition to ended: syncPendingListeners runs via upsertConversationMeta and must
	// both detach the listener AND clear the already-merged pending so it does not freeze.
	store.upsertConversationMeta('c1', { state: 'ended' });
	const s = store.getState();
	assert.equal(s.pendingsFlat.length, 0);
	assert.deepEqual(s.conversations.c1.pending, {});
	assert.equal(pendingCountFor(s.conversations.c1.pending), 0);
});

test('mergeConversationPending builds pendingsFlat from camelCase keys, dropping cancelled', () => {
	const { store } = makeStore({ nowMs: () => 5000 });
	store.upsertConversationMeta('c1', { state: 'active' });
	store.mergeConversationPending('c1', {
		r1: { sender: 'John', questionText: 'q?', msgId: 'm1', cancelled: false, suggestions: ['a'], askedAt: '2026-06-15T00:00:00.000Z' },
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
		askedAt: '2026-06-15T00:00:00.000Z',
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

test('setSessions replaces the sessions map and notifies', () => {
	const { store } = makeStore();
	let notified = 0;
	store.subscribe(() => { notified += 1; });
	store.setSessions({ 'sess-a': { state: 'active' } });
	assert.deepEqual(store.getState().sessions, { 'sess-a': { state: 'active' } });
	assert.ok(notified >= 1);
});

test('startGlobalListeners subscribes the sessions path', () => {
	const { store, fb } = makeStore();
	store.startGlobalListeners();
	const valuePaths = fb.calls.onValue.map((e) => e.path);
	assert.ok(valuePaths.includes(paths.sessions()));
});

test('toggleSessionsCollapsed flips and persists', () => {
	const { store, storage } = makeStore();
	const before = store.getState().ui.sessionsCollapsed;
	store.toggleSessionsCollapsed();
	assert.equal(store.getState().ui.sessionsCollapsed, !before);
	assert.equal(storage.getItem('sb.sessionsCollapsed'), String(!before));
});

test('startGlobalListeners subscribes the sessionAcks path', () => {
	const { store, fb } = makeStore();
	store.startGlobalListeners();
	const valuePaths = fb.calls.onValue.map((e) => e.path);
	assert.ok(valuePaths.includes(paths.sessionAcks()));
});

test('the sessionAcks listener replaces state.sessionAcks', () => {
	const { store, fb } = makeStore();
	store.startGlobalListeners();
	const entry = fb.calls.onValue.find((e) => e.path === paths.sessionAcks());
	assert.ok(entry, 'sessionAcks listener attached');
	entry.cb({ 'sess-a': '2026-06-15T12:00:00.000Z' });
	assert.deepEqual(store.getState().sessionAcks, { 'sess-a': '2026-06-15T12:00:00.000Z' });
	entry.cb(null);
	assert.deepEqual(store.getState().sessionAcks, {});
});

test('toggleSessionSelected adds and removes an id; clearSessionSelection resets to empty', () => {
	const { store } = makeStore();
	assert.deepEqual(store.getState().ui.selectedSessionIds, []);
	store.toggleSessionSelected('sess-a');
	assert.deepEqual(store.getState().ui.selectedSessionIds, ['sess-a']);
	store.toggleSessionSelected('sess-b');
	assert.deepEqual(store.getState().ui.selectedSessionIds, ['sess-a', 'sess-b']);
	store.toggleSessionSelected('sess-a');
	assert.deepEqual(store.getState().ui.selectedSessionIds, ['sess-b']);
	store.clearSessionSelection();
	assert.deepEqual(store.getState().ui.selectedSessionIds, []);
});

test('ackSession writes ackSessionCmd via fb.setValue', () => {
	const { store, fb } = makeStore();
	store.ackSession('sess-a');
	const expected = ackSessionCmd('sess-a', fb.nowIso);
	assert.deepEqual(fb.calls.set, [expected]);
});

test('conveneSelected pushes conveneCmd and clears selection on success', async () => {
	const { store, fb } = makeStore();
	store.toggleSessionSelected('sess-a');
	store.toggleSessionSelected('sess-b');
	const ok = await store.conveneSelected({ target: 'new', title: 'Pairing' });
	assert.equal(ok, true);
	assert.deepEqual(fb.calls.pushed, [conveneCmd({ sessionIds: ['sess-a', 'sess-b'], target: 'new', title: 'Pairing' }, fb.nowIso)]);
	assert.deepEqual(store.getState().ui.selectedSessionIds, []);
});

test('conveneSelected keeps the selection and surfaces to global on failure', async () => {
	const fb = makeFakeFb();
	fb.pushValue = () => Promise.reject(new Error('DENIED'));
	const { store } = makeStore({ fb });
	store.toggleSessionSelected('sess-a');
	assert.equal(await store.conveneSelected({ target: 'new' }), false);
	assert.deepEqual(store.getState().ui.selectedSessionIds, ['sess-a']);
	assert.ok(store.getState().paneErrors.global);
});

test('spawnFresh / awayOn / awayOff / setHidden push and return true', async () => {
	const { store, fb } = makeStore();
	assert.equal(await store.spawnFresh({ surface: 'windows', project: 'X' }), true);
	assert.equal(await store.awayOn(), true);
	assert.equal(await store.awayOff({ decision: 'skip' }), true);
	assert.equal(await store.setHidden('c1', true), true);
	assert.deepEqual(fb.calls.pushed, [
		spawnFreshCmd({ surface: 'windows', project: 'X' }, fb.nowIso),
		awayOnCmd(fb.nowIso),
		awayOffCmd({ decision: 'skip' }, fb.nowIso),
	]);
	assert.deepEqual(fb.calls.set, [setHiddenCmd('c1', true)]);
});

test('a rejected global write surfaces to the global pane', async () => {
	const fb = makeFakeFb();
	fb.pushValue = () => Promise.reject(new Error('DENIED'));
	const { store } = makeStore({ fb });
	assert.equal(await store.awayOn(), false);
	assert.ok(store.getState().paneErrors.global);
});

test('requestClaudeStatus calls requestStatus and returns true on ok', async () => {
	let called = null;
	const { store } = makeStore({ requestStatus: (a) => { called = a; return Promise.resolve({ ok: true, status: 200 }); } });
	assert.equal(await store.requestClaudeStatus('check'), true);
	assert.equal(called, 'check');
});

test('requestClaudeStatus surfaces a non-ok response to the global pane', async () => {
	const { store } = makeStore({ requestStatus: () => Promise.resolve({ ok: false, status: 503 }) });
	assert.equal(await store.requestClaudeStatus('check'), false);
	assert.ok(store.getState().paneErrors.global);
});

test('retrySignIn surfaces a rejected popup as an auth error', async () => {
	const fb = makeFakeFb();
	fb.signIn = () => Promise.reject(new Error('popup-blocked'));
	const { store } = makeStore({ fb });
	await store.retrySignIn();
	assert.ok(store.getState().authError);
});

test('a denied pending-questions read surfaces to the global pane, not the sign-in gate', () => {
	const { store, fb } = makeStore();
	store.setAuthed(true, { email: 'ok@example.com' });
	store.upsertConversationMeta('c1', { state: 'active' });
	const entry = fb.calls.onValue.find((e) => e.path === 'conversations/c1/pending_questions');
	assert.ok(entry, 'pending listener attached');
	entry.errCb(new Error('PERMISSION_DENIED'));
	assert.equal(store.getState().authed, true, 'still authed - one conv error must not blank the app');
	assert.ok(store.getState().paneErrors.global);
});

test('mergeConversationMembers replaces the members map so server-side deletions propagate', () => {
	const { store } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active' });
	store.mergeConversationMembers('c1', { a: { alive: true }, b: { alive: true } });
	assert.deepEqual(Object.keys(store.getState().conversations.c1.members), ['a', 'b']);
	store.mergeConversationMembers('c1', { a: { alive: true } });
	assert.deepEqual(Object.keys(store.getState().conversations.c1.members), ['a']);
});

test('mergeConversationAgentStatus replaces the map so a cleared status disappears', () => {
	const { store } = makeStore();
	store.upsertConversationMeta('c1', { state: 'active' });
	store.mergeConversationAgentStatus('c1', { a: { state: 'working' } });
	assert.deepEqual(store.getState().conversations.c1.agentStatus, { a: { state: 'working' } });
	store.mergeConversationAgentStatus('c1', {});
	assert.deepEqual(store.getState().conversations.c1.agentStatus, {});
});

test('startGlobalListeners is idempotent: a second call detaches the first set', () => {
	const { store, fb } = makeStore();
	store.startGlobalListeners();
	const unsubsBefore = fb.calls.unsubs.length;
	store.startGlobalListeners();
	const detached = fb.calls.unsubs.slice(unsubsBefore).map((u) => u.path);
	assert.ok(detached.includes('conversations'), 'prior conversations listener detached before re-attach');
	assert.ok(detached.includes('global_settings/away_mode'), 'prior away-mode listener detached');
	assert.equal(detached.length, 13, 'all 13 global listeners detached before re-attach - update this count when adding a listener');
});

test('sendAnswer writes answerCmd and returns true on success', async () => {
	const { store, fb } = makeStore();
	const ok = await store.sendAnswer('c1', 'r1', 'hi', 'John');
	assert.equal(ok, true);
	assert.deepEqual(fb.calls.set, [answerCmd('c1', 'r1', 'hi', 'John', fb.nowIso)]);
});

test('sendAnswer surfaces a rejected write to the detail pane and returns false', async () => {
	const fb = makeFakeFb();
	fb.setValue = () => Promise.reject(new Error('PERMISSION_DENIED'));
	const { store } = makeStore({ fb });
	const ok = await store.sendAnswer('c1', 'r1', 'hi', 'John');
	assert.equal(ok, false);
	assert.ok(store.getState().paneErrors.detail);
});

test('restoreLine / patchLine / dropLine push their commands and return true', async () => {
	const { store, fb } = makeStore();
	assert.equal(await store.restoreLine('c1', 'go'), true);
	assert.equal(await store.patchLine('c1', 'c2'), true);
	assert.equal(await store.dropLine('c1'), true);
	assert.deepEqual(fb.calls.pushed, [
		resumeCmd({ sourceConversationId: 'c1', prompt: 'go' }, fb.nowIso),
		combineCmd({ sourceConversationId: 'c1', targetConversationId: 'c2' }, fb.nowIso),
		forceEndCmd({ conversationId: 'c1' }, fb.nowIso),
	]);
});

test('a rejected detail write surfaces to the detail pane', async () => {
	const fb = makeFakeFb();
	fb.pushValue = () => Promise.reject(new Error('DENIED'));
	const { store } = makeStore({ fb });
	assert.equal(await store.dropLine('c1'), false);
	assert.ok(store.getState().paneErrors.detail);
});
