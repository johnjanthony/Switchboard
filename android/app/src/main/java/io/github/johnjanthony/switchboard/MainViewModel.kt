package io.github.johnjanthony.switchboard

import android.app.Application
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.webkit.MimeTypeMap
import android.widget.Toast
import androidx.lifecycle.AndroidViewModel
import androidx.core.content.FileProvider
import com.google.firebase.database.ChildEventListener
import com.google.firebase.database.DataSnapshot
import com.google.firebase.database.DatabaseError
import com.google.firebase.database.FirebaseDatabase
import com.google.firebase.database.ValueEventListener
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

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val _channels = MutableStateFlow<Map<String, Channel>>(emptyMap())
    val channels: StateFlow<Map<String, Channel>> = _channels.asStateFlow()

    private val _projectMru = MutableStateFlow<List<String>>(emptyList())
    val projectMru: StateFlow<List<String>> = _projectMru.asStateFlow()

    // Per-channel: the current pending question (msgId, message), or null
    private val _pendingQuestions = MutableStateFlow<Map<String, Pair<String, ChannelMessage>>>(emptyMap())
    val pendingQuestions: StateFlow<Map<String, Pair<String, ChannelMessage>>> = _pendingQuestions.asStateFlow()

    private val _selectedChannelId = MutableStateFlow<String?>(null)
    val selectedChannelId: StateFlow<String?> = _selectedChannelId.asStateFlow()

    private val _unseenChannels = MutableStateFlow<Set<String>>(emptySet())
    val unseenChannels: StateFlow<Set<String>> = _unseenChannels.asStateFlow()

    private val _hiddenChannels = MutableStateFlow<Map<String, Channel>>(emptyMap())
    val hiddenChannels: StateFlow<Map<String, Channel>> = _hiddenChannels.asStateFlow()

    private val _awayModeActive = MutableStateFlow(false)
    val awayModeActive: StateFlow<Boolean> = _awayModeActive.asStateFlow()
    private var lastAppliedAwayModeUpdate: Long = 0L

    private val database = FirebaseDatabase.getInstance()
    private val sessionsRef = database.getReference("sessions")
    private val responsesRef = database.getReference("responses")
    private val commandsRef = database.getReference("commands")
    private val awayModeRef = database.getReference("away_mode")

    private val messageListeners = mutableMapOf<String, ChildEventListener>()

    init {
        sessionsRef.keepSynced(true)
        setupChannelsListener()
        setupAwayModeListener()
        loadProjectMru()
    }

    private fun loadProjectMru() {
        val prefs = getApplication<Application>().getSharedPreferences("switchboard_prefs", Context.MODE_PRIVATE)
        val mruString = prefs.getString("project_mru", "") ?: ""
        if (mruString.isNotEmpty()) {
            _projectMru.value = mruString.split("|").filter { it.isNotBlank() }
        }
    }

    private fun saveProjectMru(mru: List<String>) {
        val prefs = getApplication<Application>().getSharedPreferences("switchboard_prefs", Context.MODE_PRIVATE)
        prefs.edit().putString("project_mru", mru.joinToString("|")).apply()
    }

    private fun updateProjectMru(project: String) {
        if (project.isBlank()) return
        val current = _projectMru.value.toMutableList()
        current.remove(project)
        current.add(0, project)
        val limited = current.take(10)
        _projectMru.value = limited
        saveProjectMru(limited)
    }

    fun removeFromProjectMru(project: String) {
        val current = _projectMru.value.toMutableList()
        if (current.remove(project)) {
            _projectMru.value = current
            saveProjectMru(current)
        }
    }

    private fun setupChannelsListener() {
        sessionsRef.addChildEventListener(object : ChildEventListener {
            override fun onChildAdded(snapshot: DataSnapshot, previousChildName: String?) {
                val channelId = snapshot.key ?: return
                ensureSessionSynchronized(channelId, snapshot)
            }

            override fun onChildChanged(snapshot: DataSnapshot, previousChildName: String?) {
                val channelId = snapshot.key ?: return
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

    private fun setupAwayModeListener() {
        awayModeRef.addValueEventListener(object : ValueEventListener {
            override fun onDataChange(snapshot: DataSnapshot) {
                // Guard against out-of-order delivery during Firebase reconnect.
                // If updated_at is present AND strictly older than the last
                // applied value, discard. Missing updated_at (legacy/malformed)
                // falls through and is applied — we cannot prove it stale.
                val updatedAt = snapshot.child("updated_at").getValue(Long::class.java)
                if (updatedAt != null && updatedAt < lastAppliedAwayModeUpdate) return
                if (updatedAt != null) lastAppliedAwayModeUpdate = updatedAt
                _awayModeActive.value = snapshot.child("active").getValue(Boolean::class.java) == true
            }
            override fun onCancelled(error: DatabaseError) {}
        })
    }

    private fun ensureSessionSynchronized(channelId: String, snapshot: DataSnapshot) {
        val state = snapshot.child("state").getValue(String::class.java)
        val hiddenField = snapshot.child("hidden").getValue(Boolean::class.java)
        val isHidden = hiddenField == true || state == "closed"

        val metaSnap = snapshot.child("meta")
        if (!metaSnap.exists()) return

        val type = metaSnap.child("type").getValue(String::class.java) ?: "single"
        val projectKey = metaSnap.child("project_key").getValue(String::class.java) ?: channelId
        val agentSenders = metaSnap.child("agent_senders").children
            .mapNotNull { it.getValue(String::class.java) }
        val task = metaSnap.child("task").getValue(String::class.java) ?: ""

        val existing = _channels.value[channelId] ?: _hiddenChannels.value[channelId]

        if (existing != null) {
            val updated = existing.copy(
                type = type,
                projectKey = projectKey,
                agentSenders = agentSenders,
                task = task,
                hidden = isHidden,
            )
            movePartition(channelId, updated, isHidden)
        } else {
            val channel = Channel(
                channelId = channelId,
                type = type,
                projectKey = projectKey,
                agentSenders = agentSenders,
                task = task,
                messages = emptyList(),
                hidden = isHidden,
            )
            movePartition(channelId, channel, isHidden)
            if (!isHidden && _selectedChannelId.value == null) {
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

    private fun movePartition(channelId: String, channel: Channel, hidden: Boolean) {
        val visible = _channels.value.toMutableMap()
        val hiddenMap = _hiddenChannels.value.toMutableMap()
        if (hidden) {
            visible.remove(channelId)
            hiddenMap[channelId] = channel
        } else {
            hiddenMap.remove(channelId)
            visible[channelId] = channel
        }
        _channels.value = visible
        _hiddenChannels.value = hiddenMap

        if (hidden && _selectedChannelId.value == channelId) {
            _selectedChannelId.value = _channels.value.keys.firstOrNull()
        }
    }

    fun hideChannel(channelId: String) {
        sessionsRef.child(channelId).child("hidden").setValue(true)
    }

    fun unhideChannel(channelId: String) {
        sessionsRef.child(channelId).child("hidden").setValue(false)
        // Select the channel so the user goes straight to it after unhiding.
        _selectedChannelId.value = channelId
    }

    private fun addChannelMessage(channelId: String, msgId: String, msg: ChannelMessage) {
        val visible = _channels.value.toMutableMap()
        val hiddenMap = _hiddenChannels.value.toMutableMap()

        val inVisible = visible[channelId]
        val inHidden = hiddenMap[channelId]
        val channel = inVisible ?: inHidden ?: return

        val newMessages = channel.messages.toMutableList()
        val existingIndex = newMessages.indexOfFirst { it.first == msgId }
        if (existingIndex != -1) {
            newMessages[existingIndex] = msgId to msg
        } else {
            newMessages.add(msgId to msg)
        }

        val updated = channel.copy(messages = newMessages)
        if (inVisible != null) {
            visible[channelId] = updated
            _channels.value = visible
        } else {
            hiddenMap[channelId] = updated
            _hiddenChannels.value = hiddenMap
        }

        // Track unseen status — apply to hidden channels too; the Hidden Channels
        // dialog uses _unseenChannels to render the subtle primary-colour adornment.
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
                // Update or add pending question — tracks for BOTH visible and
                // hidden channels so the bulk-respond flow (Slice J) can reach them.
                pending[channelId] = msgId to msg
                _pendingQuestions.value = pending
            }
        }

        if (_selectedChannelId.value == null && inVisible != null) {
            // Only auto-select visible channels on first-message arrival.
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
        updateProjectMru(project)
        val sb = StringBuilder("/spawn")
        if (useClaude) sb.append(" --claude")
        if (useGemini) sb.append(" --gemini")
        if (project.isNotBlank()) sb.append(" $project")
        sb.append(" $prompt")
        commandsRef.push().setValue(sb.toString())
    }

    fun requestAwayModeToggle(desired: Boolean) {
        // Optimistic UI update — the pill flips immediately. The Firebase listener
        // will reconcile to the authoritative server value when the mirror write
        // round-trips back (typically within 2-3 seconds).
        _awayModeActive.value = desired
        val cmd = if (desired) "/away-mode on" else "/away-mode off"
        commandsRef.push().setValue(cmd)
    }

    fun bulkRespondAndExit(text: String) {
        // Snapshot the pending questions to iterate a stable view; also covers
        // hidden channels because Slice E's addChannelMessage fix tracks
        // _pendingQuestions for both partitions.
        val snapshot = _pendingQuestions.value.toMap()
        snapshot.forEach { (_, pair) ->
            val (_, msg) = pair
            val reqId = msg.request_id
            if (!reqId.isNullOrEmpty()) {
                responsesRef.child(reqId).setValue(
                    mapOf("text" to text, "timestamp" to System.currentTimeMillis())
                )
            }
        }
        _pendingQuestions.value = emptyMap()
        requestAwayModeToggle(false)
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
