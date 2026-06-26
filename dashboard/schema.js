// RTDB read-path builders: the single source of truth for the paths the store
// subscribes to (direct-RTDB reads). Field names are pinned to the verified
// Appendix contract. Write-command paths (away/spawn/combine/force-end/answer/
// meta-hidden) live with their builders in commands.js.
// NOTE: unread_count and pending_responses are intentionally NOT exposed here
// for v1 (per spec): the dashboard derives pending counts by enumerating
// pending_questions children, not the pending_responses mirror counter, and
// the optional unread dot is out of v1 scope.

export function conversations() {
	return 'conversations';
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

export function widgetRings() {
	return 'widget/rings';
}

export function widgetQuota() {
	return 'widget/quota';
}

export function widgetPushedAt() {
	return 'widget/pushed_at';
}

export function widgetStatus() {
	return 'widget/status';
}
