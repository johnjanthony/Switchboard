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

export function oldestPendingAgeSeconds(pendingsFlat, messageTimestampResolver, nowMs) {
	if (!pendingsFlat || pendingsFlat.length === 0) {
		return null;
	}
	let oldestAge = null;
	for (const pending of pendingsFlat) {
		const isoTs = pending.msgId != null ? messageTimestampResolver[pending.msgId] : undefined;
		let originMs;
		if (isoTs !== undefined && isoTs !== null) {
			originMs = Date.parse(isoTs);
		} else {
			originMs = pending.firstObservedMs;
		}
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
	if (seconds < 60) {
		return `${Math.round(seconds)}s`;
	}
	if (seconds < 3600) {
		return `${Math.round(seconds / 60)}m`;
	}
	if (seconds < 86400) {
		return `${Math.round(seconds / 3600)}h`;
	}
	return `${Math.round(seconds / 86400)}d`;
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
