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

data class BulkRespondSection(
	val label: String,
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
	val payload: BulkRespondPayload,
)

// --- Conversation model (post T-027 / 2026-05-19 conversations redesign) ---
// ConversationSummary / ConversationRow are the primary data model on the phone and Wear.
// Real conversations are read from /conversations/<id>/...

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
	val pendingResponses: Int = 0,
	val preview: String? = null,
	val continuedFrom: String? = null,  // conv_id this conversation was resumed from, if any
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
					// The server writes isoformat() with a +00:00 offset, which
					// Instant.parse (ISO_INSTANT, wants Z) rejects; an unguarded
					// parse crashed list rendering for any dormant member (M14).
					// OffsetDateTime accepts both +00:00 and Z; anything else
					// degrades to "no warning", like formatRelativeTime.
					val ms = try {
						java.time.OffsetDateTime.parse(iso).toInstant().toEpochMilli()
					} catch (_: Exception) {
						return@let false
					}
					val days = (now - ms) / (1000L * 60 * 60 * 24)
					days in 25L..29L
				} ?: false
			}
		}

	/** Comma-separated list of member sender names for display. */
	val memberRoster: String
		get() = members.joinToString(", ") { it.sender }
}

/**
 * View-model composite for both phone and Wear. Wraps a [ConversationSummary]
 * (Firebase-mirrored conversation state) with per-conversation runtime state:
 * messages, pending questions, and the answered-question set.
 */
data class ConversationRow(
	val summary: ConversationSummary,
	val messages: List<Pair<String, ChannelMessage>> = emptyList(),
	val pendingQuestions: Map<String, Pending> = emptyMap(),
	val answeredQuestionMsgIds: Set<String> = emptySet(),
) {
	val id: String get() = summary.id
	val title: String get() = summary.title
	val displayCount: Int get() = kotlin.math.max(summary.unreadCount, summary.pendingResponses)
	val isOpenConversation: Boolean get() = summary.isOpenConversation
	val hidden: Boolean get() = summary.hidden
	val preview: String? get() = summary.preview
	val continuedFrom: String? get() = summary.continuedFrom
	val lastActivityAt: String get() = summary.lastActivityAt
	/** Freshest live agent status across all members, or null if none qualify. */
	val agentStatus: AgentStatus?
		get() = summary.agentStatuses.values
			.filter { it.isFresh() }
			.maxByOrNull { it.updatedAt }
	val isResumable: Boolean get() = summary.isResumable
	val staleSessionWarning: Boolean get() = summary.staleSessionWarning
	val memberRoster: String get() = summary.memberRoster
	val state: String get() = summary.state
	val members: List<ConversationMember> get() = summary.members
}

// --- Watchtower widget hub (T-180): rings / quota / status read from widget/* ---
// Mirrors ChannelMessage's @PropertyName style so Firebase getValue(Class) maps the
// server's snake_case nodes. Every field is defaulted so the no-arg constructor
// getValue requires exists. @IgnoreExtraProperties drops fields we do not render
// yet (e.g. status.incidents) instead of throwing.

@IgnoreExtraProperties
data class WidgetRing(
	@get:PropertyName("pct") @set:PropertyName("pct") var pct: Double = 0.0,
	@get:PropertyName("model") @set:PropertyName("model") var model: String = "",
	@get:PropertyName("status") @set:PropertyName("status") var status: String = "",
	@get:PropertyName("context_tokens") @set:PropertyName("context_tokens") var contextTokens: Long = 0L,
	@get:PropertyName("window") @set:PropertyName("window") var window: Long = 0L,
	@get:PropertyName("is_error") @set:PropertyName("is_error") var isError: Boolean = false,
)

@IgnoreExtraProperties
data class WidgetQuotaWindow(
	@get:PropertyName("pct") @set:PropertyName("pct") var pct: Double = 0.0,
	@get:PropertyName("resets_at") @set:PropertyName("resets_at") var resetsAt: String = "",
)

@IgnoreExtraProperties
data class WidgetQuota(
	@get:PropertyName("session") @set:PropertyName("session") var session: WidgetQuotaWindow? = null,
	@get:PropertyName("weekly") @set:PropertyName("weekly") var weekly: WidgetQuotaWindow? = null,
	@get:PropertyName("polled_at") @set:PropertyName("polled_at") var polledAt: String = "",
)

@IgnoreExtraProperties
data class WidgetStatus(
	@get:PropertyName("level") @set:PropertyName("level") var level: String = "unknown",
	@get:PropertyName("description") @set:PropertyName("description") var description: String = "",
	@get:PropertyName("watch_state") @set:PropertyName("watch_state") var watchState: String = "idle",
	@get:PropertyName("dot_visible") @set:PropertyName("dot_visible") var dotVisible: Boolean = false,
	@get:PropertyName("has_data") @set:PropertyName("has_data") var hasData: Boolean = false,
	@get:PropertyName("button") @set:PropertyName("button") var button: String = "check",
	@get:PropertyName("fetched_at") @set:PropertyName("fetched_at") var fetchedAt: String = "",
)
