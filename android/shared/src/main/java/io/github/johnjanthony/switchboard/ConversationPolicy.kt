package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ConversationRow

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
 * Whether the Firebase DB listeners should be attached now. They must be attached
 * only once an authenticated user exists: attaching them before the async Google
 * sign-in completes makes the unauthenticated listens fail Permission denied under
 * auth-required rules, and Firebase cancels them with no auto-retry, leaving the UI
 * empty. The alreadyAttached guard keeps attachment idempotent (no duplicate
 * listeners when the auth state fires again, e.g. on token refresh).
 */
fun shouldAttachFirebaseListeners(hasAuthedUser: Boolean, alreadyAttached: Boolean): Boolean =
	hasAuthedUser && !alreadyAttached
