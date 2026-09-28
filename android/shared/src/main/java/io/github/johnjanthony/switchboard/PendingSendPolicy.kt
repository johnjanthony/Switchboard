package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ChannelMessage
import java.time.OffsetDateTime

/** One /message_commands entry as the phone reads it. */
data class QueuedCommand(val conversationId: String, val text: String, val issuedAt: String?)

/** A send this device saw queued; goneAtMs is stamped when it leaves the queue. */
data class SeenSend(val conversationId: String, val text: String, val issuedAt: String?, val goneAtMs: Long? = null)

enum class PendingSendState { SENDING, OFFLINE, NOT_PICKED_UP, EXPIRED, NOT_DELIVERED }

data class PendingSend(val key: String, val text: String, val issuedAt: String?, val state: PendingSendState)

/**
 * Pure derivations for composer messages the server has not written back. A send
 * is a /message_commands entry until the server consumes it; the server deletes it
 * only after writing the /messages row, which carries the entry's key as
 * command_id. Operator's dashboard/derive.js implements the same rules.
 */
object PendingSendPolicy {
	const val SENDING_WINDOW_MS = 20_000L
	const val NOT_DELIVERED_GRACE_MS = 5_000L
	// Mirrors the server's COMMAND_TTL_SECONDS (server/command_freshness.py), which
	// is not published to clients.
	const val COMMAND_TTL_MS = 600_000L

	/** A raw DataSnapshot value; anything that is not a well-formed entry is ignored. */
	fun parseQueued(value: Any?): QueuedCommand? {
		val map = value as? Map<*, *> ?: return null
		val convId = map["conversation_id"] as? String ?: return null
		val text = map["text"] as? String ?: return null
		return QueuedCommand(convId, text, map["issued_at"] as? String)
	}

	fun commandIdsIn(messages: List<Pair<String, ChannelMessage>>): Set<String> =
		messages.mapNotNullTo(mutableSetOf()) { it.second.command_id }

	private fun issuedAtMs(iso: String?): Long? =
		if (iso == null) null else try {
			OffsetDateTime.parse(iso).toInstant().toEpochMilli()
		} catch (_: Exception) {
			null
		}

	private fun queuedState(issuedAt: String?, nowMs: Long, connected: Boolean): PendingSendState {
		val issuedMs = issuedAtMs(issuedAt)
		// An unparseable stamp cannot age out, but it is still queued: show it as
		// not picked up rather than an optimistic "sending" that never escalates.
		val age = if (issuedMs != null) nowMs - issuedMs else SENDING_WINDOW_MS
		return when {
			issuedMs != null && age >= COMMAND_TTL_MS -> PendingSendState.EXPIRED
			!connected -> PendingSendState.OFFLINE
			age >= SENDING_WINDOW_MS -> PendingSendState.NOT_PICKED_UP
			else -> PendingSendState.SENDING
		}
	}

	fun derive(
		convId: String,
		queued: Map<String, QueuedCommand>,
		deliveredIds: Set<String>,
		seen: Map<String, SeenSend>,
		nowMs: Long,
		connected: Boolean,
	): List<PendingSend> {
		val rows = mutableListOf<PendingSend>()
		for ((key, cmd) in queued) {
			if (cmd.conversationId != convId || key in deliveredIds) continue
			rows.add(PendingSend(key, cmd.text, cmd.issuedAt, queuedState(cmd.issuedAt, nowMs, connected)))
		}
		for ((key, entry) in seen) {
			val goneAt = entry.goneAtMs ?: continue
			if (entry.conversationId != convId || key in queued || key in deliveredIds) continue
			if (nowMs - goneAt < NOT_DELIVERED_GRACE_MS) continue
			rows.add(PendingSend(key, entry.text, entry.issuedAt, PendingSendState.NOT_DELIVERED))
		}
		return rows.sortedBy { issuedAtMs(it.issuedAt) ?: Long.MAX_VALUE }
	}

	fun updateSeen(
		seen: Map<String, SeenSend>,
		queued: Map<String, QueuedCommand>,
		nowMs: Long,
		deliveredIds: Set<String>,
	): Map<String, SeenSend> {
		val next = mutableMapOf<String, SeenSend>()
		for ((key, entry) in seen) {
			when {
				key in queued || entry.goneAtMs != null -> next[key] = entry
				key in deliveredIds -> {}
				else -> next[key] = entry.copy(goneAtMs = nowMs)
			}
		}
		for ((key, cmd) in queued) {
			if (key !in next) next[key] = SeenSend(cmd.conversationId, cmd.text, cmd.issuedAt)
		}
		return next
	}

	fun pruneDelivered(seen: Map<String, SeenSend>, deliveredIds: Set<String>): Map<String, SeenSend> =
		seen.filterNot { (key, entry) -> entry.goneAtMs != null && key in deliveredIds }

	/** A gone send inside its grace window has no row yet but still needs the clock. */
	fun needsTick(convId: String, queued: Map<String, QueuedCommand>, seen: Map<String, SeenSend>): Boolean =
		queued.values.any { it.conversationId == convId } ||
			seen.values.any { it.conversationId == convId && it.goneAtMs != null }

	fun restoreDraft(current: String, text: String): String =
		if (current.isBlank()) text else "$current\n$text"

	fun statusText(state: PendingSendState): String = when (state) {
		PendingSendState.SENDING -> "sending..."
		PendingSendState.OFFLINE -> "Offline: sends when you reconnect."
		PendingSendState.NOT_PICKED_UP -> "Server hasn't taken this. It delivers if the server returns within 10 min."
		PendingSendState.EXPIRED -> "Expired: the server will drop this, not deliver it."
		PendingSendState.NOT_DELIVERED -> "Not delivered"
	}
}
