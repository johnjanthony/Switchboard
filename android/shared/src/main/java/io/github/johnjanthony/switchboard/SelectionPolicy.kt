package io.github.johnjanthony.switchboard

/**
 * Decide whether an arriving message may auto-select its conversation.
 *
 * True only on surfaces that opted in (Wear), when nothing is selected, for
 * visible active conversations. The phone must never auto-select on message
 * arrival: selection is navigation-driven there, and force-selecting meant
 * every later message to that conversation zeroed its unread badge while
 * John sat on Page A (H07). Pure function so the gating is unit-testable
 * without Android/Firebase plumbing.
 */
fun shouldAutoSelectOnMessageArrival(
	autoSelectEnabled: Boolean,
	selectedConversationId: String?,
	rowHidden: Boolean,
	rowState: String,
): Boolean =
	autoSelectEnabled && selectedConversationId == null && !rowHidden && rowState == "active"
