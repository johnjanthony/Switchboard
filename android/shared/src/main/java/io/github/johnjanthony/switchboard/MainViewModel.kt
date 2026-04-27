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
import io.github.johnjanthony.switchboard.network.BulkRespondEntry
import io.github.johnjanthony.switchboard.network.BulkRespondPayload
import io.github.johnjanthony.switchboard.network.BulkRespondSection
import io.github.johnjanthony.switchboard.network.Channel
import io.github.johnjanthony.switchboard.network.ChannelMessage
import io.github.johnjanthony.switchboard.network.Pending
import io.github.johnjanthony.switchboard.network.SpawnCollisionData
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

class MainViewModel(application: Application) : AndroidViewModel(application) {

	// Keyed by cwdKey (Firebase form, e.g. "c:__work__switchboard")
	private val _channels = MutableStateFlow<Map<String, Channel>>(emptyMap())
	val channels: StateFlow<Map<String, Channel>> = _channels.asStateFlow()

	private val _projectMru = MutableStateFlow<List<String>>(emptyList())
	val projectMru: StateFlow<List<String>> = _projectMru.asStateFlow()

	private val _globalAway = MutableStateFlow(false)
	val globalAway: StateFlow<Boolean> = _globalAway.asStateFlow()

	// Keys are cwdKey
	private val _cwdOverrides = MutableStateFlow<Map<String, Boolean>>(emptyMap())
	val cwdOverrides: StateFlow<Map<String, Boolean>> = _cwdOverrides.asStateFlow()

	private val _pendingCollision = MutableStateFlow<SpawnCollisionData?>(null)
	val pendingCollision: StateFlow<SpawnCollisionData?> = _pendingCollision.asStateFlow()

	private val _bulkRespondDialog = MutableStateFlow<BulkRespondPayload?>(null)
	val bulkRespondDialog: StateFlow<BulkRespondPayload?> = _bulkRespondDialog.asStateFlow()

	private val _selectedCwdKey = MutableStateFlow<String?>(null)
	val selectedCwdKey: StateFlow<String?> = _selectedCwdKey.asStateFlow()

	private val _unseenChannels = MutableStateFlow<Set<String>>(emptySet())
	val unseenChannels: StateFlow<Set<String>> = _unseenChannels.asStateFlow()

	private val database = FirebaseDatabase.getInstance()
	private val channelsRef = database.getReference("channels")
	private val responsesRef = database.getReference("responses")
	private val commandsRef = database.getReference("commands")
	private val awayModeRef = database.getReference("away_mode")
	private val awayCommandsRef = database.getReference("away_mode_commands")
	private val spawnCollisionsRef = database.getReference("spawn_collisions")
	private val bulkRespondRef = database.getReference("bulk_respond_dialog")

	private val messageListeners = mutableMapOf<String, ChildEventListener>()

	init {
		channelsRef.keepSynced(true)
		setupChannelsListener()
		setupAwayModeListener()
		setupSpawnCollisionsListener()
		setupBulkRespondListener()
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

	fun isAwayActive(cwdKey: String): Boolean {
		val override = _cwdOverrides.value[cwdKey]
		return override ?: _globalAway.value
	}

	// --- Firebase listeners ---

	private fun setupChannelsListener() {
		channelsRef.addChildEventListener(object : ChildEventListener {
			override fun onChildAdded(snapshot: DataSnapshot, previousChildName: String?) {
				val cwdKey = snapshot.key ?: return
				syncChannel(cwdKey, snapshot)
			}
			override fun onChildChanged(snapshot: DataSnapshot, previousChildName: String?) {
				val cwdKey = snapshot.key ?: return
				syncChannel(cwdKey, snapshot)
			}
			override fun onChildRemoved(snapshot: DataSnapshot) {
				val cwdKey = snapshot.key ?: return
				removeChannel(cwdKey)
			}
			override fun onChildMoved(snapshot: DataSnapshot, previousChildName: String?) {}
			override fun onCancelled(error: DatabaseError) {}
		})
	}

	private fun syncChannel(cwdKey: String, snapshot: DataSnapshot) {
		val hidden = snapshot.child("hidden").getValue(Boolean::class.java) == true
		val title = snapshot.child("title").getValue(String::class.java)
		val cwdCanonical = snapshot.child("cwd_canonical").getValue(String::class.java) ?: ""
		val lastActivityAt = snapshot.child("last_activity_at").getValue(String::class.java)
		val preview = snapshot.child("preview").getValue(String::class.java)
		val unreadCount = snapshot.child("unread_count").getValue(Int::class.java) ?: 0
		val cwd = cwdCanonical.ifBlank { fromFirebaseKey(cwdKey) }

		val existing = _channels.value[cwdKey]
		val updated = (existing ?: Channel(cwd = cwd, cwdKey = cwdKey)).copy(
			cwd = cwd,
			cwdKey = cwdKey,
			title = title,
			cwdCanonical = cwdCanonical,
			hidden = hidden,
			lastActivityAt = lastActivityAt,
			preview = preview,
			unreadCount = unreadCount,
		)
		val newMap = _channels.value.toMutableMap()
		newMap[cwdKey] = updated
		_channels.value = newMap

		if (_selectedCwdKey.value == null && !hidden) {
			_selectedCwdKey.value = cwdKey
		}
		if (hidden && _selectedCwdKey.value == cwdKey) {
			_selectedCwdKey.value = _channels.value.entries.firstOrNull { !it.value.hidden }?.key
		}

		if (!messageListeners.containsKey(cwdKey)) {
			val listener = object : ChildEventListener {
				override fun onChildAdded(snap: DataSnapshot, prev: String?) {
					val msgId = snap.key ?: return
					try {
						val msg = snap.getValue(ChannelMessage::class.java) ?: return
						addMessage(cwdKey, msgId, msg)
					} catch (e: Exception) {
						android.util.Log.e("MainViewModel", "MALFORMED MESSAGE at channels/$cwdKey/messages/$msgId")
						android.util.Log.e("MainViewModel", "Value Type: ${snap.value?.javaClass?.name}")
						android.util.Log.e("MainViewModel", "Value Content: ${snap.value}")
						android.util.Log.e("MainViewModel", "Error: ${e.message}")
					}
				}
				override fun onChildChanged(snap: DataSnapshot, prev: String?) {
					val msgId = snap.key ?: return
					try {
						val msg = snap.getValue(ChannelMessage::class.java) ?: return
						addMessage(cwdKey, msgId, msg)
					} catch (e: Exception) {
						android.util.Log.e("MainViewModel", "MALFORMED MESSAGE (update) at channels/$cwdKey/messages/$msgId")
						android.util.Log.e("MainViewModel", "Value Type: ${snap.value?.javaClass?.name}")
						android.util.Log.e("MainViewModel", "Value Content: ${snap.value}")
						android.util.Log.e("MainViewModel", "Error: ${e.message}")
					}
				}
				override fun onChildRemoved(snap: DataSnapshot) {}
				override fun onChildMoved(snap: DataSnapshot, prev: String?) {}
				override fun onCancelled(error: DatabaseError) {}
			}
			messageListeners[cwdKey] = listener
			snapshot.child("messages").ref.addChildEventListener(listener)
		}
	}

	private fun removeChannel(cwdKey: String) {
		val listener = messageListeners.remove(cwdKey)
		if (listener != null) {
			channelsRef.child(cwdKey).child("messages").removeEventListener(listener)
		}
		val newMap = _channels.value.toMutableMap()
		if (newMap.remove(cwdKey) != null) {
			_channels.value = newMap
			if (_selectedCwdKey.value == cwdKey) {
				_selectedCwdKey.value = newMap.entries.firstOrNull { !it.value.hidden }?.key
			}
		}
	}

	private fun addMessage(cwdKey: String, msgId: String, msg: ChannelMessage) {
		val channel = _channels.value[cwdKey] ?: return
		val newMessages = channel.messages.toMutableList()
		val idx = newMessages.indexOfFirst { it.first == msgId }
		if (idx >= 0) newMessages[idx] = msgId to msg else newMessages.add(msgId to msg)

		var newPending = channel.pendingQuestions.toMutableMap()
		if (msg.type == "question" && msg.request_id != null) {
			if (msg.response_text != null || msg.cancelled) {
				newPending.remove(msg.request_id!!)
			} else {
				newPending[msg.request_id!!] = Pending(
					sender = msg.sender,
					requestId = msg.request_id!!,
					questionText = msg.text,
					cancelled = msg.cancelled,
					msgId = msgId,
					suggestions = msg.suggestions,
				)
			}
		}

		val updated = channel.copy(messages = newMessages, pendingQuestions = newPending)
		val newMap = _channels.value.toMutableMap()
		newMap[cwdKey] = updated
		_channels.value = newMap

		if (msg.sender != "Human" && cwdKey != _selectedCwdKey.value) {
			_unseenChannels.value = _unseenChannels.value + cwdKey
		}
		if (_selectedCwdKey.value == null && !channel.hidden) {
			_selectedCwdKey.value = cwdKey
		}
	}

	private fun setupAwayModeListener() {
		awayModeRef.child("global").addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				_globalAway.value = snapshot.getValue(Boolean::class.java) == true
			}
			override fun onCancelled(error: DatabaseError) {}
		})
		awayModeRef.child("overrides").addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				val map = mutableMapOf<String, Boolean>()
				for (child in snapshot.children) {
					val key = child.key ?: continue
					val value = child.getValue(Boolean::class.java) ?: continue
					map[key] = value
				}
				_cwdOverrides.value = map
			}
			override fun onCancelled(error: DatabaseError) {}
		})
	}

	private fun setupSpawnCollisionsListener() {
		spawnCollisionsRef.addChildEventListener(object : ChildEventListener {
			override fun onChildAdded(snapshot: DataSnapshot, previousChildName: String?) {
				val spawnId = snapshot.key ?: return
				val cwd = snapshot.child("cwd").getValue(String::class.java) ?: return
				val cwdKey = snapshot.child("cwd_key").getValue(String::class.java) ?: return
				val channelTitle = snapshot.child("channel_title").getValue(String::class.java)
				val lastActivityAt = snapshot.child("last_activity_at").getValue(String::class.java)
				val hidden = snapshot.child("hidden").getValue(Boolean::class.java) == true
				_pendingCollision.value = SpawnCollisionData(spawnId, cwd, cwdKey, channelTitle, lastActivityAt, hidden)
			}
			override fun onChildChanged(snapshot: DataSnapshot, previousChildName: String?) {}
			override fun onChildRemoved(snapshot: DataSnapshot) {
				if (_pendingCollision.value?.spawnId == snapshot.key) {
					_pendingCollision.value = null
				}
			}
			override fun onChildMoved(snapshot: DataSnapshot, previousChildName: String?) {}
			override fun onCancelled(error: DatabaseError) {}
		})
	}

	private fun setupBulkRespondListener() {
		bulkRespondRef.child("active").addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				if (!snapshot.exists()) {
					_bulkRespondDialog.value = null
					return
				}
				val defaultText = snapshot.child("default_text").getValue(String::class.java) ?: ""
				val sections = snapshot.child("sections").children.mapNotNull { sectionSnap ->
					val cwd = sectionSnap.child("cwd").getValue(String::class.java) ?: return@mapNotNull null
					val entries = sectionSnap.child("entries").children.mapNotNull { entrySnap ->
						val requestId = entrySnap.child("request_id").getValue(String::class.java) ?: return@mapNotNull null
						val sender = entrySnap.child("sender").getValue(String::class.java) ?: return@mapNotNull null
						val questionText = entrySnap.child("question_text").getValue(String::class.java) ?: ""
						BulkRespondEntry(requestId, sender, questionText)
					}
					BulkRespondSection(cwd, entries)
				}
				_bulkRespondDialog.value = BulkRespondPayload(sections, defaultText)
			}
			override fun onCancelled(error: DatabaseError) {}
		})
	}

	// --- Public actions ---

	fun selectChannel(cwdKey: String) {
		_selectedCwdKey.value = cwdKey
		_unseenChannels.value = _unseenChannels.value - cwdKey
	}

	fun hasAnyPendingQuestions(): Boolean {
		return _channels.value.values.any { it.pendingQuestions.isNotEmpty() }
	}

	fun submitReply(cwdKey: String, sender: String, text: String) {
		responsesRef.child("${cwdKey}__$sender").setValue(mapOf(
			"text" to text,
			"written_at" to nowIso(),
		))
		// Remove from pending optimistically
		val channel = _channels.value[cwdKey] ?: return
		val newPending = channel.pendingQuestions.filterValues { it.sender != sender }
		_channels.value = _channels.value.toMutableMap().also { it[cwdKey] = channel.copy(pendingQuestions = newPending) }
	}

	fun requestAwayModeToggle(cwdKey: String?, desired: Boolean) {
		if (cwdKey == null) {
			_globalAway.value = desired
			if (desired) enterGlobalAway() else exitGlobalAway()
		} else {
			val newOverrides = _cwdOverrides.value.toMutableMap()
			newOverrides[cwdKey] = desired
			_cwdOverrides.value = newOverrides
			val cwd = _channels.value[cwdKey]?.cwd ?: return
			if (desired) enterCwdAway(cwd) else exitCwdAway(cwd)
		}
	}

	fun hideChannel(cwdKey: String) {
		channelsRef.child(cwdKey).child("hidden").setValue(true)
	}

	fun unhideChannel(cwdKey: String) {
		channelsRef.child(cwdKey).child("hidden").setValue(false)
		_selectedCwdKey.value = cwdKey
	}

	fun resolveSpawnCollision(spawnId: String, action: String) {
		spawnCollisionsRef.child(spawnId).child("decision").setValue(action)
	}

	fun submitBulkRespond(action: String, defaultText: String? = null) {
		val payload = mutableMapOf<String, Any>("action" to action)
		if (defaultText != null) payload["default_text"] = defaultText
		bulkRespondRef.child("decision").setValue(payload)
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

	// --- Away mode command emitters ---

	private fun enterGlobalAway() {
		awayCommandsRef.push().setValue(mapOf("type" to "enter_global", "issued_at" to nowIso()))
	}

	private fun exitGlobalAway() {
		awayCommandsRef.push().setValue(mapOf("type" to "exit_global", "issued_at" to nowIso()))
	}

	private fun enterCwdAway(cwd: String) {
		awayCommandsRef.push().setValue(mapOf("type" to "enter_cwd", "cwd" to cwd, "issued_at" to nowIso()))
	}

	private fun exitCwdAway(cwd: String) {
		awayCommandsRef.push().setValue(mapOf("type" to "exit_cwd", "cwd" to cwd, "issued_at" to nowIso()))
	}

	// --- Utilities ---

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

	private fun nowIso(): String = DateTimeFormatter.ISO_INSTANT.format(Instant.now())

	private fun fromFirebaseKey(key: String): String {
		val result = StringBuilder()
		var i = 0
		while (i < key.length) {
			when {
				key.startsWith("____", i) -> { result.append('_'); i += 4 }
				key.startsWith("__", i) -> { result.append('/'); i += 2 }
				else -> { result.append(key[i]); i++ }
			}
		}
		return result.toString()
	}
}
