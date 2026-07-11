// The dashboard's single view-model store. Owns all projected state and is the
// only writer of it; components drive everything through the actions below and
// never assign onto the store or guard its methods with "?".
//
// deps: { fb, paths, storage, nowMs }
//   fb      - the firebase.js wrapper (onAuth/signIn/on*/pushValue/setValue/nowIso)
//   paths   - the schema.js path builders
//   storage - localStorage-like { getItem, setItem }
//   nowMs   - () => epoch ms, used to stamp pendingsFlat firstObservedMs

import {
	answerCmd, resumeCmd, combineCmd, forceEndCmd,
	spawnFreshCmd, awayOnCmd, awayOffCmd, setHiddenCmd,
	conveneCmd, ackSessionCmd,
} from './commands.js';

export function createStore(deps) {
	const { fb, paths, storage, nowMs, requestStatus } = deps;

	const state = initialState(storage);
	const subscribers = new Set();

	// Per-(convId,requestId) first-sighting timestamps, kept private so
	// pendingsFlat.firstObservedMs is stable across re-emits.
	const firstObservedByKey = new Map();
	// Lazy per-selection listener unsubscribes, detached on the next selection.
	let selectionUnsubs = [];
	// Global (non-selection) listener unsubscribes, detached before re-attach so
	// an auth re-fire or account switch never leaves a duplicate listener set.
	let globalUnsubs = [];
	// Per-active-conversation pending listener unsubscribes, keyed by convId.
	const pendingUnsubsByConv = new Map();

	function getState() {
		return state;
	}

	function subscribe(fn) {
		subscribers.add(fn);
		return () => { subscribers.delete(fn); };
	}

	function notify() {
		for (const fn of subscribers) {
			fn(state);
		}
	}

	function setAuthed(authed, user) {
		state.authed = authed;
		state.user = user || null;
		state.authError = null;
		notify();
	}

	function setAuthError(msgOrNull) {
		state.authError = msgOrNull;
		notify();
	}

	function setGlobalReadError(err) {
		// A denied global RTDB read (e.g. signed in with an unauthorized Google
		// account, or a rules change revoking access mid-session) would
		// otherwise silently blank the dashboard: the SDK detaches the listener
		// and no data ever arrives. Drop back to the sign-in gate with an
		// explanatory error so the operator can switch to the authorized account
		// instead of staring at an empty screen (M4).
		const detail = err && err.message ? `: ${err.message}` : '';
		state.authed = false;
		state.user = null;
		state.authError = `Database access denied${detail}. Sign in with the authorized Google account.`;
		notify();
	}

	function retrySignIn() {
		state.authError = null;
		notify();
		return fb.signIn().catch((err) => {
			setAuthError(`Sign-in failed${err && err.message ? ': ' + err.message : ''}. Try again.`);
		});
	}

	function setGlobalAway(value) {
		state.globalAway = value;
		notify();
	}

	function setWslAvailable(value) {
		state.wslAvailable = value;
		notify();
	}

	function setWidgetRings(map) {
		state.widget = { ...state.widget, rings: map || {} };
		notify();
	}

	function setWidgetQuota(quota) {
		state.widget = { ...state.widget, quota: quota || null };
		notify();
	}

	function setWidgetStatus(status) {
		state.widget = { ...state.widget, status: status || null };
		notify();
	}

	function setWidgetPushedAt(ts) {
		state.widget = { ...state.widget, pushedAt: ts || null };
		notify();
	}

	function setSessions(map) {
		state.sessions = map || {};
		notify();
	}

	function setSessionAcks(map) {
		state.sessionAcks = map || {};
		notify();
	}

	function upsertConversationMeta(id, meta) {
		const conv = state.conversations[id] || {};
		state.conversations[id] = { ...conv, meta };
		notify();
		syncPendingListeners();
	}

	function removeConversation(id) {
		delete state.conversations[id];
		detachPendingListener(id);
		rebuildPendingsFlat();
		notify();
	}

	function upsertAdminNotification(key, n) {
		state.adminNotifications[key] = n;
		notify();
	}

	function setHealth(health) {
		state.health = {
			reachable: health.reachable,
			healthy: health.healthy,
			totalAnswered: health.totalAnswered,
		};
		notify();
	}

	function setPaneError(paneKey, msgOrNull) {
		if (msgOrNull === null || msgOrNull === undefined) {
			delete state.paneErrors[paneKey];
		} else {
			state.paneErrors[paneKey] = msgOrNull;
		}
		notify();
	}

	function setAwayOffDialogOpen(open) {
		state.ui.awayOffDialogOpen = open;
		notify();
	}

	function setLeftCollapsed(value) {
		state.ui.leftCollapsed = !!value;
		storage.setItem('sb.leftCollapsed', String(state.ui.leftCollapsed));
		notify();
	}

	function toggleLeftCollapsed() {
		setLeftCollapsed(!state.ui.leftCollapsed);
	}

	function toggleRightCollapsed() {
		state.ui.rightCollapsed = !state.ui.rightCollapsed;
		storage.setItem('sb.rightCollapsed', String(state.ui.rightCollapsed));
		notify();
	}

	function toggleSessionsCollapsed() {
		state.ui.sessionsCollapsed = !state.ui.sessionsCollapsed;
		storage.setItem('sb.sessionsCollapsed', String(state.ui.sessionsCollapsed));
		notify();
	}

	function toggleSessionSelected(id) {
		const selected = state.ui.selectedSessionIds;
		state.ui.selectedSessionIds = selected.includes(id)
			? selected.filter((existing) => existing !== id)
			: [...selected, id];
		notify();
	}

	function clearSessionSelection() {
		state.ui.selectedSessionIds = [];
		notify();
	}

	function ackSession(sessionId) {
		const c = ackSessionCmd(sessionId, fb.nowIso);
		return guardedWrite('global', () => fb.setValue(c.path, c.value));
	}

	async function conveneSelected({ target, title } = {}) {
		const c = conveneCmd({ sessionIds: state.ui.selectedSessionIds, target, title }, fb.nowIso);
		const ok = await guardedWrite('global', () => fb.pushValue(c.path, c.value));
		if (ok) {
			clearSessionSelection();
		}
		return ok;
	}

	function spawnFresh(opts) {
		const c = spawnFreshCmd(opts, fb.nowIso);
		return guardedWrite('global', () => fb.pushValue(c.path, c.value));
	}

	function awayOn() {
		const c = awayOnCmd(fb.nowIso);
		return guardedWrite('global', () => fb.pushValue(c.path, c.value));
	}

	function awayOff(opts) {
		const c = awayOffCmd(opts || {}, fb.nowIso);
		return guardedWrite('global', () => fb.pushValue(c.path, c.value));
	}

	function setHidden(convId, hidden) {
		const c = setHiddenCmd(convId, hidden);
		return guardedWrite('global', () => fb.setValue(c.path, c.value));
	}

	function requestClaudeStatus(action) {
		return guardedWrite('global', async () => {
			const resp = await requestStatus(action);
			if (!resp || !resp.ok) {
				throw new Error(resp ? `HTTP ${resp.status}` : 'no response');
			}
		});
	}

	// Drag-to-resize the left conversation list. Width is clamped to a sane range
	// and persisted, like the collapse flags. Only meaningful when not collapsed.
	function setLeftWidth(px) {
		state.ui.leftWidth = clampLeftWidth(px);
		storage.setItem('sb.leftWidth', String(state.ui.leftWidth));
		notify();
	}

	function mergeConversationMessages(id, map) {
		const conv = state.conversations[id] || {};
		const messages = { ...(conv.messages || {}), ...(map || {}) };
		state.conversations[id] = { ...conv, messages };
		notify();
	}

	function mergeConversationMembers(id, map) {
		const conv = state.conversations[id] || {};
		state.conversations[id] = { ...conv, members: map || {} };
		notify();
	}

	function mergeConversationAgentStatus(id, map) {
		const conv = state.conversations[id] || {};
		state.conversations[id] = { ...conv, agentStatus: map || {} };
		notify();
	}

	function mergeConversationPending(id, map) {
		const conv = state.conversations[id] || {};
		state.conversations[id] = { ...conv, pending: map || {} };
		rebuildPendingsFlat();
		notify();
	}

	function selectConversation(id) {
		detachSelectionListeners();
		state.selectedConversationId = id;
		notify();
		if (id === null || id === undefined) {
			return;
		}
		// The synchronous try/catch only guards the attach; a permission-denied
		// arrives asynchronously on the error callback, so route that to the
		// detail pane too (M4) — otherwise a denied read leaves the pane blank.
		const onDetailError = (err) => setPaneError('detail', String(err && err.message ? err.message : err));
		try {
			selectionUnsubs.push(fb.onValue(paths.messages(id), (val) => mergeConversationMessages(id, val || {}), onDetailError));
			selectionUnsubs.push(fb.onValue(paths.membersActive(id), (val) => mergeConversationMembers(id, val || {}), onDetailError));
			selectionUnsubs.push(fb.onValue(paths.agentStatus(id), (val) => mergeConversationAgentStatus(id, val || {}), onDetailError));
		} catch (err) {
			setPaneError('detail', String(err && err.message ? err.message : err));
		}
	}

	function retrySelectedConversation() {
		setPaneError('detail', null);
		selectConversation(state.selectedConversationId);
	}

	function startGlobalListeners() {
		// A denied read on any global listener means this account cannot read the
		// database at all; surface it (back to the sign-in gate) rather than
		// silently blanking the dashboard (M4).
		for (const unsub of globalUnsubs) {
			unsub();
		}
		globalUnsubs = [];
		const onReadError = (err) => setGlobalReadError(err);
		globalUnsubs.push(fb.onChildAdded(paths.conversations(), (val, key) => {
			if (val && val.meta) {
				upsertConversationMeta(key, val.meta);
			}
		}, onReadError));
		globalUnsubs.push(fb.onChildChanged(paths.conversations(), (val, key) => {
			if (val && val.meta) {
				upsertConversationMeta(key, val.meta);
			}
		}, onReadError));
		globalUnsubs.push(fb.onChildRemoved(paths.conversations(), (_val, key) => {
			removeConversation(key);
		}, onReadError));
		globalUnsubs.push(fb.onValue(paths.globalAway(), (val) => setGlobalAway(!!val), onReadError));
		globalUnsubs.push(fb.onValue(paths.wslAvailable(), (val) => setWslAvailable(!!val), onReadError));
		globalUnsubs.push(fb.onValue(paths.widgetRings(), (val) => setWidgetRings(val || {}), onReadError));
		globalUnsubs.push(fb.onValue(paths.widgetQuota(), (val) => setWidgetQuota(val || null), onReadError));
		globalUnsubs.push(fb.onValue(paths.widgetStatus(), (val) => setWidgetStatus(val || null), onReadError));
		globalUnsubs.push(fb.onValue(paths.widgetPushedAt(), (val) => setWidgetPushedAt(val || null), onReadError));
		globalUnsubs.push(fb.onValue(paths.sessions(), (val) => setSessions(val || {}), onReadError));
		globalUnsubs.push(fb.onValue(paths.sessionAcks(), (val) => setSessionAcks(val || {}), onReadError));
		globalUnsubs.push(fb.onChildAdded(paths.adminNotifications(), (val, key) => upsertAdminNotification(key, val), onReadError));
		globalUnsubs.push(fb.onChildChanged(paths.adminNotifications(), (val, key) => upsertAdminNotification(key, val), onReadError));
	}

	// --- private helpers -------------------------------------------------

	function syncPendingListeners() {
		for (const id of Object.keys(state.conversations)) {
			const conv = state.conversations[id];
			const active = !!conv && !!conv.meta && conv.meta.state === 'active';
			if (active && !pendingUnsubsByConv.has(id)) {
				const unsub = fb.onValue(
					paths.pendingQuestions(id),
					(val) => mergeConversationPending(id, val || {}),
					(err) => setPaneError('global', String(err && err.message ? err.message : err)),
				);
				pendingUnsubsByConv.set(id, unsub);
			} else if (!active && pendingUnsubsByConv.has(id)) {
				detachPendingListener(id);
				// The listener is gone, so the server's removal update (e.g. from
				// mark_question_cancelled) will never arrive. Clear the already-merged
				// pending so a stale, answerable question does not freeze in local state.
				if (conv) {
					state.conversations[id] = { ...conv, pending: {} };
				}
				rebuildPendingsFlat();
				notify();
			}
		}
	}

	function detachPendingListener(id) {
		const unsub = pendingUnsubsByConv.get(id);
		if (unsub) {
			unsub();
			pendingUnsubsByConv.delete(id);
		}
	}

	function detachSelectionListeners() {
		for (const unsub of selectionUnsubs) {
			unsub();
		}
		selectionUnsubs = [];
	}

	function rebuildPendingsFlat() {
		const flat = [];
		const seenKeys = new Set();
		for (const convId of Object.keys(state.conversations)) {
			const conv = state.conversations[convId];
			if (!conv || !conv.meta || conv.meta.state !== 'active') {
				continue;
			}
			const pending = conv.pending || {};
			for (const requestId of Object.keys(pending)) {
				const record = pending[requestId];
				if (!record || record.cancelled === true) {
					continue;
				}
				const key = `${convId}\u0000${requestId}`;
				seenKeys.add(key);
				let firstObservedMs = firstObservedByKey.get(key);
				if (firstObservedMs === undefined) {
					firstObservedMs = nowMs();
					firstObservedByKey.set(key, firstObservedMs);
				}
				flat.push({
					convId,
					requestId,
					sender: record.sender,
					questionText: record.questionText,
					suggestions: record.suggestions,
					msgId: record.msgId,
					askedAt: record.askedAt,
					firstObservedMs,
				});
			}
		}
		// Drop first-sighting stamps for pendings that are gone, so a recurring
		// (convId,requestId) is re-stamped fresh rather than reusing a stale time.
		for (const key of [...firstObservedByKey.keys()]) {
			if (!seenKeys.has(key)) {
				firstObservedByKey.delete(key);
			}
		}
		state.pendingsFlat = flat;
	}

	// Run a write, surfacing any failure to the given pane. Resolves true iff the
	// write committed, so a caller (e.g. AnswerBox) can clear its input on success only.
	async function guardedWrite(pane, thunk) {
		try {
			await thunk();
			return true;
		} catch (err) {
			setPaneError(pane, String(err && err.message ? err.message : err));
			return false;
		}
	}

	function sendAnswer(convId, requestId, text, sender) {
		const c = answerCmd(convId, requestId, text, sender, fb.nowIso);
		return guardedWrite('detail', () => fb.setValue(c.path, c.value));
	}

	function restoreLine(convId, prompt) {
		const c = resumeCmd({ sourceConversationId: convId, prompt: prompt || undefined }, fb.nowIso);
		return guardedWrite('detail', () => fb.pushValue(c.path, c.value));
	}

	function patchLine(sourceId, targetId) {
		const c = combineCmd({ sourceConversationId: sourceId, targetConversationId: targetId }, fb.nowIso);
		return guardedWrite('detail', () => fb.pushValue(c.path, c.value));
	}

	function dropLine(convId) {
		const c = forceEndCmd({ conversationId: convId }, fb.nowIso);
		return guardedWrite('detail', () => fb.pushValue(c.path, c.value));
	}

	return {
		getState,
		subscribe,
		startGlobalListeners,
		setAuthed,
		setAuthError,
		setGlobalReadError,
		retrySignIn,
		setGlobalAway,
		setWslAvailable,
		setWidgetRings,
		setWidgetQuota,
		setWidgetStatus,
		setWidgetPushedAt,
		setSessions,
		setSessionAcks,
		toggleSessionSelected,
		clearSessionSelection,
		ackSession,
		conveneSelected,
		spawnFresh,
		awayOn,
		awayOff,
		setHidden,
		requestClaudeStatus,
		upsertConversationMeta,
		removeConversation,
		upsertAdminNotification,
		setHealth,
		setPaneError,
		setAwayOffDialogOpen,
		selectConversation,
		retrySelectedConversation,
		toggleLeftCollapsed,
		setLeftCollapsed,
		toggleRightCollapsed,
		toggleSessionsCollapsed,
		setLeftWidth,
		mergeConversationMessages,
		mergeConversationMembers,
		mergeConversationAgentStatus,
		mergeConversationPending,
		sendAnswer,
		restoreLine,
		patchLine,
		dropLine,
	};
}

const LEFT_WIDTH_DEFAULT = 280;
const LEFT_WIDTH_MIN = 180;
const LEFT_WIDTH_MAX = 560;

function clampLeftWidth(px) {
	const n = Number(px);
	if (!Number.isFinite(n)) {
		return LEFT_WIDTH_DEFAULT;
	}
	return Math.max(LEFT_WIDTH_MIN, Math.min(LEFT_WIDTH_MAX, Math.round(n)));
}

function readStoredLeftWidth(storage) {
	const raw = storage.getItem('sb.leftWidth');
	if (raw == null || raw === '') {
		return LEFT_WIDTH_DEFAULT;
	}
	return clampLeftWidth(raw);
}

function initialState(storage) {
	return {
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
			leftCollapsed: storage.getItem('sb.leftCollapsed') === 'true',
			rightCollapsed: storage.getItem('sb.rightCollapsed') === 'true',
			leftWidth: readStoredLeftWidth(storage),
			awayOffDialogOpen: false,
			sessionsCollapsed: storage.getItem('sb.sessionsCollapsed') === 'true',
			selectedSessionIds: [],
		},
		paneErrors: {},
	};
}
