package io.github.johnjanthony.switchboard.network

data class ChannelMessage(
    var sender: String = "",
    var message_type: String = "",   // "question"|"notify"|"agent"|"document"
    var content: String = "",
    var url: String? = null,         // present only for "document" type
    var request_id: String? = null,  // present only for "question" type
    var timestamp: Long = 0L,
    var format: String = "plain",
    var suggestions: List<String>? = null,
)

data class Channel(
    val channelId: String,
    val type: String,                // "single"|"collab"
    val projectKey: String,
    val agentSenders: List<String> = emptyList(),
    val task: String = "",
    val messages: MutableList<Pair<String, ChannelMessage>> = mutableListOf(),
)
