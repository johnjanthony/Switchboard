package io.github.johnjanthony.switchboard

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.webkit.MimeTypeMap
import android.widget.Toast
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

    private val _unseenChannels = MutableStateFlow<Set<String>>(emptySet())
    val unseenChannels: StateFlow<Set<String>> = _unseenChannels.asStateFlow()

    private val database = FirebaseDatabase.getInstance()
    private val sessionsRef = database.getReference("sessions")
    private val responsesRef = database.getReference("responses")
    private val commandsRef = database.getReference("commands")

    private val messageListeners = mutableMapOf<String, ChildEventListener>()

    init {
        sessionsRef.keepSynced(true)
        setupChannelsListener()
    }

    private fun setupChannelsListener() {
        sessionsRef.addChildEventListener(object : ChildEventListener {
            override fun onChildAdded(snapshot: DataSnapshot, previousChildName: String?) {
                val channelId = snapshot.key ?: return
                ensureSessionSynchronized(channelId, snapshot)
            }

            override fun onChildChanged(snapshot: DataSnapshot, previousChildName: String?) {
                val channelId = snapshot.key ?: return
                
                // 1. Handle closure or reopening
                val state = snapshot.child("state").getValue(String::class.java)
                if (state == "closed") {
                    val current = _channels.value.toMutableMap()
                    if (current.containsKey(channelId)) {
                        current.remove(channelId)
                        _channels.value = current
                        if (_selectedChannelId.value == channelId) {
                            _selectedChannelId.value = _channels.value.keys.firstOrNull()
                        }
                    }
                    return
                }

                // 2. Ensure synchronized and update metadata
                ensureSessionSynchronized(channelId, snapshot)
            }

            override fun onChildRemoved(snapshot: DataSnapshot) {
                val channelId = snapshot.key ?: return
                cleanupSession(channelId)
            }
            override fun onChildMoved(snapshot: DataSnapshot, previousChildName: String?) {}
            override fun onCancelled(error: DatabaseError) {}
        })
    }

    private fun ensureSessionSynchronized(channelId: String, snapshot: DataSnapshot) {
        val state = snapshot.child("state").getValue(String::class.java)
        if (state == "closed") return

        val metaSnap = snapshot.child("meta")
        if (!metaSnap.exists()) return

        val type = metaSnap.child("type").getValue(String::class.java) ?: "single"
        val projectKey = metaSnap.child("project_key").getValue(String::class.java) ?: channelId
        val agentSenders = metaSnap.child("agent_senders").children
            .mapNotNull { it.getValue(String::class.java) }
        val task = metaSnap.child("task").getValue(String::class.java) ?: ""

        val current = _channels.value.toMutableMap()
        val existing = current[channelId]
        
        if (existing != null) {
            current[channelId] = existing.copy(
                type = type,
                projectKey = projectKey,
                agentSenders = agentSenders,
                task = task
            )
            _channels.value = current
        } else {
            val channel = Channel(
                channelId = channelId,
                type = type,
                projectKey = projectKey,
                agentSenders = agentSenders,
                task = task,
                messages = emptyList()
            )
            current[channelId] = channel
            _channels.value = current
            if (_selectedChannelId.value == null) {
                _selectedChannelId.value = channelId
            }
        }

        // Attach message listener if not already present
        if (!messageListeners.containsKey(channelId)) {
            val listener = object : ChildEventListener {
                override fun onChildAdded(snap: DataSnapshot, prev: String?) {
                    val msgId = snap.key ?: return
                    val msg = snap.getValue(ChannelMessage::class.java) ?: return
                    addChannelMessage(channelId, msgId, msg)
                }
                override fun onChildChanged(snap: DataSnapshot, prev: String?) {
                    val msgId = snap.key ?: return
                    val msg = snap.getValue(ChannelMessage::class.java) ?: return
                    addChannelMessage(channelId, msgId, msg)
                }
                override fun onChildRemoved(snap: DataSnapshot) {}
                override fun onChildMoved(snap: DataSnapshot, prev: String?) {}
                override fun onCancelled(error: DatabaseError) {}
            }
            messageListeners[channelId] = listener
            snapshot.child("messages").ref.addChildEventListener(listener)
        }
    }

    private fun cleanupSession(channelId: String) {
        val listener = messageListeners.remove(channelId)
        if (listener != null) {
            sessionsRef.child(channelId).child("messages").removeEventListener(listener)
        }
        
        val current = _channels.value.toMutableMap()
        if (current.containsKey(channelId)) {
            current.remove(channelId)
            _channels.value = current
            if (_selectedChannelId.value == channelId) {
                _selectedChannelId.value = _channels.value.keys.firstOrNull()
            }
        }
    }

    fun closeChannel(channelId: String) {
        // 1. Mark session as closed in Firebase
        sessionsRef.child(channelId).child("state").setValue("closed")

        // 2. Auto-reply to any pending question for this channel
        val pending = _pendingQuestions.value[channelId]
        if (pending != null) {
            val (msgId, msg) = pending
            if (msg.request_id != null) {
                replyToQuestion(channelId, msgId, msg.request_id!!, "I'm back at my desk now, let's proceed in the terminal")
            }
        }

        // 3. Update local state
        val current = _channels.value.toMutableMap()
        current.remove(channelId)
        _channels.value = current
        if (_selectedChannelId.value == channelId) {
            _selectedChannelId.value = _channels.value.keys.firstOrNull()
        }
    }

    private fun addChannelMessage(channelId: String, msgId: String, msg: ChannelMessage) {
        val current = _channels.value.toMutableMap()
        val channel = current[channelId] ?: return
        
        val newMessages = channel.messages.toMutableList()
        val existingIndex = newMessages.indexOfFirst { it.first == msgId }
        if (existingIndex != -1) {
            newMessages[existingIndex] = msgId to msg
        } else {
            newMessages.add(msgId to msg)
        }
        
        current[channelId] = channel.copy(messages = newMessages)
        _channels.value = current
        
        // Track unseen status
        if (msg.sender != "Human" && channelId != _selectedChannelId.value) {
            _unseenChannels.value = _unseenChannels.value + channelId
        }
        
        if (msg.message_type == "question" && msg.request_id != null) {
            val pending = _pendingQuestions.value.toMutableMap()
            if (msg.response_text != null) {
                // If it's answered, remove from pending if it was there
                if (pending[channelId]?.first == msgId) {
                    pending.remove(channelId)
                    _pendingQuestions.value = pending
                }
            } else {
                // Update or add pending question
                pending[channelId] = msgId to msg
                _pendingQuestions.value = pending
            }
        }
        
        if (_selectedChannelId.value == null) {
            _selectedChannelId.value = channelId
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
        _unseenChannels.value = _unseenChannels.value - channelId
    }

    fun spawnSession(project: String, prompt: String, useClaude: Boolean, useGemini: Boolean) {
        val sb = StringBuilder("/spawn")
        if (useClaude) sb.append(" --claude")
        if (useGemini) sb.append(" --gemini")
        if (project.isNotBlank()) sb.append(" $project")
        sb.append(" $prompt")
        commandsRef.push().setValue(sb.toString())
    }

    fun downloadAndOpenFile(context: Context, url: String, fileName: String) {
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            Toast.makeText(context, "Invalid URL", Toast.LENGTH_SHORT).show()
            return
        }
        val client = OkHttpClient()
        val request = Request.Builder().url(url).build()
        client.newCall(request).enqueue(object : okhttp3.Callback {
            override fun onFailure(call: okhttp3.Call, e: IOException) {
                Handler(Looper.getMainLooper()).post {
                    Toast.makeText(context, "Download failed: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
            override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                val body = response.body
                if (!response.isSuccessful || body == null) {
                    val errorMsg = try { body?.string()?.take(100) ?: "" } catch (e: Exception) { "" }
                    Handler(Looper.getMainLooper()).post {
                        Toast.makeText(context, "Server error: ${response.code} $errorMsg", Toast.LENGTH_LONG).show()
                    }
                    return
                }
                
                // Sanitize filename while preserving extension
                val ext = fileName.substringAfterLast('.', "")
                val nameWithoutExt = fileName.substringBeforeLast('.')
                val safeBase = nameWithoutExt.replace(Regex("[^a-zA-Z0-9.\\-_]"), "_").take(50)
                val safeFileName = if (ext.isNotEmpty() && ext != fileName) "$safeBase.$ext" else safeBase
                
                val file = File(context.cacheDir, safeFileName)
                try {
                    FileOutputStream(file).use { output -> body.byteStream().copyTo(output) }
                    Handler(Looper.getMainLooper()).post { openFile(context, file) }
                } catch (e: IOException) {
                    Handler(Looper.getMainLooper()).post {
                        Toast.makeText(context, "Save failed: ${e.message}", Toast.LENGTH_LONG).show()
                    }
                }
            }
        })
    }

    private fun openFile(context: Context, file: File) {
        try {
            val uri: Uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
            val mimeType = context.contentResolver.getType(uri) ?: "application/octet-stream"

            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, mimeType)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            
            val chooser = Intent.createChooser(intent, "Open ${file.name}")
            chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(chooser)
        } catch (e: Exception) {
            Handler(Looper.getMainLooper()).post {
                Toast.makeText(context, "Cannot open file: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }
}
