// The dashboard's single view-model store. Owns all projected state and is the
// only writer of it; components drive everything through the actions below and
// never assign onto the store or guard its methods with "?".
//
// deps: { fb, paths, storage, nowMs }
//   fb      - the firebase.js wrapper (onAuth/signIn/on*/pushValue/setValue/updateValue/nowIso)
//   paths   - the schema.js path builders
//   storage - localStorage-like { getItem, setItem }
//   nowMs   - () => epoch ms, used to stamp pendingsFlat firstObservedMs

export function createStore(deps) {
	const { fb, paths, storage, nowMs } = deps;

	const state = initialState(storage);
	const subscribers = new Set();

	// Per-(convId,requestId) first-sighting timestamps, kept private so
	// pendingsFlat.firstObservedMs is stable across re-emits.
	const firstObservedByKey = new Map();
	// Lazy per-selection listener unsubscribes, detached on the next selection.
	let selectionUnsubs = [];
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

	function retrySignIn() {
		state.authError = null;
		notify();
		fb.signIn();
	}

	function setGlobalAway(value) {
		state.globalAway = value;
		notify();
	}

	function setOpenConversationId(idOrNull) {
		state.openConversationId = idOrNull;
		notify();
	}

	function setWslAvailable(value) {
		state.wslAvailable = value;
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

	function toggleLeftCollapsed() {
		state.ui.leftCollapsed = !state.ui.leftCollapsed;
		storage.setItem('sb.leftCollapsed', String(state.ui.leftCollapsed));
		notify();
	}

	function toggleRightCollapsed() {
		state.ui.rightCollapsed = !state.ui.rightCollapsed;
		storage.setItem('sb.rightCollapsed', String(state.ui.rightCollapsed));
		notify();
	}

	function mergeConversationMessages(id, map) {
		const conv = state.conversations[id] || {};
		const messages = { ...(conv.messages || {}), ...(map || {}) };
		state.conversations[id] = { ...conv, messages };
		if (id === state.selectedConversationId) {
			rebuildMessageTimestampResolver(messages);
		}
		notify();
	}

	function mergeConversationMembers(id, map) {
		const conv = state.conversations[id] || {};
		state.conversations[id] = { ...conv, members: { ...(conv.members || {}), ...(map || {}) } };
		notify();
	}

	function mergeConversationAgentStatus(id, map) {
		const conv = state.conversations[id] || {};
		state.conversations[id] = { ...conv, agentStatus: { ...(conv.agentStatus || {}), ...(map || {}) } };
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
		state.messageTimestampResolver = {};
		notify();
		if (id === null || id === undefined) {
			return;
		}
		try {
			selectionUnsubs.push(fb.onValue(paths.messages(id), (val) => mergeConversationMessages(id, val || {})));
			selectionUnsubs.push(fb.onValue(paths.membersActive(id), (val) => mergeConversationMembers(id, val || {})));
			selectionUnsubs.push(fb.onValue(paths.agentStatus(id), (val) => mergeConversationAgentStatus(id, val || {})));
		} catch (err) {
			setPaneError('detail', String(err && err.message ? err.message : err));
		}
	}

	function retrySelectedConversation() {
		setPaneError('detail', null);
		selectConversation(state.selectedConversationId);
	}

	function startGlobalListeners() {
		fb.onChildAdded(paths.conversations(), (val, key) => {
			if (val && val.meta) {
				upsertConversationMeta(key, val.meta);
			}
		});
		fb.onChildChanged(paths.conversations(), (val, key) => {
			if (val && val.meta) {
				upsertConversationMeta(key, val.meta);
			}
		});
		fb.onChildRemoved(paths.conversations(), (_val, key) => {
			removeConversation(key);
		});
		fb.onValue(paths.globalAway(), (val) => setGlobalAway(!!val));
		fb.onValue(paths.openConversationId(), (val) => setOpenConversationId(val || null));
		fb.onValue(paths.wslAvailable(), (val) => setWslAvailable(!!val));
		fb.onChildAdded(paths.adminNotifications(), (val, key) => upsertAdminNotification(key, val));
		fb.onChildChanged(paths.adminNotifications(), (val, key) => upsertAdminNotification(key, val));
	}

	// --- private helpers -------------------------------------------------

	function rebuildMessageTimestampResolver(messages) {
		const resolver = {};
		for (const msgId of Object.keys(messages || {})) {
			const message = messages[msgId];
			if (message && message.timestamp !== undefined) {
				resolver[msgId] = message.timestamp;
			}
		}
		state.messageTimestampResolver = resolver;
	}

	function syncPendingListeners() {
		for (const id of Object.keys(state.conversations)) {
			const conv = state.conversations[id];
			const active = !!conv && !!conv.meta && conv.meta.state === 'active';
			if (active && !pendingUnsubsByConv.has(id)) {
				const unsub = fb.onValue(paths.pendingQuestions(id), (val) => mergeConversationPending(id, val || {}));
				pendingUnsubsByConv.set(id, unsub);
			} else if (!active && pendingUnsubsByConv.has(id)) {
				detachPendingListener(id);
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

	return {
		getState,
		subscribe,
		startGlobalListeners,
		setAuthed,
		setAuthError,
		retrySignIn,
		setGlobalAway,
		setOpenConversationId,
		setWslAvailable,
		upsertConversationMeta,
		removeConversation,
		upsertAdminNotification,
		setHealth,
		setPaneError,
		setAwayOffDialogOpen,
		selectConversation,
		retrySelectedConversation,
		toggleLeftCollapsed,
		toggleRightCollapsed,
		mergeConversationMessages,
		mergeConversationMembers,
		mergeConversationAgentStatus,
		mergeConversationPending,
	};
}

function initialState(storage) {
	return {
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
		ui: {
			leftCollapsed: storage.getItem('sb.leftCollapsed') === 'true',
			rightCollapsed: storage.getItem('sb.rightCollapsed') === 'true',
			awayOffDialogOpen: false,
		},
		paneErrors: {},
	};
}
