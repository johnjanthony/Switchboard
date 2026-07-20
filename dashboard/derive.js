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
