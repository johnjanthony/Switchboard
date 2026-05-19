package io.github.johnjanthony.switchboard

import android.app.Application
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
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
import io.github.johnjanthony.switchboard.network.PendingExitToggle
import io.github.johnjanthony.switchboard.network.AgentStatus
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

	companion object {
		// Default response text seeded into the bulk-respond modal when the user
		// toggles away-mode off and there are pending ask_human questions in
		// scope. The user can edit this in the modal before tapping "send to all";
		// the edited value rides on the away_mode_commands exit command as
		// `default_text`, and the server applies it on `decision == "send_default"`.
		private const val BULK_RESPOND_DEFAULT_TEXT = "I'll respond when I'm back at my desk."
	}

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

	private val _pendingExitToggle = MutableStateFlow<PendingExitToggle?>(null)
	val pendingExitToggle: StateFlow<PendingExitToggle?> = _pendingExitToggle.asStateFlow()

	// Set when the user swipes a channel row to "At desk" AND the channel has no
	// pending questions — without pendings the bulk-respond modal would not pop,
	// so we surface a plain confirm dialog instead. Value is the cwdKey awaiting
	// confirmation, or null when no swipe is pending.
	private val _pendingSwipeAtDeskConfirm = MutableStateFlow<String?>(null)
	val pendingSwipeAtDeskConfirm: StateFlow<String?> = _pendingSwipeAtDeskConfirm.asStateFlow()

	private val _markdownViewerContent = MutableStateFlow<Pair<String, String>?>(null) // fileName to content
	val markdownViewerContent: StateFlow<Pair<String, String>?> = _markdownViewerContent.asStateFlow()

	private val _selectedCwdKey = MutableStateFlow<String?>(null)
	val selectedCwdKey: StateFlow<String?> = _selectedCwdKey.asStateFlow()

	private val _pendingDeepLinkMessageId = MutableStateFlow<String?>(null)
	val pendingDeepLinkMessageId: StateFlow<String?> = _pendingDeepLinkMessageId.asStateFlow()

	private val database = FirebaseDatabase.getInstance()
	private val channelsRef = database.getReference("channels")
	private val responsesRef = database.getReference("responses")
	private val commandsRef = database.getReference("commands")
	private val awayCommandsRef = database.getReference("away_mode_commands")
	private val spawnCollisionsRef = database.getReference("spawn_collisions")
	private val globalAwayRef = database.getReference("global_settings/away_mode")

	private val messageListeners = mutableMapOf<String, ChildEventListener>()

	init {
		channelsRef.keepSynced(true)
		setupChannelsListener()
		setupAwayModeListener()
		setupSpawnCollisionsListener()
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
		val awayMode = snapshot.child("away_mode").getValue(Boolean::class.java)
		val pendingResponses = snapshot.child("pending_responses").getValue(Int::class.java) ?: 0
		val cwd = cwdCanonical.ifBlank { fromFirebaseKey(cwdKey) }

		val asSnap = snapshot.child("agent_status")
		val agentStatus: AgentStatus? =
			if (asSnap.exists()) {
				val sender  = asSnap.child("sender").getValue(String::class.java)
				val state   = asSnap.child("state").getValue(String::class.java)
				val detail  = asSnap.child("detail").getValue(String::class.java)
				val updated = asSnap.child("updated_at").getValue(Long::class.java) ?: 0L
				if (sender != null && state != null && updated > 0L)
					AgentStatus(sender, state, detail, updated)
				else
					null
			} else null

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
			awayMode = awayMode,
			pendingResponses = pendingResponses,
			agentStatus = agentStatus,
		)
		val newMap = _channels.value.toMutableMap()
		newMap[cwdKey] = updated
		_channels.value = newMap
		recomputeCwdOverrides()

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
				override fun onChildRemoved(snap: DataSnapshot) {
					val msgId = snap.key ?: return
					removeMessage(cwdKey, msgId)
				}
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
			recomputeCwdOverrides()
			if (_selectedCwdKey.value == cwdKey) {
				_selectedCwdKey.value = newMap.entries.firstOrNull { !it.value.hidden }?.key
			}
		}
	}

	private fun recomputeCwdOverrides() {
		// Derive _cwdOverrides from each channel's awayMode field. A null awayMode
		// means "follow global"; a non-null value (true or false) is the channel's
		// own override of the global flag. This keeps a single source of truth —
		// `channels/{key}/away_mode` — instead of mirroring overrides into a
		// separate Firebase node.
		val map = mutableMapOf<String, Boolean>()
		for ((key, ch) in _channels.value) {
			val v = ch.awayMode
			if (v != null) map[key] = v
		}
		_cwdOverrides.value = map
	}

	private fun isQuestionType(type: String): Boolean {
		return type == "question" || type == "ask_human"
	}

	private fun addMessage(cwdKey: String, msgId: String, msg: ChannelMessage) {
		val channel = _channels.value[cwdKey] ?: return

		// Maintain the raw arrival-order list. The current channel.messages may already
		// be spliced from a prior addMessage call, so we re-derive from a sorted-by-msgId
		// snapshot. Firebase push keys are time-ordered, so sortedBy { it.first } gives
		// us a deterministic arrival order regardless of in-list splice state.
		val rawMessages = channel.messages.toMutableList()
		val idx = rawMessages.indexOfFirst { it.first == msgId }
		if (idx >= 0) rawMessages[idx] = msgId to msg else rawMessages.add(msgId to msg)
		val sortedRaw = rawMessages.sortedBy { it.first }

		// Apply splice to produce display order.
		val displayMessages = applySpliceOrder(sortedRaw)

		// Derive answered-set: any message whose attached_to_msg_id names a known message
		// marks that named message as answered.
		val answeredSet: Set<String> = sortedRaw
			.mapNotNull { (_, m) -> m.attached_to_msg_id }
			.filter { targetId -> sortedRaw.any { it.first == targetId } }
			.toSet()

		// pendingQuestions / pendingResponses: a question is "no longer pending"
		// when it's cancelled, rejected, OR has a reply attached.
		var newPending = channel.pendingQuestions.toMutableMap()
		var newPendingResponses = channel.pendingResponses
		if (isQuestionType(msg.type) && msg.request_id != null) {
			val isAnsweredViaSplice = msgId in answeredSet
			if (msg.cancelled || msg.rejected || isAnsweredViaSplice) {
				if (newPending.containsKey(msg.request_id)) {
					newPending.remove(msg.request_id!!)
					newPendingResponses = (newPendingResponses - 1).coerceAtLeast(0)
				}
			} else {
				if (!newPending.containsKey(msg.request_id)) {
					newPendingResponses += 1
				}
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
		// Also: when the new message itself is a reply (has attached_to_msg_id), the
		// question it points at must drop out of pending.
		msg.attached_to_msg_id?.let { targetMsgId ->
			val targetQuestion = sortedRaw.firstOrNull { it.first == targetMsgId }?.second
			val targetRequestId = targetQuestion?.request_id
			if (targetRequestId != null && newPending.containsKey(targetRequestId)) {
				newPending.remove(targetRequestId)
				newPendingResponses = (newPendingResponses - 1).coerceAtLeast(0)
			}
		}

		val updated = channel.copy(
			messages = displayMessages,
			pendingQuestions = newPending,
			pendingResponses = newPendingResponses,
			answeredQuestionMsgIds = answeredSet,
		)
		val newMap = _channels.value.toMutableMap()
		newMap[cwdKey] = updated
		_channels.value = newMap

		if (_selectedCwdKey.value == cwdKey) {
			channelsRef.child(cwdKey).child("unread_count").setValue(0)
		} else if (_selectedCwdKey.value == null && !channel.hidden) {
			_selectedCwdKey.value = cwdKey
		}

		if (msg.rejected) {
			Handler(Looper.getMainLooper()).post {
				Toast.makeText(getApplication(), msg.text, Toast.LENGTH_LONG).show()
			}
		}
	}

	private fun removeMessage(cwdKey: String, msgId: String) {
		val channel = _channels.value[cwdKey] ?: return
		val rawMessages = channel.messages.filterNot { it.first == msgId }
		if (rawMessages.size == channel.messages.size) return
		val removed = channel.messages.firstOrNull { it.first == msgId }?.second

		// Re-derive both display order and answered set from the trimmed raw list.
		val sortedRaw = rawMessages.sortedBy { it.first }
		val displayMessages = applySpliceOrder(sortedRaw)
		val answeredSet: Set<String> = sortedRaw
			.mapNotNull { (_, m) -> m.attached_to_msg_id }
			.filter { targetId -> sortedRaw.any { it.first == targetId } }
			.toSet()

		val newPending = channel.pendingQuestions.toMutableMap()
		var newPendingResponses = channel.pendingResponses
		if (removed != null && isQuestionType(removed.type) && removed.request_id != null) {
			if (newPending.containsKey(removed.request_id)) {
				newPending.remove(removed.request_id)
				newPendingResponses = (newPendingResponses - 1).coerceAtLeast(0)
			}
		}
		val updated = channel.copy(
			messages = displayMessages,
			pendingQuestions = newPending,
			pendingResponses = newPendingResponses,
			answeredQuestionMsgIds = answeredSet,
		)
		val newMap = _channels.value.toMutableMap()
		newMap[cwdKey] = updated
		_channels.value = newMap
	}

	private fun setupAwayModeListener() {
		// Per-channel away-mode lives in each channel snapshot's `away_mode` field
		// (parsed in syncChannel + materialized via recomputeCwdOverrides). We only
		// listen to the global flag here.
		globalAwayRef.addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				_globalAway.value = snapshot.getValue(Boolean::class.java) == true
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

	// --- Public actions ---

	fun selectChannel(cwdKey: String) {
		_selectedCwdKey.value = cwdKey
		// Clear the server-maintained unread badge for this channel so the
		// indicator drops on every device subscribed to this Firebase node.
		channelsRef.child(cwdKey).child("unread_count").setValue(0)
	}

	fun clearSelectedChannel() {
		_selectedCwdKey.value = null
	}

	fun setPendingDeepLinkMessageId(messageId: String?) {
		_pendingDeepLinkMessageId.value = messageId
	}

	fun clearPendingDeepLinkMessageId() {
		_pendingDeepLinkMessageId.value = null
	}

	fun closeMarkdownViewer() {
		_markdownViewerContent.value = null
	}

	fun hasAnyPendingQuestions(): Boolean {
		return _channels.value.values.any { it.pendingQuestions.isNotEmpty() }
	}

	fun markMessageOpened(cwdKey: String, msgId: String) {
		channelsRef.child(cwdKey).child("messages").child(msgId).child("opened").setValue(true)
	}

	fun submitReply(cwdKey: String, sender: String, text: String, requestId: String?) {
		val key = requestId ?: "${cwdKey}__$sender"
		responsesRef.child(key).setValue(mapOf(
			"text" to text,
			"cwd_key" to cwdKey,
			"sender" to sender,
			"request_id" to requestId,
			"written_at" to nowIso(),
		))
		// Remove from pending optimistically. Decrement pendingResponses by the
		// number of pending entries we're dropping for this sender so the row-
		// level pending dot clears immediately rather than after the Firebase
		// echo round-trip. Server's atomic decrement on resolve produces the
		// same final value.
		val channel = _channels.value[cwdKey] ?: return
		val droppedCount = if (requestId != null) {
			if (channel.pendingQuestions.containsKey(requestId)) 1 else 0
		} else {
			channel.pendingQuestions.values.count { it.sender == sender }
		}
		val newPending = if (requestId != null) {
			channel.pendingQuestions.filterKeys { it != requestId }
		} else {
			channel.pendingQuestions.filterValues { it.sender != sender }
		}
		val newPendingResponses = (channel.pendingResponses - droppedCount).coerceAtLeast(0)
		_channels.value = _channels.value.toMutableMap().also {
			it[cwdKey] = channel.copy(pendingQuestions = newPending, pendingResponses = newPendingResponses)
		}
	}

	fun requestAwayModeToggle(cwdKey: String?, desired: Boolean) {
		// "On" direction (entering away mode): no bulk-respond modal needed —
		// fire the command immediately and let the Firebase listener carry the
		// committed state back to the UI.
		if (desired) {
			if (cwdKey == null) enterGlobalAway()
			else _channels.value[cwdKey]?.cwd?.let { enterCwdAway(it) }
			return
		}

		// "Off" direction (exiting away mode). Per the schema-reorg spec, the
		// modal lives on the phone. Build it locally from in-memory channel state
		// when there are pending questions in scope; otherwise fire exit straight
		// through. We do NOT optimistically flip _globalAway / _cwdOverrides here:
		// (a) for the no-pending fast path the Firebase listener will reflect the
		// committed state in well under a second, and (b) for the pending path
		// the user may still cancel in the modal, in which case nothing should
		// have changed.
		if (cwdKey == null) {
			val sections = buildBulkRespondSectionsForGlobal()
			if (sections.isEmpty()) {
				exitGlobalAway()
			} else {
				_pendingExitToggle.value = PendingExitToggle(
					scopeCwdKey = null,
					payload = BulkRespondPayload(
						sections = sections,
						defaultText = BULK_RESPOND_DEFAULT_TEXT,
					),
				)
			}
		} else {
			val channel = _channels.value[cwdKey] ?: return
			val cwd = channel.cwd
			val section = buildBulkRespondSectionForChannel(channel)
			if (section == null) {
				exitCwdAway(cwd)
			} else {
				_pendingExitToggle.value = PendingExitToggle(
					scopeCwdKey = cwdKey,
					payload = BulkRespondPayload(
						sections = listOf(section),
						defaultText = BULK_RESPOND_DEFAULT_TEXT,
					),
				)
			}
		}
	}

	fun requestSwipeAtDesk(cwdKey: String) {
		// Channel-row swipe-to-At-desk needs a confirmation gate. If the channel
		// has pending questions, requestAwayModeToggle will surface the bulk-
		// respond modal — that modal is itself the confirmation, so we just
		// invoke the standard path. With no pendings the standard path commits
		// immediately, which is too aggressive for a single accidental swipe;
		// we route through a plain confirm dialog instead.
		val channel = _channels.value[cwdKey] ?: return
		val hasPendings = channel.pendingQuestions.values.any { !it.cancelled }
		if (hasPendings) {
			requestAwayModeToggle(cwdKey, false)
		} else {
			_pendingSwipeAtDeskConfirm.value = cwdKey
		}
	}

	fun confirmSwipeAtDesk() {
		val cwdKey = _pendingSwipeAtDeskConfirm.value ?: return
		_pendingSwipeAtDeskConfirm.value = null
		requestAwayModeToggle(cwdKey, false)
	}

	fun cancelSwipeAtDesk() {
		_pendingSwipeAtDeskConfirm.value = null
	}

	private fun buildBulkRespondSectionForChannel(channel: Channel): BulkRespondSection? {
		// Source pending questions from the channel's locally-tracked
		// pendingQuestions map (populated as messages stream in via addMessage).
		// We bypass channel.pendingResponses here and trust the per-question
		// records — the count and the records can briefly drift mid-flight but
		// the records are the structurally-richer source for the modal's list.
		val entries = channel.pendingQuestions.values
			.filter { !it.cancelled }
			.map { BulkRespondEntry(it.requestId, it.sender, it.questionText) }
		if (entries.isEmpty()) return null
		return BulkRespondSection(cwd = channel.cwd, entries = entries)
	}

	private fun buildBulkRespondSectionsForGlobal(): List<BulkRespondSection> {
		// Aggregate sections across every channel that holds pending questions.
		// We do NOT filter by awayMode here: the server clears all per-channel
		// overrides on a successful global flip, so every pending IS in scope of
		// the transition. The server's _apply_bulk_respond_decision uses
		// registry.all_pending() with no filter; the phone matches that.
		return _channels.value.values
			.mapNotNull { buildBulkRespondSectionForChannel(it) }
	}

	fun hideChannel(cwdKey: String) {
		channelsRef.child(cwdKey).child("hidden").setValue(true)
	}

	fun unhideChannel(cwdKey: String) {
		channelsRef.child(cwdKey).child("hidden").setValue(false)
		_selectedCwdKey.value = cwdKey
	}

	fun resolveSpawnCollision(spawnId: String, action: String) {
		spawnCollisionsRef.child(spawnId).child("decision").setValue(mapOf("action" to action))
	}

	fun submitExitToggleDecision(decision: String, defaultText: String?) {
		val pending = _pendingExitToggle.value ?: return
		_pendingExitToggle.value = null
		if (pending.scopeCwdKey == null) {
			exitGlobalAway(decision = decision, defaultText = defaultText)
		} else {
			val cwd = _channels.value[pending.scopeCwdKey]?.cwd ?: return
			exitCwdAway(cwd, decision = decision, defaultText = defaultText)
		}
	}

	fun cancelExitToggle() {
		_pendingExitToggle.value = null
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

	private fun exitGlobalAway(decision: String? = null, defaultText: String? = null) {
		val payload = mutableMapOf<String, Any>(
			"type" to "exit_global",
			"issued_at" to nowIso(),
		)
		if (decision != null) payload["decision"] = decision
		if (defaultText != null) payload["default_text"] = defaultText
		awayCommandsRef.push().setValue(payload)
	}

	private fun enterCwdAway(cwd: String) {
		awayCommandsRef.push().setValue(mapOf("type" to "enter_cwd", "cwd" to cwd, "issued_at" to nowIso()))
	}

	private fun exitCwdAway(cwd: String, decision: String? = null, defaultText: String? = null) {
		val payload = mutableMapOf<String, Any>(
			"type" to "exit_cwd",
			"cwd" to cwd,
			"issued_at" to nowIso(),
		)
		if (decision != null) payload["decision"] = decision
		if (defaultText != null) payload["default_text"] = defaultText
		awayCommandsRef.push().setValue(payload)
	}

	// --- Utilities ---

	fun saveFileToDownloads(context: Context, url: String, fileName: String) {
		if (!url.startsWith("http://") && !url.startsWith("https://")) {
			Toast.makeText(context, "Invalid URL", Toast.LENGTH_SHORT).show()
			return
		}
		try {
			val request = DownloadManager.Request(Uri.parse(url))
				.setTitle(fileName)
				.setDescription("Downloading file from Switchboard")
				.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
				.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName)
				.setAllowedOverMetered(true)
				.setAllowedOverRoaming(true)

			val downloadManager = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
			downloadManager.enqueue(request)
			Toast.makeText(context, "Download started...", Toast.LENGTH_SHORT).show()
		} catch (e: Exception) {
			Toast.makeText(context, "Failed to start download: ${e.message}", Toast.LENGTH_LONG).show()
		}
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
		val fileName = file.name
		if (fileName.endsWith(".md", ignoreCase = true) || fileName.endsWith(".txt", ignoreCase = true)) {
			try {
				val content = file.readText()
				_markdownViewerContent.value = fileName to content
				return
			} catch (e: Exception) {
				android.util.Log.e("MainViewModel", "Failed to read file for viewer: ${e.message}")
			}
		}

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
