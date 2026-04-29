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
	@get:PropertyName("response_text") @set:PropertyName("response_text") var response_text: String? = null,
	@get:PropertyName("timestamp") @set:PropertyName("timestamp") var timestamp: String? = null,
	@get:PropertyName("format") @set:PropertyName("format") var format: String = "plain",
	@get:PropertyName("suggestions") @set:PropertyName("suggestions") var suggestions: List<String>? = null,
	@get:PropertyName("filename") @set:PropertyName("filename") var filename: String? = null,
	@get:PropertyName("cancelled") @set:PropertyName("cancelled") var cancelled: Boolean = false,
	@get:PropertyName("rejected") @set:PropertyName("rejected") var rejected: Boolean = false,
	@get:PropertyName("title") @set:PropertyName("title") var title: String? = null,
)

data class Pending(
	val sender: String,
	val requestId: String,
	val questionText: String,
	val cancelled: Boolean = false,
	val msgId: String,
	val suggestions: List<String>? = null,
)

data class Channel(
	val cwd: String,
	val cwdKey: String,
	val title: String? = null,
	val cwdCanonical: String = "",
	val hidden: Boolean = false,
	val lastActivityAt: String? = null,
	val preview: String? = null,
	val unreadCount: Int = 0,
	val awayMode: Boolean? = null,
	val pendingResponses: Int = 0,
	val pendingQuestions: Map<String, Pending> = emptyMap(),
	val messages: List<Pair<String, ChannelMessage>> = emptyList(),
)

data class SpawnCollisionData(
	val spawnId: String,
	val cwd: String,
	val cwdKey: String,
	val channelTitle: String?,
	val lastActivityAt: String?,
	val hidden: Boolean,
)

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
