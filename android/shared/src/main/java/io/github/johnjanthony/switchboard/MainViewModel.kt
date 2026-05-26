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
import io.github.johnjanthony.switchboard.network.ConversationMember
import io.github.johnjanthony.switchboard.network.ConversationSummary
import io.github.johnjanthony.switchboard.network.Pending
import io.github.johnjanthony.switchboard.network.PendingExitToggle
import io.github.johnjanthony.switchboard.network.AgentStatus
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

	private val _activeConversations = MutableStateFlow<List<ConversationSummary>>(emptyList())
	val activeConversations: StateFlow<List<ConversationSummary>> = _activeConversations.asStateFlow()

	private val _wslAvailable = MutableStateFlow(true)
	val wslAvailable: StateFlow<Boolean> = _wslAvailable.asStateFlow()

	private val _openConversationId = MutableStateFlow<String?>(null)
	val openConversationId: StateFlow<String?> = _openConversationId.asStateFlow()

	private val _pendingDeepLinkMessageId = MutableStateFlow<String?>(null)
	val pendingDeepLinkMessageId: StateFlow<String?> = _pendingDeepLinkMessageId.asStateFlow()

	// Maps requestId → convId so submitReply can write to the new
	// /conversations/<convId>/answers/<requestId> path.
	private val requestIdToConvId = mutableMapOf<String, String>()

	// Maps msgId → convId so markMessageOpened can write to the correct
	// /conversations/<convId>/messages/<msgId>/opened path.
	// Populated in routeConversationMessage as messages arrive.
	private val msgIdToConvId = mutableMapOf<String, String>()

	// Maps cwdKey → convId so selectChannel can write the unread_count clear
	// to /conversations/<convId>/unread_count instead of the legacy channel path.
	// Populated in startConversationListener as conversations arrive.
	private val cwdKeyToConvId = mutableMapOf<String, String>()

	private val database = FirebaseDatabase.getInstance()
	private val channelsRef = database.getReference("channels")
	private val responsesRef = database.getReference("responses")
	private val awayCommandsRef = database.getReference("away_mode_commands")
	private val globalAwayRef = database.getReference("global_settings/away_mode")
	private val conversationsRef = database.getReference("conversations")
	private val adminNotificationsRef = database.getReference("admin_notifications")

	private val messageListeners = mutableMapOf<String, ChildEventListener>()
	private val conversationMessageListeners = mutableMapOf<String, ChildEventListener>()
	private var adminListener: ChildEventListener? = null

	init {
		channelsRef.keepSynced(true)
		setupChannelsListener()
		setupAwayModeListener()
		startOpenConversationListener()
		startWslAvailableListener()
		startConversationListener()
		startConversationMessageSubscriptions()
		setupAdminNotificationsListener()
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

	fun isAwayActive(cwdKey: String): Boolean = _globalAway.value

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

	private fun setupAdminNotificationsListener() {
		val listener = object : ChildEventListener {
			override fun onChildAdded(snapshot: DataSnapshot, prev: String?) {
				val msgId = snapshot.key ?: return
				val sender = snapshot.child("sender").getValue(String::class.java) ?: "system"
				val text = snapshot.child("text").getValue(String::class.java) ?: ""
				val format = snapshot.child("format").getValue(String::class.java) ?: "plain"
				val timestamp = snapshot.child("timestamp").getValue(String::class.java) ?: ""
				val msg = ChannelMessage(
					sender = sender, type = "notify",
					text = text, format = format, timestamp = timestamp,
				)
				// Surface via existing channel infrastructure: synthesize a synthetic "_admin" channel.
				ensureAdminChannelExists()
				addMessage("_admin", msgId, msg)
			}
			override fun onChildChanged(snapshot: DataSnapshot, prev: String?) {}
			override fun onChildRemoved(snapshot: DataSnapshot) {}
			override fun onChildMoved(snapshot: DataSnapshot, prev: String?) {}
			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "admin_notifications listener cancelled: $error")
			}
		}
		adminNotificationsRef.addChildEventListener(listener)
		adminListener = listener
	}

	/** Ensure the synthetic _admin channel exists in _channels so addMessage can target it. */
	private fun ensureAdminChannelExists() {
		if (!_channels.value.containsKey("_admin")) {
			val newMap = _channels.value.toMutableMap()
			newMap["_admin"] = Channel(cwd = "_admin", cwdKey = "_admin", title = "Admin")
			_channels.value = newMap
		}
	}

	private fun syncChannel(cwdKey: String, snapshot: DataSnapshot) {
		val hidden = snapshot.child("hidden").getValue(Boolean::class.java) == true
		val title = snapshot.child("title").getValue(String::class.java)
		val cwdCanonical = snapshot.child("cwd_canonical").getValue(String::class.java) ?: ""
		val lastActivityAt = snapshot.child("last_activity_at").getValue(String::class.java)
		val preview = snapshot.child("preview").getValue(String::class.java)
		val unreadCount = snapshot.child("unread_count").getValue(Int::class.java) ?: 0
		val pendingResponses = snapshot.child("pending_responses").getValue(Int::class.java) ?: 0
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
			pendingResponses = pendingResponses,
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
			if (_selectedCwdKey.value == cwdKey) {
				_selectedCwdKey.value = newMap.entries.firstOrNull { !it.value.hidden }?.key
			}
		}
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
			// Clear unread badge on the conversation side (server increments
			// /conversations/<convId>/unread_count; we write 0 back there).
			val convId = cwdKeyToConvId[cwdKey]
			if (convId != null) {
				conversationsRef.child(convId).child("unread_count").setValue(0)
			}
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
		// Away mode is global-only. Listen to the global flag.
		globalAwayRef.addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				_globalAway.value = snapshot.getValue(Boolean::class.java) == true
			}
			override fun onCancelled(error: DatabaseError) {}
		})
	}

	private fun startOpenConversationListener() {
		val ref = database.getReference("global_settings/open_conversation_id")
		ref.addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				val newId = snapshot.getValue(String::class.java)
				val changed = _openConversationId.value != newId
				_openConversationId.value = newId
				// Re-emit conversations so isOpenConversation flags are updated
				if (changed) recomputeConversationOpenFlags()
			}
			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "open_conversation_id listener cancelled: $error")
			}
		})
	}

	private fun startWslAvailableListener() {
		val ref = database.getReference("global_settings/wsl_available")
		ref.addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				_wslAvailable.value = snapshot.getValue(Boolean::class.java) ?: true
			}
			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "wsl_available listener cancelled: $error")
			}
		})
	}

	private fun startConversationListener() {
		val ref = database.getReference("conversations")
		ref.addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				val openId = _openConversationId.value
				// Rebuild cwdKeyToConvId from the current snapshot so selectChannel
				// and addMessage can clear unread_count on the conversation path.
				val newCwdKeyToConvId = mutableMapOf<String, String>()
				val summaries = snapshot.children.mapNotNull { convNode ->
					try {
						val convId = convNode.key ?: return@mapNotNull null
						val meta = convNode.child("meta")
						val title = meta.child("title").getValue(String::class.java) ?: convId
						val state = meta.child("state").getValue(String::class.java) ?: "active"
						val lastActivityAt = meta.child("last_activity_at").getValue(Double::class.java)
							?.let { java.time.Instant.ofEpochMilli((it * 1000.0).toLong()).toString() } ?: ""

						if (state != "active") return@mapNotNull null

						val membersNode = convNode.child("members_active")
						val members = membersNode.children.mapNotNull { memberNode ->
							try {
								val memberCwd = memberNode.child("cwd").getValue(String::class.java) ?: ""
								if (memberCwd.isNotBlank()) {
									newCwdKeyToConvId[toFirebaseKey(memberCwd)] = convId
								}
								ConversationMember(
									cliSessionId = memberNode.child("cli_session_id").getValue(String::class.java) ?: return@mapNotNull null,
									sender = memberNode.child("sender").getValue(String::class.java) ?: return@mapNotNull null,
									cwd = memberCwd,
									surface = memberNode.child("surface").getValue(String::class.java) ?: "windows",
									alive = memberNode.child("alive").getValue(Boolean::class.java) ?: true,
									sessionLostPermanently = memberNode.child("session_lost_permanently").getValue(Boolean::class.java) ?: false,
									sessionEndedAt = memberNode.child("session_ended_at").getValue(String::class.java),
									sessionEndReason = memberNode.child("session_end_reason").getValue(String::class.java),
									joinedAt = memberNode.child("joined_at").getValue(Double::class.java) ?: 0.0,
									leftAt = memberNode.child("left_at").getValue(Double::class.java),
									lastSeenSeq = memberNode.child("last_seen_seq").getValue(Int::class.java) ?: 0,
								)
							} catch (e: Exception) { null }
						}

						val agentStatusNode = convNode.child("agent_status")
						val agentStatuses = mutableMapOf<String, AgentStatus>()
						for (asSnap in agentStatusNode.children) {
							val asKey = asSnap.key ?: continue
							val asState = asSnap.child("state").getValue(String::class.java) ?: continue
							val asDetail = asSnap.child("detail").getValue(String::class.java)
							val asUpdated = asSnap.child("updated_at").getValue(Long::class.java) ?: 0L
							if (asUpdated > 0L) {
								agentStatuses[asKey] = AgentStatus(asKey, asState, asDetail, asUpdated)
							}
						}

						ConversationSummary(
							id = convId,
							title = title,
							state = state,
							members = members,
							lastActivityAt = lastActivityAt,
							isOpenConversation = (convId == openId),
							agentStatuses = agentStatuses,
						)
					} catch (e: Exception) { null }
				}
				cwdKeyToConvId.clear()
				cwdKeyToConvId.putAll(newCwdKeyToConvId)
				_activeConversations.value = summaries
			}

			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "conversations listener cancelled: $error")
			}
		})
	}

	/**
	 * Subscribe to /conversations/<id>/messages for each conversation as it appears/disappears.
	 * Messages are routed into the existing addMessage pipeline by looking up the channel whose
	 * cwd matches one of the conversation's active members.
	 */
	private fun startConversationMessageSubscriptions() {
		conversationsRef.addChildEventListener(object : ChildEventListener {
			override fun onChildAdded(snapshot: DataSnapshot, previousChildName: String?) {
				val convId = snapshot.key ?: return
				attachConversationMessageListener(convId)
			}
			override fun onChildChanged(snapshot: DataSnapshot, previousChildName: String?) {}
			override fun onChildRemoved(snapshot: DataSnapshot) {
				val convId = snapshot.key ?: return
				detachConversationMessageListener(convId)
			}
			override fun onChildMoved(snapshot: DataSnapshot, previousChildName: String?) {}
			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "conversations child listener cancelled: $error")
			}
		})
	}

	private fun attachConversationMessageListener(convId: String) {
		if (conversationMessageListeners.containsKey(convId)) return
		val messagesRef = conversationsRef.child(convId).child("messages")
		val listener = object : ChildEventListener {
			override fun onChildAdded(snap: DataSnapshot, prev: String?) {
				val msgId = snap.key ?: return
				routeConversationMessage(convId, msgId, snap)
			}
			override fun onChildChanged(snap: DataSnapshot, prev: String?) {
				val msgId = snap.key ?: return
				routeConversationMessage(convId, msgId, snap)
			}
			override fun onChildRemoved(snap: DataSnapshot) {}
			override fun onChildMoved(snap: DataSnapshot, prev: String?) {}
			override fun onCancelled(error: DatabaseError) {}
		}
		conversationMessageListeners[convId] = listener
		messagesRef.addChildEventListener(listener)
	}

	private fun detachConversationMessageListener(convId: String) {
		val listener = conversationMessageListeners.remove(convId) ?: return
		conversationsRef.child(convId).child("messages").removeEventListener(listener)
	}

	private fun routeConversationMessage(convId: String, msgId: String, snap: DataSnapshot) {
		try {
			val msg = snap.getValue(ChannelMessage::class.java) ?: return
			// Track requestId → convId so submitReply can write to the conversation-scoped answer path.
			if ((msg.type == "question" || msg.type == "ask_human") && msg.request_id != null) {
				requestIdToConvId[msg.request_id!!] = convId
			}
			// Track msgId → convId so markMessageOpened writes to the correct conversation path.
			msgIdToConvId[msgId] = convId
			// Route to the channel whose cwd matches a conversation member, or fall back
			// to the first channel that shares a cwd_key matching the legacy conv_id form.
			val cwdKey = findCwdKeyForConversation(convId) ?: return
			addMessage(cwdKey, msgId, msg)
		} catch (e: Exception) {
			android.util.Log.e("MainViewModel", "MALFORMED MESSAGE at conversations/$convId/messages/$msgId: ${e.message}")
		}
	}

	/**
	 * Find the cwdKey of the channel that corresponds to the given conversation.
	 * We look at each conversation summary's members and match against the channels map by cwd.
	 */
	private fun findCwdKeyForConversation(convId: String): String? {
		val conv = _activeConversations.value.firstOrNull { it.id == convId } ?: return null
		val channels = _channels.value
		for (member in conv.members) {
			val memberCwdKey = toFirebaseKey(member.cwd)
			if (channels.containsKey(memberCwdKey)) return memberCwdKey
		}
		return null
	}

	/** Convert a canonical cwd path to its Firebase-safe key form (inverse of fromFirebaseKey). */
	fun toFirebaseKeyPublic(cwd: String): String = toFirebaseKey(cwd)

	private fun toFirebaseKey(cwd: String): String {
		val sb = StringBuilder()
		for (ch in cwd) {
			when (ch) {
				'/' -> sb.append("__")
				'_' -> sb.append("____")
				else -> sb.append(ch)
			}
		}
		return sb.toString()
	}

	/** Re-emit the current conversations list with refreshed isOpenConversation flags. */
	private fun recomputeConversationOpenFlags() {
		val openId = _openConversationId.value
		_activeConversations.value = _activeConversations.value.map { it.copy(isOpenConversation = (it.id == openId)) }
	}

	// --- Public actions ---

	fun selectChannel(cwdKey: String) {
		_selectedCwdKey.value = cwdKey
		// Clear the server-maintained unread badge for this conversation so the
		// indicator drops on every device subscribed to this Firebase node.
		val convId = cwdKeyToConvId[cwdKey]
		if (convId != null) {
			conversationsRef.child(convId).child("unread_count").setValue(0)
		}
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
		// Look up convId from the msgIdToConvId map populated in routeConversationMessage.
		// This writes to the canonical /conversations/<convId>/messages/<msgId>/opened path.
		val convId = msgIdToConvId[msgId]
		if (convId != null) {
			conversationsRef.child(convId).child("messages").child(msgId).child("opened").setValue(true)
		}
		// If convId is unknown (message arrived before this session, or pre-migration data),
		// we skip the write rather than creating phantom nodes on the wrong path.
	}

	fun submitReply(cwdKey: String, sender: String, text: String, requestId: String?) {
		// Write to the conversation-scoped answer path when we know the convId.
		// Fall back to the legacy /responses path for requests not tracked in requestIdToConvId
		// (e.g. questions that arrived before this session started).
		val convId = if (requestId != null) requestIdToConvId[requestId] else null
		if (requestId != null && convId != null) {
			database.getReference("conversations/$convId/answers/$requestId").setValue(mapOf(
				"text" to text,
				"sender" to sender,
				"request_id" to requestId,
				"written_at" to nowIso(),
			))
		} else {
			val key = requestId ?: "${cwdKey}__$sender"
			responsesRef.child(key).setValue(mapOf(
				"text" to text,
				"cwd_key" to cwdKey,
				"sender" to sender,
				"request_id" to requestId,
				"written_at" to nowIso(),
			))
		}
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
		// Clean up the requestId→convId tracking entry once the reply is submitted.
		if (requestId != null) requestIdToConvId.remove(requestId)
	}

	fun requestAwayModeToggle(cwdKey: String?, desired: Boolean) {
		// Per-cwd away mode was retired. Both global and per-channel toggles now operate
		// on the global flag only. cwdKey parameter is retained for call-site compatibility
		// but is not used for routing.
		// "On" direction: fire the global command immediately.
		if (desired) {
			enterGlobalAway()
			return
		}

		// "Off" direction: build bulk-respond sections across all channels and show
		// the modal if there are pending questions; otherwise fire global exit straight through.
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
		// Write to conversation path; also write the legacy channel path so the
		// channel-driven hidden state stays consistent while Channel.hidden is still
		// the primary UI driver. TODO: retire the channels write once ConversationSummary.hidden feeds the UI.
		val convId = cwdKeyToConvId[cwdKey]
		if (convId != null) {
			conversationsRef.child(convId).child("meta").child("hidden").setValue(true)
		}
		channelsRef.child(cwdKey).child("hidden").setValue(true)
	}

	fun unhideChannel(cwdKey: String) {
		val convId = cwdKeyToConvId[cwdKey]
		if (convId != null) {
			conversationsRef.child(convId).child("meta").child("hidden").setValue(false)
		}
		channelsRef.child(cwdKey).child("hidden").setValue(false)
		_selectedCwdKey.value = cwdKey
	}

	fun submitExitToggleDecision(decision: String, defaultText: String?) {
		val pending = _pendingExitToggle.value ?: return
		_pendingExitToggle.value = null
		// Per-cwd away mode retired — all exits are global.
		exitGlobalAway(decision = decision, defaultText = defaultText)
	}

	fun cancelExitToggle() {
		_pendingExitToggle.value = null
	}

	/**
	 * T-027 conversation-aware spawn. Writes a structured spawn_commands record.
	 * Also auto-enables global away mode if not already on (Task 38).
	 * Returns true if away mode was auto-enabled (caller can show a toast).
	 */
	fun spawnSession(
		surface: String,
		project: String,
		prompt: String,
		targetConversationId: String?,
	): Boolean {
		updateProjectMru(project)
		val wasAwayOff = !_globalAway.value
		if (wasAwayOff) {
			// Task 38: auto-enable away mode on spawn
			enterGlobalAway()
		}
		val record = mutableMapOf<String, Any>(
			"type" to "fresh",
			"surface" to surface,
			"project" to project,
			"issued_at" to nowIso(),
		)
		if (prompt.isNotBlank()) record["prompt"] = prompt
		if (targetConversationId != null) record["target_conversation_id"] = targetConversationId
		database.getReference("spawn_commands").push().setValue(record)
		return wasAwayOff
	}

	// --- Conversation command writers ---

	fun endConversation(conversationId: String) {
		database.getReference("force_end_commands").push().setValue(mapOf(
			"conversation_id" to conversationId,
			"issued_at" to nowIso(),
		))
	}

	fun resumeConversation(sourceConversationId: String, newPrompt: String?) {
		val record = mutableMapOf<String, Any>(
			"type" to "resume",
			"source_conversation_id" to sourceConversationId,
			"issued_at" to nowIso(),
		)
		if (newPrompt != null) record["prompt"] = newPrompt
		database.getReference("spawn_commands").push().setValue(record)
	}

	fun combineConversations(sourceConversationId: String, targetConversationId: String) {
		database.getReference("combine_commands").push().setValue(mapOf(
			"source_conversation_id" to sourceConversationId,
			"target_conversation_id" to targetConversationId,
			"issued_at" to nowIso(),
		))
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
