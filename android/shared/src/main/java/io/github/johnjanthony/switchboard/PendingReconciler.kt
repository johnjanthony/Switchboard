package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ChannelMessage
import io.github.johnjanthony.switchboard.network.Pending

/**
 * The answered-question set for the per-message checkmark affordance: any message whose
 * attached_to_msg_id names another message present in the list marks that target as
 * answered. Derived from the arrival-ordered raw list; splicing reorders but never adds or
 * removes ids, so the set matches the display list. This is the single source
 * for the checkmark set on both phone and wear (REV-205). Pending state itself is NOT
 * derived here — it comes from the authoritative pending_questions node (REV-203).
 */
fun answeredQuestionMsgIds(messages: List<Pair<String, ChannelMessage>>): Set<String> {
	val ids = messages.mapTo(HashSet()) { it.first }
	return messages
		.mapNotNull { (_, m) -> m.attached_to_msg_id }
		.filter { it in ids }
		.toSet()
}

/**
 * Build a Pending from one pending_questions child's fields, or null if a required field
 * (sender, questionText) is absent. requestId is the child key. Mirrors the server node
 * written by add_pending_question_record: {sender, questionText, cancelled, msgId,
 * suggestions, ...}; presence of the child means the question is genuinely pending.
 */
fun pendingFromNode(
	requestId: String,
	sender: String?,
	questionText: String?,
	cancelled: Boolean,
	msgId: String?,
	suggestions: List<String>?,
): Pending? {
	if (sender == null || questionText == null) return null
	return Pending(
		sender = sender,
		requestId = requestId,
		questionText = questionText,
		cancelled = cancelled,
		msgId = msgId,
		suggestions = suggestions,
	)
}
