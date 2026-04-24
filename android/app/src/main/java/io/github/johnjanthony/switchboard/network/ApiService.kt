package io.github.johnjanthony.switchboard.network

import com.google.firebase.database.IgnoreExtraProperties
import com.google.firebase.database.PropertyName

@IgnoreExtraProperties
data class ChannelMessage(
    @get:PropertyName("sender") @set:PropertyName("sender") var sender: String = "",
    @get:PropertyName("message_type") @set:PropertyName("message_type") var message_type: String = "",
    @get:PropertyName("content") @set:PropertyName("content") var content: String = "",
    @get:PropertyName("url") @set:PropertyName("url") var url: String? = null,
    @get:PropertyName("request_id") @set:PropertyName("request_id") var request_id: String? = null,
    @get:PropertyName("response_text") @set:PropertyName("response_text") var response_text: String? = null,
    @get:PropertyName("timestamp") @set:PropertyName("timestamp") var timestamp: Long = 0L,
    @get:PropertyName("format") @set:PropertyName("format") var format: String = "plain",
    @get:PropertyName("suggestions") @set:PropertyName("suggestions") var suggestions: List<String>? = null,
    @get:PropertyName("filename") @set:PropertyName("filename") var filename: String? = null,
)

data class Channel(
    val channelId: String,
    val type: String,                // "single"|"collab"
    val projectKey: String,
    val agentSenders: List<String> = emptyList(),
    val task: String = "",
    val messages: List<Pair<String, ChannelMessage>> = emptyList(),
    val hidden: Boolean = false,
)
