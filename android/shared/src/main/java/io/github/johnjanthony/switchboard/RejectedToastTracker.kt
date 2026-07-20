package io.github.johnjanthony.switchboard

/**
 * Decides whether a just-observed rejected message should raise a toast. A rejected
 * message toasts at most once, and only if it arrived after the message listener attached,
 * so the initial replay of historical rejected notices on cold start / re-attach does not
 * re-toast (REV-202). ISO-8601 timestamps sort chronologically as strings. A missing/blank
 * timestamp is treated as history. Assumes a rejection is delivered as a message carrying a
 * post-attach timestamp (the "reply withdrawn" notice is a fresh message).
 */
class RejectedToastTracker {
	private val seen = HashSet<String>()

	fun shouldToast(msgId: String, rejected: Boolean, timestamp: String?, attachedAt: String): Boolean {
		if (!rejected) return false
		if (msgId in seen) return false
		if (timestamp.isNullOrBlank() || timestamp <= attachedAt) {
			seen.add(msgId)   // history / undatable: remember so re-delivery stays silent
			return false
		}
		seen.add(msgId)
		return true
	}
}
