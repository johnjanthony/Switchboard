package io.github.johnjanthony.switchboard

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.lifecycle.ViewModel
import androidx.core.content.FileProvider
import com.google.firebase.database.ChildEventListener
import com.google.firebase.database.DataSnapshot
import com.google.firebase.database.DatabaseError
import com.google.firebase.database.FirebaseDatabase
import io.github.johnjanthony.switchboard.network.Channel
import io.github.johnjanthony.switchboard.network.ChannelMessage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

class MainViewModel : ViewModel() {

    private val _channels = MutableStateFlow<Map<String, Channel>>(emptyMap())
    val channels: StateFlow<Map<String, Channel>> = _channels.asStateFlow()

    // Per-channel: the current pending question (msgId, message), or null
    private val _pendingQuestions = MutableStateFlow<Map<String, Pair<String, ChannelMessage>>>(emptyMap())
    val pendingQuestions: StateFlow<Map<String, Pair<String, ChannelMessage>>> = _pendingQuestions.asStateFlow()

    private val _selectedChannelId = MutableStateFlow<String?>(null)
    val selectedChannelId: StateFlow<String?> = _selectedChannelId.asStateFlow()

    private val database = FirebaseDatabase.getInstance()
    private val sessionsRef = database.getReference("sessions")
    private val responsesRef = database.getReference("responses")
    private val commandsRef = database.getReference("commands")

    init {
        setupChannelsListener()
    }

    private fun setupChannelsListener() {
        sessionsRef.addChildEventListener(object : ChildEventListener {
            override fun onChildAdded(snapshot: DataSnapshot, previousChildName: String?) {
                val channelId = snapshot.key ?: return
                val metaSnap = snapshot.child("meta")
                val type = metaSnap.child("type").getValue(String::class.java) ?: "single"
                val projectKey = metaSnap.child("project_key").getValue(String::class.java) ?: channelId
                val agentSenders = metaSnap.child("agent_senders").children
                    .mapNotNull { it.getValue(String::class.java) }
                val task = metaSnap.child("task").getValue(String::class.java) ?: ""

                val channel = Channel(
                    channelId = channelId,
                    type = type,
                    projectKey = projectKey,
                    agentSenders = agentSenders,
                    task = task,
                )

                snapshot.child("messages").children.forEach { msgSnap ->
                    val msgId = msgSnap.key ?: return@forEach
                    val msg = msgSnap.getValue(ChannelMessage::class.java) ?: return@forEach
                    channel.messages.add(msgId to msg)
                    if (msg.message_type == "question" && msg.request_id != null) {
                        enqueuePendingQuestion(channelId, msgId, msg)
                    }
                }

                val current = _channels.value.toMutableMap()
                current[channelId] = channel
                _channels.value = current

                if (_selectedChannelId.value == null) {
                    _selectedChannelId.value = channelId
                }

                snapshot.child("messages").ref.addChildEventListener(object : ChildEventListener {
                    override fun onChildAdded(snap: DataSnapshot, prev: String?) {
                        val msgId = snap.key ?: return
                        val msg = snap.getValue(ChannelMessage::class.java) ?: return
                        addChannelMessage(channelId, msgId, msg)
                    }
                    override fun onChildChanged(snap: DataSnapshot, prev: String?) {}
                    override fun onChildRemoved(snap: DataSnapshot) {}
                    override fun onChildMoved(snap: DataSnapshot, prev: String?) {}
                    override fun onCancelled(error: DatabaseError) {}
                })
            }
            override fun onChildChanged(snapshot: DataSnapshot, previousChildName: String?) {}
            override fun onChildRemoved(snapshot: DataSnapshot) {}
            override fun onChildMoved(snapshot: DataSnapshot, previousChildName: String?) {}
            override fun onCancelled(error: DatabaseError) {}
        })
    }

    private fun addChannelMessage(channelId: String, msgId: String, msg: ChannelMessage) {
        val current = _channels.value.toMutableMap()
        val channel = current[channelId] ?: return
        if (channel.messages.any { it.first == msgId }) return
        channel.messages.add(msgId to msg)
        _channels.value = current
        if (msg.message_type == "question" && msg.request_id != null) {
            enqueuePendingQuestion(channelId, msgId, msg)
        }
        if (_selectedChannelId.value == null) {
            _selectedChannelId.value = channelId
        }
    }

    private fun enqueuePendingQuestion(channelId: String, msgId: String, msg: ChannelMessage) {
        val current = _pendingQuestions.value.toMutableMap()
        if (!current.containsKey(channelId)) {
            current[channelId] = msgId to msg
            _pendingQuestions.value = current
        }
    }

    fun replyToQuestion(channelId: String, msgId: String, requestId: String, text: String) {
        responsesRef.child(requestId).setValue(
            mapOf("text" to text, "timestamp" to System.currentTimeMillis())
        )
        val current = _pendingQuestions.value.toMutableMap()
        current.remove(channelId)
        _pendingQuestions.value = current
    }

    fun sendInjectMessage(channelId: String, text: String) {
        val injectRef = database.getReference("sessions/$channelId/inject_queue")
        injectRef.push().setValue(mapOf("content" to text, "timestamp" to System.currentTimeMillis()))
    }

    fun selectChannel(channelId: String) {
        _selectedChannelId.value = channelId
    }

    fun spawnSession(project: String, prompt: String, collab: Boolean = false) {
        val flags = if (collab) " --collab" else ""
        val command = if (project.isBlank()) "/spawn$flags $prompt"
                      else "/spawn $project$flags $prompt"
        commandsRef.push().setValue(command)
    }

    fun downloadAndOpenFile(context: Context, url: String, fileName: String) {
        val client = OkHttpClient()
        val request = Request.Builder().url(url).build()
        client.newCall(request).enqueue(object : okhttp3.Callback {
            override fun onFailure(call: okhttp3.Call, e: IOException) { e.printStackTrace() }
            override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                if (!response.isSuccessful) return
                val file = File(context.cacheDir, fileName)
                try {
                    FileOutputStream(file).use { output -> response.body?.byteStream()?.copyTo(output) }
                    Handler(Looper.getMainLooper()).post { openFile(context, file) }
                } catch (e: IOException) { e.printStackTrace() }
            }
        })
    }

    private fun openFile(context: Context, file: File) {
        val uri: Uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, context.contentResolver.getType(uri))
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(Intent.createChooser(intent, "Open with"))
    }
}
