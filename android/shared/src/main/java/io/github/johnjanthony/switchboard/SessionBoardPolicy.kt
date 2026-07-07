package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ConversationMember
import io.github.johnjanthony.switchboard.network.RegistrySession
import java.time.OffsetDateTime
import java.time.format.DateTimeParseException

// --- Session registry board (convening chunk 4): pure derivations backing the phone Sessions
// board. Mirrors the state buckets Operator's derive.js uses (isConvenable, needsAttention).

private val LIVE_STATES = setOf("active", "idle", "awaiting_human", "awaiting_agent")
private val TERMINAL_STATES = setOf("ended", "lost")

/** Display label for a session row: name -> sender -> cwd tail -> a placeholder. */
fun sessionBoardLabel(rec: RegistrySession): String {
	if (!rec.name.isNullOrBlank()) return rec.name!!
	if (!rec.sender.isNullOrBlank()) return rec.sender!!
	val tail = cwdTail(rec.cwd)
	return tail.ifBlank { "(unknown)" }
}

/** Last path segment of a cwd, splitting on both `/` and `\` since sessions span Windows and WSL. */
fun cwdTail(cwd: String): String =
	cwd.split('/', '\\').lastOrNull { it.isNotBlank() } ?: ""

/**
 * Parse an ISO-8601 offset date-time to epoch millis, accepting both the server's `+00:00`
 * stamps and the app's own `nowIso()` `Z` stamps. Null on blank input or parse failure - callers
 * treat that as "can't compare", not as a crash.
 */
fun parseIsoMs(iso: String?): Long? {
	if (iso.isNullOrBlank()) return null
	return try {
		OffsetDateTime.parse(iso).toInstant().toEpochMilli()
	} catch (e: DateTimeParseException) {
		null
	}
}

/**
 * True when a session is idle and its last event is unacknowledged: either there is no ack yet,
 * or the last event happened strictly after the most recent ack. Both timestamps are compared as
 * parsed instants (never as raw strings) since lastEventAt and ackIso come from different
 * stampers using different (though equivalent) offset notations.
 */
fun sessionNeedsAttention(rec: RegistrySession, ackIso: String?): Boolean {
	if (rec.state != "idle") return false
	val eventMs = parseIsoMs(rec.lastEventAt) ?: return false
	if (ackIso == null) return true
	val ackMs = parseIsoMs(ackIso) ?: return false
	return eventMs > ackMs
}

/** Hint for when a session will next resume, shown on the board row. */
fun sessionWakeLabel(rec: RegistrySession): String = when (rec.state) {
	"awaiting_agent" -> "wakes instantly"
	"awaiting_human" -> "wakes on next phone answer"
	"active" -> "wakes at end of current turn"
	"idle" -> "wakes on your next prompt"
	"ended", "lost" -> "Resume into conversation"
	else -> ""
}

/** True for a terminal session with a known cwd - the only case a session can be resumed into. */
fun isSessionResumable(rec: RegistrySession): Boolean =
	rec.state in TERMINAL_STATES && rec.cwd.isNotBlank()

/** True for a live session, or a terminal one that's resumable. Mirrors Operator's isConvenable. */
fun isSessionSelectable(rec: RegistrySession): Boolean =
	rec.state in LIVE_STATES || isSessionResumable(rec)

/**
 * Split the registry into (live, ended) for the board's two sections. Live sessions are sorted
 * needs-attention first, then by last event time descending; ended sessions are sorted by last
 * event time descending. Unparseable/missing timestamps sort last in both lists.
 */
fun partitionSessionBoard(
	sessions: Map<String, RegistrySession>,
	acks: Map<String, String>,
): Pair<List<RegistrySession>, List<RegistrySession>> {
	val live = sessions.entries.filter { it.value.state !in TERMINAL_STATES }
	val ended = sessions.entries.filter { it.value.state in TERMINAL_STATES }

	val liveSorted = live
		.sortedWith(
			compareByDescending<Map.Entry<String, RegistrySession>> { sessionNeedsAttention(it.value, acks[it.key]) }
				.thenByDescending { parseIsoMs(it.value.lastEventAt) ?: Long.MIN_VALUE }
		)
		.map { it.value }

	val endedSorted = ended
		.sortedByDescending { parseIsoMs(it.value.lastEventAt) ?: Long.MIN_VALUE }
		.map { it.value }

	return liveSorted to endedSorted
}

/** Count of sessions needing attention, for the board's tab badge. */
fun sessionBadgeCount(sessions: Map<String, RegistrySession>, acks: Map<String, String>): Int =
	sessions.count { (id, rec) -> sessionNeedsAttention(rec, acks[id]) }

/**
 * True when a conversation can be resumed: at least one member's session (looked up by
 * cliSessionId, the registry's map key) has a terminal registry record. Registry-backed
 * replacement for member archaeology (Task 11 wires this in).
 */
fun conversationResumable(members: List<ConversationMember>, sessions: Map<String, RegistrySession>): Boolean =
	members.any { member ->
		val rec = sessions[member.cliSessionId]
		rec != null && rec.state in TERMINAL_STATES
	}
