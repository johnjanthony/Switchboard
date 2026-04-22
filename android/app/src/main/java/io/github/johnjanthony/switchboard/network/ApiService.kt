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

interface ApiService {
    @GET("android/questions")
    suspend fun getQuestions(): List<Question>

    @POST("android/reply")
    suspend fun sendReply(@Body reply: ReplyRequest): ReplyResponse
}
