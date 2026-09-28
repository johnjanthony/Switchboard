// Pure derivation functions ported from the Android ConversationPolicy.
// No I/O, no Firebase, no store coupling: every input is a plain value.

export function memberState(member) {
	if (member && member.session_lost_permanently === true) {
		return 'lost';
	}
	if (member && member.alive === true) {
		return 'alive';
	}
	return 'dormant';
}

export function isActive(meta) {
	return !!meta && meta.state === 'active';
}

export function isThinking(agentStatusMap, nowMs = Date.now()) {
	if (!agentStatusMap) return false;
	const RECENCY_MS = 30 * 60 * 1000;
	for (const sender of Object.keys(agentStatusMap)) {
		const status = agentStatusMap[sender];
		if (!status) continue;
		let updatedAt = NaN;
		if (typeof status.updatedAt === 'number') {
			updatedAt = status.updatedAt;
		} else if (typeof status.updated_at === 'number') {
			updatedAt = status.updated_at;
		} else if (typeof status.updated_at === 'string') {
			updatedAt = Date.parse(status.updated_at);
		}
		const fresh = !Number.isNaN(updatedAt) && (nowMs - updatedAt) < RECENCY_MS;
		if (fresh && status.state && status.state !== 'idle' && status.state !== 'clear') {
			return true;
		}
	}
	return false;
}

export function agentStatusLabel(agentStatusMap, nowMs = Date.now()) {
	if (!agentStatusMap) return null;
	const RECENCY_MS = 30 * 60 * 1000;
	for (const sender of Object.keys(agentStatusMap)) {
		const status = agentStatusMap[sender];
		if (!status) continue;
		let updatedAt = NaN;
		if (typeof status.updatedAt === 'number') {
			updatedAt = status.updatedAt;
		} else if (typeof status.updated_at === 'number') {
			updatedAt = status.updated_at;
		} else if (typeof status.updated_at === 'string') {
			updatedAt = Date.parse(status.updated_at);
		}
		const fresh = !Number.isNaN(updatedAt) && (nowMs - updatedAt) < RECENCY_MS;
		if (fresh && status.state && status.state !== 'idle' && status.state !== 'clear') {
			if (status.detail) {
				return `${status.state}: ${status.detail}`;
			}
			return status.state;
		}
	}
	return null;
}

export function pendingQuestionText(pendingMap) {
	if (!pendingMap) return null;
	for (const id of Object.keys(pendingMap)) {
		const req = pendingMap[id];
		if (!req || req.cancelled) continue;
		const q = req.question || req.questionText || req.prompt;
		if (q && typeof q === 'string') return q;
	}
	return null;
}

export function pendingCountFor(pendingMap) {
	if (!pendingMap) {
		return 0;
	}
	let count = 0;
	for (const requestId of Object.keys(pendingMap)) {
		const record = pendingMap[requestId];
		if (record && record.cancelled !== true) {
			count += 1;
		}
	}
	return count;
}

export function globalPendingCount(convs) {
	if (!convs) {
		return 0;
	}
	let total = 0;
	for (const convId of Object.keys(convs)) {
		const conv = convs[convId];
		if (conv && isActive(conv.meta)) {
			total += pendingCountFor(conv.pending);
		}
	}
	return total;
}

// Resolve the title of a conversation's predecessor (the one it was continued
// from), or null when there is nothing to show: no continued_from pointer, or the
// pointer targets a conversation absent from [conversations] (aged out / not yet
// loaded). The caller hides the "Continued from" banner rather than render a dead
// affordance. Mirrors ConversationPolicy.predecessorTitle on the phone.
export function predecessorTitle(conv, conversations) {
	const predecessorId = conv && conv.meta ? conv.meta.continued_from : null;
	if (!predecessorId || !conversations) {
		return null;
	}
	const predecessor = conversations[predecessorId];
	return predecessor && predecessor.meta ? (predecessor.meta.title || null) : null;
}

export function oldestPendingAgeSeconds(pendingsFlat, nowMs) {
	if (!pendingsFlat || pendingsFlat.length === 0) {
		return null;
	}
	let oldestAge = null;
	for (const pending of pendingsFlat) {
		const askedMs = pending.askedAt != null ? Date.parse(pending.askedAt) : NaN;
		const originMs = Number.isNaN(askedMs) ? pending.firstObservedMs : askedMs;
		const ageSeconds = (nowMs - originMs) / 1000;
		if (oldestAge === null || ageSeconds > oldestAge) {
			oldestAge = ageSeconds;
		}
	}
	return oldestAge;
}

// Join a conversation member to its live context ring, if Watchtower is tracking
// that session. Rings are keyed by Claude Code session_id, which equals the
// member's cli_session_id. Returns the ring object or null.
export function ringForMember(member, rings) {
	if (!member || !rings) {
		return null;
	}
	const sid = member.cli_session_id;
	if (!sid) {
		return null;
	}
	return rings[sid] || null;
}

// Severity bucket for a context-fill fraction (0..1), matching Watchtower's
// SeverityClassifier.For: red above 0.80, amber from 0.50, else green; cold when
// there is no usable number.
export function ringSeverity(pct) {
	const p = Number(pct);
	if (pct == null || Number.isNaN(p)) {
		return 'cold';
	}
	if (p > 0.80) {
		return 'red';
	}
	if (p >= 0.50) {
		return 'amber';
	}
	return 'green';
}

const SESSION_CHIPS = {
	active: { label: 'active', cls: 'chip-active' },
	idle: { label: 'idle', cls: 'chip-idle' },
	awaiting_human: { label: 'needs you', cls: 'chip-awaiting-human' },
	awaiting_agent: { label: 'waiting on agent', cls: 'chip-awaiting-agent' },
	ended: { label: 'ended', cls: 'chip-ended' },
	lost: { label: 'lost', cls: 'chip-lost' },
};

export function sessionChip(record) {
	if (record && record.blocked_on_approval) {
		return { label: 'needs approval', cls: 'chip-needs-approval' };
	}
	const state = record && record.state ? record.state : 'idle';
	return SESSION_CHIPS[state] || { label: state, cls: 'chip-idle' };
}

export function projectTail(cwd) {
	if (!cwd) {
		return '';
	}
	const parts = String(cwd).split(/[\\/]/).filter(Boolean);
	return parts.length ? parts[parts.length - 1] : '';
}

export function sessionAgeSeconds(record, nowMs) {
	const iso = record ? record.last_event_at : null;
	if (!iso) {
		return null;
	}
	const t = Date.parse(iso);
	if (Number.isNaN(t)) {
		return null;
	}
	return (nowMs - t) / 1000;
}

export function formatAge(seconds) {
	if (seconds == null) {
		return '';
	}
	const s = Math.floor(seconds);
	if (s < 60) {
		return `${s}s`;
	}
	if (s < 3600) {
		return `${Math.floor(s / 60)}m`;
	}
	if (s < 86400) {
		return `${Math.floor(s / 3600)}h`;
	}
	return `${Math.floor(s / 86400)}d`;
}

export function sortSessionEntries(sessionsMap) {
	return Object.keys(sessionsMap || {})
		.map((id) => ({ id, record: sessionsMap[id] || {} }))
		.sort((a, b) => String(b.record.last_event_at || '').localeCompare(String(a.record.last_event_at || '')));
}

const SENSOR_FRESH_SECONDS = 120;

export function sensorOffline(pushedAtIso, nowMs) {
	if (!pushedAtIso) {
		return true;
	}
	const t = Date.parse(pushedAtIso);
	if (Number.isNaN(t)) {
		return true;
	}
	return (nowMs - t) / 1000 > SENSOR_FRESH_SECONDS;
}

// Display name for a session row: custom/ai name wins regardless of name_source
// (name_source stays on the record for styling only), then sender, then the
// last path segment of cwd, then a placeholder.
export function sessionLabel(record) {
	if (record && record.name) {
		return record.name;
	}
	if (record && record.sender) {
		return record.sender;
	}
	const tail = record ? projectTail(record.cwd) : '';
	return tail || '(unknown)';
}

const TERMINAL_SESSION_STATES = new Set(['ended', 'lost']);

// The single live session bound to a conversation, or null when there are zero
// or several. Rows use it to show that agent's state chip.
//
// Bindings, not member counts, are what a row can actually see: the Operator
// subscribes to the whole session roster globally but to members_active only for
// the SELECTED conversation. A dormant member is an unbound session, so a
// two-member conversation with one dormant agent resolves to its live survivor -
// which is the honest reading, since the chip describes the agent that is here.
export function soleSessionFor(convId, sessionsMap) {
	if (!convId || !sessionsMap) {
		return null;
	}
	let found = null;
	for (const id of Object.keys(sessionsMap)) {
		const record = sessionsMap[id];
		if (!record || record.conversation_id !== convId) {
			continue;
		}
		if (TERMINAL_SESSION_STATES.has(record.state)) {
			continue;
		}
		if (found) {
			return null;
		}
		found = record;
	}
	return found;
}

// True when an idle session has an unacknowledged event: no ack yet, or the
// session's last_event_at is newer than the stored ack. Uses Date.parse (not
// string comparison) because the server stamps "+00:00" while fb.nowIso stamps
// "Z", so equal-second timestamps compare unequal lexicographically.
export function needsAttention(record, ackIso) {
	if (!record) {
		return false;
	}
	if (record.blocked_on_approval) {
		return true;
	}
	if (record.state !== 'idle') {
		return false;
	}
	const eventMs = Date.parse(record.last_event_at);
	if (Number.isNaN(eventMs)) {
		return false;
	}
	if (!ackIso) {
		return true;
	}
	return eventMs > Date.parse(ackIso);
}

const WAKE_PATH_HINTS = {
	awaiting_agent: 'wakes instantly',
	awaiting_human: 'on next phone answer',
	active: 'at end of current turn',
	idle: "on John's next prompt",
	ended: 'Resume into conversation',
	lost: 'Resume into conversation',
};

export function wakePathHint(record) {
	const state = record ? record.state : undefined;
	return WAKE_PATH_HINTS[state] || '';
}

// Weak, tooltip-only hint: a session that has been sitting inside a tool call
// for a while with no title-bar verdict yet (no "working"/"star" heartbeat) may
// be stuck on an approval prompt Switchboard can't see directly. Any non-null
// title_state is the heartbeat winning, so it suppresses the hint; the
// blocked_on_approval flag (a hard signal) also suppresses it since that case
// already has its own chip and needsAttention badge. Never counts toward
// needsAttention - this is tooltip text only.
export function approvalHint(record, nowMs) {
	if (!record || !record.in_tool || record.blocked_on_approval) {
		return '';
	}
	if (record.title_state != null) {
		return '';
	}
	const ageSeconds = sessionAgeSeconds(record, nowMs);
	if (ageSeconds == null || ageSeconds <= 300) {
		return '';
	}
	return 'possibly waiting on approval';
}

const CONVENABLE_STATES = new Set(['active', 'idle', 'awaiting_human', 'awaiting_agent']);
const RESUMABLE_STATES = new Set(['ended', 'lost']);

export function isConvenable(record) {
	if (!record) {
		return false;
	}
	if (CONVENABLE_STATES.has(record.state)) {
		return true;
	}
	return RESUMABLE_STATES.has(record.state) && !!record.cwd;
}

// Spawn-dialog pick lists from the server-published spawn_options catalog.
// Absent catalog -> empty lists -> the dialog offers only "Default (CLI)".
export function modelOptionsFor(spawnOptions, agent) {
	const cli = spawnOptions ? spawnOptions[agent === 'antigravity' ? 'antigravity' : 'claude'] : null;
	const models = cli && Array.isArray(cli.models) ? cli.models : [];
	return models.map((m) => m && m.id).filter(Boolean);
}

export function effortOptionsFor(spawnOptions, agent, modelId) {
	if (agent === 'antigravity') return [];
	const cli = spawnOptions ? spawnOptions.claude : null;
	const models = cli && Array.isArray(cli.models) ? cli.models : [];
	if (!modelId) {
		const seen = [];
		for (const m of models) {
			for (const e of (m && m.efforts) || []) {
				if (!seen.includes(e)) seen.push(e);
			}
		}
		return seen;
	}
	const entry = models.find((m) => m && m.id === modelId);
	return (entry && Array.isArray(entry.efforts) && entry.efforts) || [];
}

// ---- Pending sends: composer messages the server has not written back ----
// A send is a /message_commands entry until the server consumes it; the server
// deletes it only after writing the /messages row, which carries the entry's
// key as command_id. These derivations turn the queue plus the conversation's
// rows into the pending rows the transcript shows under its real messages.

export const SENDING_WINDOW_MS = 20 * 1000;
export const NOT_DELIVERED_GRACE_MS = 5 * 1000;
// Mirrors the server's COMMAND_TTL_SECONDS (server/command_freshness.py), which
// is not published to clients.
export const COMMAND_TTL_MS = 600 * 1000;

export const PENDING_SEND_STATUS = {
	sending: 'sending...',
	offline: 'Offline: sends when you reconnect.',
	notPickedUp: "Server hasn't taken this. It delivers if the server returns within 10 min.",
	expired: 'Expired: the server will drop this, not deliver it.',
	notDelivered: 'Not delivered',
};

function isQueuedMessage(cmd) {
	return !!cmd && typeof cmd.conversation_id === 'string' && typeof cmd.text === 'string';
}

export function commandIdsIn(messages) {
	const ids = new Set();
	for (const m of Object.values(messages || {})) {
		if (m && typeof m.command_id === 'string') ids.add(m.command_id);
	}
	return ids;
}

function queuedSendState(issuedAt, nowMs, connected) {
	const issuedMs = Date.parse(issuedAt);
	// An unparseable stamp cannot age out, but it is still queued: show it as
	// not picked up rather than an optimistic "sending" that never escalates.
	const age = Number.isFinite(issuedMs) ? nowMs - issuedMs : SENDING_WINDOW_MS;
	if (Number.isFinite(issuedMs) && age >= COMMAND_TTL_MS) return 'expired';
	if (!connected) return 'offline';
	if (age >= SENDING_WINDOW_MS) return 'notPickedUp';
	return 'sending';
}

function issuedSortKey(issuedAt) {
	const ms = Date.parse(issuedAt);
	return Number.isFinite(ms) ? ms : Number.MAX_SAFE_INTEGER;
}

export function derivePendingSends({ convId, queued, messages, seen, nowMs, connected }) {
	const q = queued || {};
	const delivered = commandIdsIn(messages);
	const rows = [];
	for (const [key, cmd] of Object.entries(q)) {
		if (!isQueuedMessage(cmd) || cmd.conversation_id !== convId || delivered.has(key)) continue;
		rows.push({ key, text: cmd.text, issuedAt: cmd.issued_at || null, state: queuedSendState(cmd.issued_at, nowMs, connected) });
	}
	for (const [key, entry] of Object.entries(seen || {})) {
		if (!entry || entry.conversationId !== convId || entry.goneAtMs == null) continue;
		if (key in q || delivered.has(key)) continue;
		if (nowMs - entry.goneAtMs < NOT_DELIVERED_GRACE_MS) continue;
		rows.push({ key, text: entry.text, issuedAt: entry.issuedAt, state: 'notDelivered' });
	}
	rows.sort((a, b) => issuedSortKey(a.issuedAt) - issuedSortKey(b.issuedAt));
	return rows;
}

// Operator only has the SELECTED conversation's messages loaded, so a send that
// leaves the queue while its conversation is not selected cannot be checked
// against its row: drop it instead of stamping it gone (liveConvId is the
// selected conversation, or null).
export function updateSeenSends(seen, queued, nowMs, liveConvId, deliveredIds) {
	const q = queued || {};
	const next = {};
	for (const [key, entry] of Object.entries(seen || {})) {
		if (key in q || entry.goneAtMs != null) {
			next[key] = entry;
			continue;
		}
		if (deliveredIds.has(key) || entry.conversationId !== liveConvId) continue;
		next[key] = { ...entry, goneAtMs: nowMs };
	}
	for (const [key, cmd] of Object.entries(q)) {
		if (next[key] || !isQueuedMessage(cmd)) continue;
		next[key] = { conversationId: cmd.conversation_id, text: cmd.text, issuedAt: cmd.issued_at || null, goneAtMs: null };
	}
	return next;
}

export function pruneDeliveredSends(seen, deliveredIds) {
	const next = {};
	for (const [key, entry] of Object.entries(seen || {})) {
		if (entry.goneAtMs != null && deliveredIds.has(key)) continue;
		next[key] = entry;
	}
	return next;
}

// A gone send inside its grace window has no row yet, so "rows exist" alone
// would never start the clock that makes its Not delivered row appear.
export function pendingSendsNeedTick(convId, queued, seen) {
	for (const cmd of Object.values(queued || {})) {
		if (isQueuedMessage(cmd) && cmd.conversation_id === convId) return true;
	}
	for (const entry of Object.values(seen || {})) {
		if (entry && entry.conversationId === convId && entry.goneAtMs != null) return true;
	}
	return false;
}

export function restoreDraft(current, text) {
	const draft = current == null ? '' : String(current);
	return draft.trim() === '' ? text : `${draft}\n${text}`;
}
