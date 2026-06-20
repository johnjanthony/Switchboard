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
