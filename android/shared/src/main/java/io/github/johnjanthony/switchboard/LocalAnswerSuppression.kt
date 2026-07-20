package io.github.johnjanthony.switchboard

/**
 * Per-conversation suppression of locally-answered pending questions.
 *
 * When the phone answers a question it optimistically removes the pending entry,
 * but the server's pending_questions node may still list the request until the
 * answer is processed. Suppression hides that stale listing without hiding a
 * genuine re-ask: an id stays suppressed only while it is continuously present
 * between consecutive authoritative snapshots of its conversation.
 */
class LocalAnswerSuppression {
	private val byConv = mutableMapOf<String, MutableSet<String>>()

	/** Record a locally-answered request for [convId]. */
	fun add(convId: String, requestId: String) {
		byConv.getOrPut(convId) { mutableSetOf() }.add(requestId)
	}

	/**
	 * Reconcile against a fresh authoritative snapshot for [convId], where
	 * [previous] is the prior snapshot for the same conversation (empty on
	 * first sight), and return the effective pending map with suppressed
	 * entries removed. Ids the server has caught up on (absent from [parsed])
	 * are dropped from the suppression set.
	 */
	fun <T> reconcile(convId: String, parsed: Map<String, T>, previous: Map<String, T>): Map<String, T> {
		val set = byConv[convId] ?: return parsed
		set.retainAll { it in parsed.keys && it in previous.keys }
		if (set.isEmpty()) byConv.remove(convId)
		return parsed.filterKeys { it !in set }
	}

	/** Drop all suppression state for [convId] (listener detached or conversation gone). */
	fun clear(convId: String) {
		byConv.remove(convId)
	}
}
