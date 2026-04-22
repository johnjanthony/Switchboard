package io.github.johnjanthony.switchboard

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.FileProvider
import androidx.lifecycle.ViewModel
import com.google.firebase.database.ChildEventListener
import com.google.firebase.database.DataSnapshot
import com.google.firebase.database.DatabaseError
import com.google.firebase.database.FirebaseDatabase
import com.google.firebase.database.ValueEventListener
import com.google.firebase.messaging.FirebaseMessaging
import io.github.johnjanthony.switchboard.network.CollabMessage
import io.github.johnjanthony.switchboard.network.CollabSession
import io.github.johnjanthony.switchboard.network.CollabSessionMeta
import io.github.johnjanthony.switchboard.network.Question
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

data class Message(
    val id: String,
    val text: String,
    val sender: String,
    val timestamp: Long,
    val isQuestion: Boolean = false,
    val suggestions: List<String>? = null,
    val documentUrl: String? = null,
    val fileName: String? = null,
    val format: String = "plain"
)

class MainViewModel : ViewModel() {
    private val _questions = mutableStateOf<List<Question>>(emptyList())
    val questions: State<List<Question>> = _questions

    // Group history by agent_id
    private val _history = mutableStateOf<Map<String, List<Message>>>(emptyMap())
    val history: State<Map<String, List<Message>>> = _history

    private val _waitingAgents = mutableStateOf<Set<String>>(emptySet())
    val waitingAgents: State<Set<String>> = _waitingAgents

    private val _selectedAgentId = mutableStateOf<String?>(null)
    val selectedAgentId: State<String?> = _selectedAgentId

    // Collab session state
    private val _collabSessions = MutableStateFlow<Map<String, CollabSession>>(emptyMap())
    val collabSessions: StateFlow<Map<String, CollabSession>> = _collabSessions.asStateFlow()

    // Pending ask_human queue per session: sessionId -> list of (messageId, CollabMessage)
    private val _pendingSessionQuestions = MutableStateFlow<Map<String, List<Pair<String, CollabMessage>>>>(emptyMap())
    val pendingSessionQuestions: StateFlow<Map<String, List<Pair<String, CollabMessage>>>> = _pendingSessionQuestions.asStateFlow()

    private var isUserTyping: Boolean = false
    private val processedRequestIds = mutableSetOf<String>()

    private val database = FirebaseDatabase.getInstance()
    private val questionsRef = database.getReference("questions")
    private val responsesRef = database.getReference("responses")
    private val commandsRef = database.getReference("commands")
    private val documentsRef = database.getReference("documents")
    private val notificationsRef = database.getReference("notifications")
    private val sessionsRef = database.getReference("sessions")

    init {
        setupFirebase()
        setupSessionsListener()
    }

    private fun setupFirebase() {
        // Subscribe to FCM topics
        FirebaseMessaging.getInstance().subscribeToTopic("questions")
        FirebaseMessaging.getInstance().subscribeToTopic("notifications")

        // Realtime Database listener
        val dbListener = object : ValueEventListener {
            override fun onDataChange(snapshot: DataSnapshot) {
                handleDatabaseUpdate()
            }

            override fun onCancelled(error: DatabaseError) {}
        }

        questionsRef.addValueEventListener(dbListener)
        responsesRef.addValueEventListener(dbListener)
        notificationsRef.addValueEventListener(dbListener)
        documentsRef.addValueEventListener(dbListener)
        sessionsRef.addValueEventListener(dbListener)
    }

    private fun handleDatabaseUpdate() {
        // Fetch all relevant nodes and merge them
        sessionsRef.get().addOnSuccessListener { sessionSnapshot ->
            questionsRef.get().addOnSuccessListener { questionSnapshot ->
                responsesRef.get().addOnSuccessListener { responseSnapshot ->
                    notificationsRef.get().addOnSuccessListener { notificationSnapshot ->
                        documentsRef.get().addOnSuccessListener { documentSnapshot ->
                            processUpdates(
                                sessionSnapshot,
                                questionSnapshot,
                                responseSnapshot,
                                notificationSnapshot,
                                documentSnapshot
                            )
                        }
                    }
                }
            }
        }
    }

    private fun processUpdates(
        sessionSnapshot: DataSnapshot,
        questionSnapshot: DataSnapshot,
        responseSnapshot: DataSnapshot,
        notificationSnapshot: DataSnapshot,
        documentSnapshot: DataSnapshot
    ) {
        val openSessions = mutableMapOf<String, Long>() // agent_id -> last_activity
        for (child in sessionSnapshot.children) {
            val agentId = child.key ?: continue
            val state = child.child("state").getValue(String::class.java) ?: "closed"
            if (state == "open") {
                openSessions[agentId] = child.child("last_activity").getValue(Long::class.java) ?: 0L
            }
        }

        val newQuestions = mutableListOf<Question>()
        val updatedHistory = mutableMapOf<String, List<Message>>()
        val waitingAgentsSet = mutableSetOf<String>()
        var autoSelectTarget: String? = null

        // Group messages by agent for open sessions
        val historyMap = mutableMapOf<String, MutableList<Message>>()
        
        // Track request_id -> agent_id to correctly place responses
        val requestToAgent = mutableMapOf<String, String>()

        // Process Questions
        for (child in questionSnapshot.children) {
            val q = child.getValue(Question::class.java) ?: continue
            requestToAgent[q.request_id] = q.agent_id
            
            // Track active questions for the UI (red dots, input fields)
            if (q.status == "pending") {
                newQuestions.add(q)
                waitingAgentsSet.add(q.agent_id)
            }

            // Only add to history if session is open
            if (openSessions.containsKey(q.agent_id)) {
                val isNew = !processedRequestIds.contains(q.request_id)
                if (isNew && q.status == "pending") {
                    processedRequestIds.add(q.request_id)
                    autoSelectTarget = q.agent_id
                }

                val agentMessages = historyMap.getOrPut(q.agent_id) { mutableListOf() }
                agentMessages.add(
                    Message(
                        id = q.request_id,
                        text = q.question,
                        sender = q.agent_id,
                        timestamp = q.created_at,
                        isQuestion = true,
                        suggestions = q.suggestions,
                        format = q.format
                    )
                )
            }
        }

        // Process Responses (Replies to questions)
        for (child in responseSnapshot.children) {
            val requestId = child.key ?: continue
            val text = child.child("text").getValue(String::class.java) ?: ""
            val timestamp = child.child("timestamp").getValue(Long::class.java) ?: 0L
            val agentId = requestToAgent[requestId] ?: child.child("agent_id").getValue(String::class.java) ?: continue

            if (openSessions.containsKey(agentId)) {
                val agentMessages = historyMap.getOrPut(agentId) { mutableListOf() }
                // Avoid duplicates if we just added it locally
                if (agentMessages.none { it.id == "resp_$requestId" }) {
                    agentMessages.add(
                        Message(
                            id = "resp_$requestId",
                            text = text,
                            sender = "Me",
                            timestamp = timestamp,
                            isQuestion = false
                        )
                    )
                }
            }
        }

        // Process Notifications (System or agent updates)
        for (child in notificationSnapshot.children) {
            val id = child.key ?: continue
            val agentId = child.child("agent_id").getValue(String::class.java) ?: ""
            val message = child.child("message").getValue(String::class.java) ?: ""
            val format = child.child("format").getValue(String::class.java) ?: "plain"
            val timestamp = child.child("timestamp").getValue(Long::class.java) ?: 0L
            val status = child.child("status").getValue(String::class.java) ?: "read"

            if (openSessions.containsKey(agentId)) {
                val isNew = !processedRequestIds.contains(id)
                if (isNew && status == "unread") {
                    processedRequestIds.add(id)
                    // We don't auto-select for notifications usually, but we could
                }

                val agentMessages = historyMap.getOrPut(agentId) { mutableListOf() }
                agentMessages.add(
                    Message(
                        id = id,
                        text = message,
                        sender = agentId,
                        timestamp = timestamp,
                        isQuestion = false,
                        format = format
                    )
                )
                
                if (status == "unread") {
                    waitingAgentsSet.add(agentId)
                }
            }
        }

        // Process Documents
        for (child in documentSnapshot.children) {
            val id = child.key ?: continue
            val agentId = child.child("agent_id").getValue(String::class.java) ?: ""
            val filename = child.child("filename").getValue(String::class.java) ?: ""
            val url = child.child("url").getValue(String::class.java) ?: ""
            val caption = child.child("caption").getValue(String::class.java) ?: ""
            val timestamp = child.child("timestamp").getValue(Long::class.java) ?: 0L
            val status = child.child("status").getValue(String::class.java) ?: "unread"

            if (openSessions.containsKey(agentId)) {
                val isNew = !processedRequestIds.contains(id)
                if (isNew && status == "unread") {
                    processedRequestIds.add(id)
                    autoSelectTarget = agentId
                }

                if (status == "unread") {
                    waitingAgentsSet.add(agentId)
                }

                val agentMessages = historyMap.getOrPut(agentId) { mutableListOf() }
                agentMessages.add(
                    Message(
                        id = id,
                        text = caption.ifBlank { "Sent a document: $filename" },
                        sender = agentId,
                        timestamp = timestamp,
                        isQuestion = false,
                        suggestions = null,
                        documentUrl = url,
                        fileName = filename
                    )
                )
            }
        }

        // Sort and update state
        for ((agentId, messages) in historyMap) {
            updatedHistory[agentId] = messages.sortedBy { it.timestamp }
        }

        // Ensure sessions with activity but NO questions/docs are still shown
        for (agentId in openSessions.keys) {
            if (!updatedHistory.containsKey(agentId)) {
                updatedHistory[agentId] = emptyList()
            }
        }

        _questions.value = newQuestions
        _history.value = updatedHistory
        _waitingAgents.value = waitingAgentsSet

        if (autoSelectTarget != null && !isUserTyping) {
            _selectedAgentId.value = autoSelectTarget
        } else if (_selectedAgentId.value == null && updatedHistory.isNotEmpty()) {
            _selectedAgentId.value = updatedHistory.keys.first()
        }
    }

    private fun setupSessionsListener() {
        sessionsRef.addChildEventListener(object : ChildEventListener {
            override fun onChildAdded(snapshot: DataSnapshot, previousChildName: String?) {
                // Only handle collab sessions (have "meta" child); single-agent sessions have "state"
                if (!snapshot.hasChild("meta")) return
                val sessionId = snapshot.key ?: return
                val meta = snapshot.child("meta").getValue(CollabSessionMeta::class.java)
                    ?: CollabSessionMeta()
                val session = CollabSession(sessionId = sessionId, meta = meta)

                snapshot.child("messages").children.forEach { msgSnap ->
                    val msgId = msgSnap.key ?: return@forEach
                    val msg = msgSnap.getValue(CollabMessage::class.java) ?: return@forEach
                    session.messages.add(msgId to msg)
                    if (msg.type == "ask_human") enqueueSessionQuestion(sessionId, msgId, msg)
                }
                snapshot.child("messages").ref.addChildEventListener(
                    object : ChildEventListener {
                        override fun onChildAdded(snap: DataSnapshot, prev: String?) {
                            val msgId = snap.key ?: return
                            val msg = snap.getValue(CollabMessage::class.java) ?: return
                            addSessionMessage(sessionId, msgId, msg)
                        }
                        override fun onChildChanged(snap: DataSnapshot, prev: String?) {}
                        override fun onChildRemoved(snap: DataSnapshot) {}
                        override fun onChildMoved(snap: DataSnapshot, prev: String?) {}
                        override fun onCancelled(error: DatabaseError) {}
                    }
                )
                _collabSessions.value = _collabSessions.value + (sessionId to session)
            }
            override fun onChildChanged(snapshot: DataSnapshot, previousChildName: String?) {}
            override fun onChildRemoved(snapshot: DataSnapshot) {}
            override fun onChildMoved(snapshot: DataSnapshot, previousChildName: String?) {}
            override fun onCancelled(error: DatabaseError) {}
        })
    }

    private fun addSessionMessage(sessionId: String, msgId: String, msg: CollabMessage) {
        val current = _collabSessions.value.toMutableMap()
        val session = current[sessionId] ?: return
        if (session.messages.any { it.first == msgId }) return  // already seeded from initial snapshot
        session.messages.add(msgId to msg)
        _collabSessions.value = current
        if (msg.type == "ask_human") enqueueSessionQuestion(sessionId, msgId, msg)
    }

    private fun enqueueSessionQuestion(sessionId: String, msgId: String, msg: CollabMessage) {
        val current = _pendingSessionQuestions.value.toMutableMap()
        val list = (current[sessionId] ?: emptyList()) + (msgId to msg)
        _pendingSessionQuestions.value = current + (sessionId to list)
    }

    fun resolveSessionQuestion(sessionId: String, msgId: String) {
        val current = _pendingSessionQuestions.value.toMutableMap()
        val list = (current[sessionId] ?: emptyList()).filter { it.first != msgId }
        _pendingSessionQuestions.value = current + (sessionId to list)
    }

    fun sendInjectMessage(sessionId: String, text: String) {
        val injectRef = FirebaseDatabase.getInstance()
            .getReference("sessions/$sessionId/inject_queue")
        val entry = mapOf("content" to text, "timestamp" to System.currentTimeMillis())
        injectRef.push().setValue(entry)
    }

    fun replyToSessionQuestion(sessionId: String, msgId: String, requestId: String, text: String) {
        val respRef = FirebaseDatabase.getInstance().getReference("responses/$requestId")
        respRef.setValue(mapOf("text" to text, "timestamp" to System.currentTimeMillis()))
        resolveSessionQuestion(sessionId, msgId)
    }

    fun setUserTyping(typing: Boolean) {
        isUserTyping = typing
    }

    fun selectAgent(agentId: String) {
        _selectedAgentId.value = agentId
        
        // Mark notifications as read for this agent
        notificationsRef.get().addOnSuccessListener { snapshot ->
            for (child in snapshot.children) {
                if (child.child("agent_id").getValue(String::class.java) == agentId &&
                    child.child("status").getValue(String::class.java) == "unread"
                ) {
                    child.ref.child("status").setValue("read")
                }
            }
        }
        
        // Mark documents as read for this agent
        documentsRef.get().addOnSuccessListener { snapshot ->
            for (child in snapshot.children) {
                if (child.child("agent_id").getValue(String::class.java) == agentId &&
                    child.child("status").getValue(String::class.java) == "unread"
                ) {
                    child.ref.child("status").setValue("read")
                }
            }
        }
    }

    fun closeSession(agentId: String) {
        // Mark session as closed in Firebase
        sessionsRef.child(agentId).child("state").setValue("closed")

        // Mark all questions for this agent as answered
        val agentQuestions = _questions.value.filter { it.agent_id == agentId }
        agentQuestions.forEach { q ->
            answerQuestion(q.request_id, agentId, "I'm back at my desk now, let's proceed in the terminal")
        }

        // Mark all documents as read (optional logic, but good for cleanup)
        documentsRef.get().addOnSuccessListener { snapshot ->
            for (child in snapshot.children) {
                if (child.child("agent_id").getValue(String::class.java) == agentId) {
                    child.ref.child("status").setValue("read")
                }
            }
        }
    }

    fun answerQuestion(requestId: String, agentId: String, text: String) {
        val timestamp = System.currentTimeMillis()
        val response = mapOf(
            "text" to text,
            "agent_id" to agentId,
            "timestamp" to timestamp
        )
        
        // Update local history immediately for snappy UI
        val currentHistory = _history.value.toMutableMap()
        val agentMessages = currentHistory[agentId]?.toMutableList() ?: mutableListOf()
        if (agentMessages.none { it.id == "resp_$requestId" }) {
            agentMessages.add(
                Message(
                    id = "resp_$requestId",
                    text = text,
                    sender = "Me",
                    timestamp = timestamp,
                    isQuestion = false
                )
            )
            currentHistory[agentId] = agentMessages.sortedBy { it.timestamp }
            _history.value = currentHistory
        }

        // Update question status in Firebase
        questionsRef.child(requestId).child("status").setValue("answered")

        responsesRef.child(requestId).setValue(response)
            .addOnSuccessListener {
                // Success
            }
            .addOnFailureListener {
                it.printStackTrace()
            }
    }

    fun spawnSession(project: String, prompt: String, agents: Int = 1, relay: Boolean = false) {
        val flags = buildString {
            if (agents > 1) append(" --agents=$agents")
            if (relay) append(" --relay")
        }
        val command = if (project.isBlank()) {
            "/spawn$flags $prompt"
        } else {
            "/spawn $project$flags $prompt"
        }
        commandsRef.push().setValue(command)
    }

    fun downloadAndOpenFile(context: Context, url: String, fileName: String) {
        val client = OkHttpClient()
        val request = Request.Builder().url(url).build()

        client.newCall(request).enqueue(object : okhttp3.Callback {
            override fun onFailure(call: okhttp3.Call, e: IOException) {
                e.printStackTrace()
            }

            override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                if (!response.isSuccessful) return

                val file = File(context.cacheDir, fileName)
                try {
                    FileOutputStream(file).use { output ->
                        response.body?.byteStream()?.copyTo(output)
                    }
                    // Run Intent logic on main thread
                    Handler(Looper.getMainLooper()).post {
                        openFile(context, file)
                    }
                } catch (e: IOException) {
                    e.printStackTrace()
                }
            }
        })
    }

    private fun openFile(context: Context, file: File) {
        val uri: Uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            file
        )

        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, context.contentResolver.getType(uri))
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }

        context.startActivity(Intent.createChooser(intent, "Open with"))
    }
}
