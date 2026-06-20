// Pure command builders. Each returns { path, value } describing one RTDB write.
// No I/O: the caller (the store) feeds the result to fb.pushValue / fb.setValue.
// Field names and command shapes are pinned to the verified Appendix contract.

export function answerCmd(convId, requestId, text, sender, nowIsoFn) {
	return {
		path: `conversations/${convId}/answers/${requestId}`,
		value: { text, sender, request_id: requestId, written_at: nowIsoFn() },
	};
}

export function awayOnCmd(nowIsoFn) {
	return {
		path: 'away_mode_commands',
		value: { type: 'enter_global', issued_at: nowIsoFn() },
	};
}

export function awayOffCmd({ decision, defaultText } = {}, nowIsoFn) {
	const value = { type: 'exit_global', issued_at: nowIsoFn() };
	if (decision !== undefined && decision !== null) {
		value.decision = decision;
	}
	if (defaultText !== undefined && defaultText !== null) {
		value.default_text = defaultText;
	}
	return { path: 'away_mode_commands', value };
}

export function spawnFreshCmd({ surface, project, prompt, targetConversationId } = {}, nowIsoFn) {
	const value = { type: 'fresh', surface, project, issued_at: nowIsoFn() };
	if (prompt !== undefined && prompt !== null) {
		value.prompt = prompt;
	}
	if (targetConversationId !== undefined && targetConversationId !== null) {
		value.target_conversation_id = targetConversationId;
	}
	return { path: 'spawn_commands', value };
}

export function resumeCmd({ sourceConversationId, prompt } = {}, nowIsoFn) {
	const value = { type: 'resume', source_conversation_id: sourceConversationId, issued_at: nowIsoFn() };
	if (prompt !== undefined && prompt !== null) {
		value.prompt = prompt;
	}
	return { path: 'spawn_commands', value };
}

export function combineCmd({ sourceConversationId, targetConversationId } = {}, nowIsoFn) {
	return {
		path: 'combine_commands',
		value: { source_conversation_id: sourceConversationId, target_conversation_id: targetConversationId, issued_at: nowIsoFn() },
	};
}

export function forceEndCmd({ conversationId } = {}, nowIsoFn) {
	return {
		path: 'force_end_commands',
		value: { conversation_id: conversationId, issued_at: nowIsoFn() },
	};
}

export function setHiddenCmd(convId, hidden) {
	return { path: `conversations/${convId}/meta/hidden`, value: hidden };
}
