package io.github.johnjanthony.switchboard.network

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

data class Question(
    var request_id: String = "",
    var agent_id: String = "",
    var question: String = "",
    var format: String = "plain",
    var suggestions: List<String>? = null,
    var status: String = "pending",
    var created_at: Long = 0
)

data class ReplyRequest(
    val request_id: String,
    val text: String
)

data class ReplyResponse(
    val status: String
)

data class CollabMessage(
    var speaker: String = "",
    var type: String = "",       // "collab" | "ask_human" | "inject"
    var content: String = "",
    var request_id: String? = null,
    var timestamp: Long = 0L,
)

data class CollabSessionMeta(
    var agent_ids: List<String> = emptyList(),
    var task: String = "",
    var created_at: Long = 0L,
)

data class CollabSession(
    val sessionId: String,
    val meta: CollabSessionMeta,
    val messages: MutableList<Pair<String, CollabMessage>> = mutableListOf(),
)

interface ApiService {
    @GET("android/questions")
    suspend fun getQuestions(): List<Question>

    @POST("android/reply")
    suspend fun sendReply(@Body reply: ReplyRequest): ReplyResponse
}
