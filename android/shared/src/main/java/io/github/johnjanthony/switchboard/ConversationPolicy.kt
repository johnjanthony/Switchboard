package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ConversationMember
import io.github.johnjanthony.switchboard.network.ConversationRow
import io.github.johnjanthony.switchboard.network.WidgetRing

/**
 * Synthetic conversation id for the admin-notifications pseudo-row. It has no
 * backing Firebase node under conversations/<id>; any writer keyed by
 * conversation id must guard against it (R3) so nothing is ever written to
 * conversations/_admin.
 */
const val ADMIN_CONVERSATION_ID = "_admin"

/** True for synthetic conversation ids that have no real Firebase node behind them. */
fun isSyntheticConversation(conversationId: String): Boolean =
	conversationId == ADMIN_CONVERSATION_ID

/** Number of still-open pending questions on a row (cancelled ones excluded). */
fun pendingReplyCount(row: ConversationRow): Int =
	row.pendingQuestions.values.count { !it.cancelled }

/** True if the row has at least one still-open pending question. */
fun conversationNeedsReply(row: ConversationRow): Boolean =
	pendingReplyCount(row) > 0

/**
 * Partition visible conversations for the watch's pending-first triage list.
 * Hidden rows are excluded. Returns (needsReply, others), each ordered by
 * lastActivityAt descending. A row needs reply iff it has an open pending
 * question; the synthetic _admin row never does, so it lands in others.
 */
fun partitionConversationsForWatch(
	rows: Collection<ConversationRow>,
): Pair<List<ConversationRow>, List<ConversationRow>> {
	val visible = rows.filter { !it.hidden }
	val needsReply = visible.filter { conversationNeedsReply(it) }
		.sortedByDescending { it.lastActivityAt }
	val others = visible.filter { !conversationNeedsReply(it) }
		.sortedByDescending { it.lastActivityAt }
	return needsReply to others
}

/**
 * Whether a thread message is an answerable (still-open) question the user can
 * tap to reply: a question type, not already answered (no reply attached), not
 * cancelled, not rejected. Mirrors the inline rule the legacy MessageListScreen
 * used so the rewrite preserves behavior.
 */
fun isAnswerableQuestion(
	type: String,
	msgId: String,
	answeredMsgIds: Set<String>,
	cancelled: Boolean,
	rejected: Boolean,
): Boolean =
	(type == "question" || type == "ask_human") &&
		msgId !in answeredMsgIds && !cancelled && !rejected

/**
 * Bulk-respond section label (R4): the conversation title, falling back to the
 * member roster when the title is blank. Client-only; the server returns a flat
 * pending list and does not build sections.
 */
fun bulkRespondSectionLabel(title: String, memberRoster: String): String =
	title.ifBlank { memberRoster }

/**
 * Resolve the display title of a conversation's predecessor (the conversation it
 * was continued from), or null when there is nothing to show. Returns null if
 * [row] has no continued_from pointer, or the pointer targets a conversation that
 * is absent from [rows] (aged out, hidden-and-pruned, or not yet hydrated): the
 * caller hides the "Continued from" banner rather than render a dead affordance.
 */
fun predecessorTitle(row: ConversationRow, rows: Map<String, ConversationRow>): String? {
	val predecessorId = row.continuedFrom ?: return null
	return rows[predecessorId]?.title
}

/**
 * Whether the Firebase DB listeners should be attached now. They must be attached
 * only once an authenticated user exists: attaching them before the async Google
 * sign-in completes makes the unauthenticated listens fail Permission denied under
 * auth-required rules, and Firebase cancels them with no auto-retry, leaving the UI
 * empty. The alreadyAttached guard keeps attachment idempotent (no duplicate
 * listeners when the auth state fires again, e.g. on token refresh).
 */
fun shouldAttachFirebaseListeners(hasAuthedUser: Boolean, alreadyAttached: Boolean): Boolean =
	hasAuthedUser && !alreadyAttached

/** Severity bucket for a context-window fill fraction. Mirrors Watchtower's
 * SeverityClassifier and Operator's ringSeverity. */
enum class RingSeverity { GREEN, AMBER, RED, NONE }

/**
 * Join a conversation member to its live context ring, if Watchtower is tracking that
 * session. Rings are keyed by the Claude Code session_id, which equals the member's
 * cliSessionId (verified contract). Returns the ring or null (null when the member has
 * no session id or no ring is tracked). Not away-mode gated: a tracked session shows its
 * fill at any time, like Operator.
 */
fun ringForMember(member: ConversationMember, ringsBySessionId: Map<String, WidgetRing>): WidgetRing? {
	val sid = member.cliSessionId
	if (sid.isEmpty()) return null
	return ringsBySessionId[sid]
}

/**
 * Severity for a fill fraction (0..1): red above 0.80, amber from 0.50, else green; NONE
 * when there is no usable number. Thresholds match Watchtower and Operator exactly.
 */
fun ringSeverity(pct: Double?): RingSeverity = when {
	pct == null || pct.isNaN() -> RingSeverity.NONE
	pct > 0.80 -> RingSeverity.RED
	pct >= 0.50 -> RingSeverity.AMBER
	else -> RingSeverity.GREEN
}

/**
 * The ring to show on a session-list row: the most-filled member's ring, but only when
 * the fill is strictly above 50%. Below that the list row stays quiet (the per-member
 * detail in the info popover still shows all fills). Returns null when no member has a
 * tracked ring or the highest fill is at or below 50%.
 */
fun listRowContextRing(members: List<ConversationMember>, ringsBySessionId: Map<String, WidgetRing>): WidgetRing? {
	val top = members.mapNotNull { ringForMember(it, ringsBySessionId) }.maxByOrNull { it.pct }
	return top?.takeIf { it.pct > 0.50 }
}
