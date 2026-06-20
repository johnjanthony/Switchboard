// RTDB path builders. Single source of truth for the schema coupling that
// direct-RTDB reads imply, per the design's "single schema module" decision.
// Paths and field names are pinned to the verified Appendix contract.
// NOTE: unread_count and pending_responses are intentionally NOT exposed here
// for v1 (per spec): the dashboard derives pending counts by enumerating
// pending_questions children, not the pending_responses mirror counter, and
// the optional unread dot is out of v1 scope.

export function conversations() {
	return 'conversations';
}

export function conversationMeta(id) {
	return `conversations/${id}/meta`;
}

export function membersActive(id) {
	return `conversations/${id}/members_active`;
}

export function pendingQuestions(id) {
	return `conversations/${id}/pending_questions`;
}

export function agentStatus(id) {
	return `conversations/${id}/agent_status`;
}

export function messages(id) {
	return `conversations/${id}/messages`;
}

export function answer(id, requestId) {
	return `conversations/${id}/answers/${requestId}`;
}

export function metaHidden(id) {
	return `conversations/${id}/meta/hidden`;
}

export function globalAway() {
	return 'global_settings/away_mode';
}

export function openConversationId() {
	return 'global_settings/open_conversation_id';
}

export function wslAvailable() {
	return 'global_settings/wsl_available';
}

export function adminNotifications() {
	return 'admin_notifications';
}

export function awayCommands() {
	return 'away_mode_commands';
}

export function spawnCommands() {
	return 'spawn_commands';
}

export function combineCommands() {
	return 'combine_commands';
}

export function forceEndCommands() {
	return 'force_end_commands';
}
