package io.github.johnjanthony.switchboard

import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.lifecycle.ViewModel
import com.google.firebase.database.DataSnapshot
import com.google.firebase.database.DatabaseError
import com.google.firebase.database.FirebaseDatabase
import com.google.firebase.database.ValueEventListener
import com.google.firebase.messaging.FirebaseMessaging
import io.github.johnjanthony.switchboard.network.Question

class MainViewModel : ViewModel() {
    private val _questions = mutableStateOf<List<Question>>(emptyList())
    val questions: State<List<Question>> = _questions

    private val database = FirebaseDatabase.getInstance()
    private val questionsRef = database.getReference("questions")
    private val responsesRef = database.getReference("responses")

    init {
        setupFirebase()
    }

    private fun setupFirebase() {
        // Subscribe to FCM topics
        FirebaseMessaging.getInstance().subscribeToTopic("questions")
        FirebaseMessaging.getInstance().subscribeToTopic("notifications")

        // Realtime Database listener
        questionsRef.addValueEventListener(object : ValueEventListener {
            override fun onDataChange(snapshot: DataSnapshot) {
                val newQuestions = mutableListOf<Question>()
                for (child in snapshot.children) {
                    val q = child.getValue(Question::class.java)
                    if (q != null) {
                        newQuestions.add(q)
                    }
                }
                _questions.value = newQuestions
            }

            override fun onCancelled(error: DatabaseError) {
                // Handle error
            }
        })
    }

    fun answerQuestion(requestId: String, text: String) {
        val response = mapOf(
            "text" to text,
            "timestamp" to System.currentTimeMillis()
        )
        responsesRef.child(requestId).setValue(response)
            .addOnSuccessListener {
                // Success - the card should disappear when the server deletes the question
            }
            .addOnFailureListener {
                it.printStackTrace()
            }
    }
}
