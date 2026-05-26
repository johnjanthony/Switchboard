package io.github.johnjanthony.switchboard.network

import com.google.firebase.database.IgnoreExtraProperties
import com.google.firebase.database.PropertyName

@IgnoreExtraProperties
data class ChannelMessage(
	@get:PropertyName("sender") @set:PropertyName("sender") var sender: String = "",
	@get:PropertyName("type") @set:PropertyName("type") var type: String = "",
	@get:PropertyName("text") @set:PropertyName("text") var text: String = "",
	@get:PropertyName("url") @set:PropertyName("url") var url: String? = null,
	@get:PropertyName("request_id") @set:PropertyName("request_id") var request_id: String? = null,
	@get:PropertyName("timestamp") @set:PropertyName("timestamp") var timestamp: String? = null,
	@get:PropertyName("format") @set:PropertyName("format") var format: String = "plain",
	@get:PropertyName("suggestions") @set:PropertyName("suggestions") var suggestions: List<String>? = null,
	@get:PropertyName("filename") @set:PropertyName("filename") var filename: String? = null,
	@get:PropertyName("cancelled") @set:PropertyName("cancelled") var cancelled: Boolean = false,
	@get:PropertyName("rejected") @set:PropertyName("rejected") var rejected: Boolean = false,
	@get:PropertyName("title") @set:PropertyName("title") var title: String? = null,
	@get:PropertyName("attached_to_msg_id") @set:PropertyName("attached_to_msg_id") var attached_to_msg_id: String? = null,
	@get:PropertyName("opened") @set:PropertyName("opened") var opened: Boolean = false,
)

data class Pending(
	val sender: String,
	val requestId: String,
	val questionText: String,
	val cancelled: Boolean = false,
	val msgId: String,
	val suggestions: List<String>? = null,
)

const val AGENT_STATUS_RECENCY_MS = 30L * 60L * 1000L  // 30 minutes

data class AgentStatus(
	val sender: String,
	val state: String,        // "thinking" | "waiting" | "tool:<name>"
	val detail: String?,
	val updatedAt: Long       // epoch ms
) {
	fun isFresh(now: Long = System.currentTimeMillis()): Boolean =
		(now - updatedAt) < AGENT_STATUS_RECENCY_MS
}

data class Channel(
	val cwd: String,
	val cwdKey: String,
	val title: String? = null,
	val cwdCanonical: String = "",
	val hidden: Boolean = false,
	val lastActivityAt: String? = null,
	val preview: String? = null,
	val unreadCount: Int = 0,
	val pendingResponses: Int = 0,
	val pendingQuestions: Map<String, Pending> = emptyMap(),
	val messages: List<Pair<String, ChannelMessage>> = emptyList(),
	val answeredQuestionMsgIds: Set<String> = emptySet(),
	val agentStatus: AgentStatus? = null,
) {
	val displayCount: Int get() = kotlin.math.max(unreadCount, pendingResponses)
}

data class BulkRespondSection(
	val cwd: String,
	val entries: List<BulkRespondEntry>,
)

data class BulkRespondEntry(
	val requestId: String,
	val sender: String,
	val questionText: String,
)

data class BulkRespondPayload(
	val sections: List<BulkRespondSection>,
	val defaultText: String,
)

data class PendingExitToggle(
	val scopeCwdKey: String?,    // null = global; otherwise per-channel cwdKey
	val payload: BulkRespondPayload,
)

// --- Conversation model (T-027 spawn-conversation-aware redesign) ---
// TODO: wire Firebase sync to /conversations/<id>/... once server migration ships;
// currently the existing /channels/<cwd>/... sync feeds Channel objects above.
// ConversationSummary is the new data model; Channel is deprecated.

data class ConversationMember(
	val cliSessionId: String = "",
	val sender: String = "",
	val cwd: String = "",
	val surface: String = "",  // "windows" | "wsl"
	val alive: Boolean = true,
	val sessionLostPermanently: Boolean = false,
	val sessionEndedAt: String? = null,  // ISO-8601
	val sessionEndReason: String? = null,
	val joinedAt: Double = 0.0,
	val leftAt: Double? = null,
	val lastSeenSeq: Int = 0,
)

data class ConversationSummary(
	val id: String,
	val title: String,
	val state: String,  // "active" | "ended"
	val members: List<ConversationMember>,
	val lastActivityAt: String,
	val isOpenConversation: Boolean = false,
	val hidden: Boolean = false,
	val unreadCount: Int = 0,
	val agentStatuses: Map<String, AgentStatus> = emptyMap(),  // keyed by sender
) {
	/** True if at least one member can be resumed (dormant, not permanently lost, has a session ID). */
	val isResumable: Boolean
		get() = members.any { !it.alive && !it.sessionLostPermanently && it.cliSessionId.isNotEmpty() }

	/**
	 * True if any member's session ended 25-29 days ago — warning that Claude Code's
	 * 30-day cleanupPeriodDays window is approaching and resume may fail soon.
	 */
	val staleSessionWarning: Boolean
		get() {
			val now = System.currentTimeMillis()
			return members.any { m ->
				m.sessionEndedAt?.let { iso ->
					val ms = java.time.Instant.parse(iso).toEpochMilli()
					val days = (now - ms) / (1000L * 60 * 60 * 24)
					days in 25L..29L
				} ?: false
			}
		}

	/** Comma-separated list of member sender names for display. */
	val memberRoster: String
		get() = members.joinToString(", ") { it.sender }
}
