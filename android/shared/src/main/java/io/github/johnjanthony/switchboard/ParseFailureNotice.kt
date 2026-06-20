package io.github.johnjanthony.switchboard

/**
 * Human-visible summary of conversation rows dropped by parse failures, or
 * null when everything parsed. Schema drift used to vanish rows silently
 * (M17/M18: the class of bug that hid the float-vs-string last_activity_at
 * regression); this makes the degradation loud. Pure function so the gating
 * is JVM-testable without Android/Firebase plumbing.
 */
fun conversationParseFailureNotice(failures: Map<String, String>): String? {
	if (failures.isEmpty()) return null
	val ids = failures.keys.sorted().joinToString(", ")
	return "${failures.size} conversation(s) failed to parse and are hidden: $ids (see logcat)"
}
