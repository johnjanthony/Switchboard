package io.github.johnjanthony.switchboard.network

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

data class Question(
    val request_id: String = "",
    val agent_id: String = "",
    val question: String = "",
    val format: String = "plain",
    val suggestions: List<String>? = null,
    val status: String = "pending",
    val created_at: Long = 0
)

data class ReplyRequest(
    val request_id: String,
    val text: String
)

data class ReplyResponse(
    val status: String
)

interface ApiService {
    @GET("android/questions")
    suspend fun getQuestions(): List<Question>

    @POST("android/reply")
    suspend fun sendReply(@Body reply: ReplyRequest): ReplyResponse
}
