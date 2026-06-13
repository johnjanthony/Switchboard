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
import io.github.johnjanthony.switchboard.network.ConversationRow
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
		// Synthetic "_admin" cwdKey used for the admin-notifications pseudo-channel.
		// Lives outside the conversations model — admin notifications are system-wide
		// broadcasts, not bound to any conversation.
		private const val ADMIN_CWD_KEY = "_admin"
	}

	// Primary phone-side state: conversations keyed by conv_id with per-conversation
	// runtime state (messages, pendings, answered set) folded in.
	private val _conversationRows = MutableStateFlow<Map<String, ConversationRow>>(emptyMap())
	val conversationRows: StateFlow<Map<String, ConversationRow>> = _conversationRows.asStateFlow()

	// Wear-compat projection. Derived from _conversationRows whenever it changes,
	// plus the synthetic _admin channel for admin notifications. TODO Dispatch C:
	// retire once Wear migrates to the conversation model.
	private val _channels = MutableStateFlow<Map<String, Channel>>(emptyMap())
	val channels: StateFlow<Map<String, Channel>> = _channels.asStateFlow()

	private val _projectMru = MutableStateFlow<List<String>>(emptyList())
	val projectMru: StateFlow<List<String>> = _projectMru.asStateFlow()

	private val _globalAway = MutableStateFlow(false)
	val globalAway: StateFlow<Boolean> = _globalAway.asStateFlow()

	private val _pendingExitToggle = MutableStateFlow<PendingExitToggle?>(null)
	val pendingExitToggle: StateFlow<PendingExitToggle?> = _pendingExitToggle.asStateFlow()

	private val _markdownViewerContent = MutableStateFlow<Pair<String, String>?>(null) // fileName to content
	val markdownViewerContent: StateFlow<Pair<String, String>?> = _markdownViewerContent.asStateFlow()

	private val _selectedConversationId = MutableStateFlow<String?>(null)
	val selectedConversationId: StateFlow<String?> = _selectedConversationId.asStateFlow()

	/**
	 * Wear-only fallback: when nothing is selected, auto-select the
	 * conversation of the first arriving message. The phone must leave this
	 * false: selection is navigation-driven there, and force-selecting on
	 * message arrival permanently zeroed the most active conversation's
	 * unread badge while John sat on Page A (H07). Wear opts in at startup.
	 */
	var autoSelectOnMessageArrival: Boolean = false

	// Wear-compat: legacy state flow exposing the selected key in cwdKey form.
	// Backed by the same `_selectedConversationId` value; the Wear app reads the cwdKey
	// of the conversation's first alive member.
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
	// /conversations/<convId>/answers/<requestId> path. Populated by routeConversationMessage
	// as questions arrive; consumed by submitReply when answers go out.
	private val requestIdToConvId = mutableMapOf<String, String>()

	private val database = FirebaseDatabase.getInstance()
	private val channelsRef = database.getReference("channels")
	private val responsesRef = database.getReference("responses")
	private val awayCommandsRef = database.getReference("away_mode_commands")
	private val globalAwayRef = database.getReference("global_settings/away_mode")
	private val conversationsRef = database.getReference("conversations")
	private val adminNotificationsRef = database.getReference("admin_notifications")

	// Legacy /channels listener — retained ONLY to surface the synthetic _admin channel
	// (admin_notifications listener feeds it). The phone Page A no longer reads from
	// `_channels` for real conversations; Wear still does via the derived projection.
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
		// Listener retained for the legacy channel data the Wear app reads. Phone Page A
		// no longer drives off this — `_conversationRows` is the primary source, and the
		// Wear-compat projection re-derives a fresh `_channels` map from it. The only data
		// we still need from /channels/<key> on the Wear path is the cwd_canonical / title
		// for routes the conversation projection synthesizes. Once Wear migrates, this
		// listener disappears entirely (Dispatch C).
		channelsRef.addChildEventListener(object : ChildEventListener {
			override fun onChildAdded(snapshot: DataSnapshot, previousChildName: String?) {
				val cwdKey = snapshot.key ?: return
				syncLegacyChannel(cwdKey, snapshot)
			}
			override fun onChildChanged(snapshot: DataSnapshot, previousChildName: String?) {
				val cwdKey = snapshot.key ?: return
				syncLegacyChannel(cwdKey, snapshot)
			}
			override fun onChildRemoved(snapshot: DataSnapshot) {
				val cwdKey = snapshot.key ?: return
				removeLegacyChannel(cwdKey)
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
				appendAdminMessage(msgId, msg)
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

	/** Ensure the synthetic _admin channel exists in _channels (Wear-compat surface). */
	private fun ensureAdminChannelExists() {
		if (!_channels.value.containsKey(ADMIN_CWD_KEY)) {
			val newMap = _channels.value.toMutableMap()
			newMap[ADMIN_CWD_KEY] = Channel(cwd = ADMIN_CWD_KEY, cwdKey = ADMIN_CWD_KEY, title = "Admin")
			_channels.value = newMap
		}
	}

	/** Append an admin message to the synthetic _admin channel (Wear-compat). */
	private fun appendAdminMessage(msgId: String, msg: ChannelMessage) {
		val channel = _channels.value[ADMIN_CWD_KEY] ?: return
		val rawMessages = channel.messages.toMutableList()
		val idx = rawMessages.indexOfFirst { it.first == msgId }
		if (idx >= 0) rawMessages[idx] = msgId to msg else rawMessages.add(msgId to msg)
		val sortedRaw = rawMessages.sortedBy { it.first }
		val displayMessages = applySpliceOrder(sortedRaw)
		val updated = channel.copy(messages = displayMessages)
		val newMap = _channels.value.toMutableMap()
		newMap[ADMIN_CWD_KEY] = updated
		_channels.value = newMap
	}

	/**
	 * Pull the per-cwd channel record into the Wear-compat `_channels` map. We extract
	 * only the legacy fields Wear still needs (title, cwd_canonical, hidden, last_activity,
	 * preview, unread_count, pending_responses); messages/pendings ride on conversationRows
	 * and are merged in via [refreshChannelsProjection].
	 */
	private fun syncLegacyChannel(cwdKey: String, snapshot: DataSnapshot) {
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
	}

	private fun removeLegacyChannel(cwdKey: String) {
		val newMap = _channels.value.toMutableMap()
		if (newMap.remove(cwdKey) != null) {
			_channels.value = newMap
		}
	}

	private fun isQuestionType(type: String): Boolean {
		return type == "question" || type == "ask_human"
	}

	/**
	 * Merge a freshly-arrived message into the per-conversation row's runtime state,
	 * deriving displayMessages, answeredSet, and pendingQuestions. Replaces the
	 * legacy addMessage(cwdKey, ...) routing.
	 */
	private fun addMessageToConversation(convId: String, msgId: String, msg: ChannelMessage) {
		val row = _conversationRows.value[convId] ?: return

		// Maintain raw arrival-order list. Firebase push keys are time-ordered, so
		// sortedBy { it.first } gives deterministic arrival order regardless of in-list
		// splice state from prior calls.
		val rawMessages = row.messages.toMutableList()
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

		// pendingQuestions: a question is "no longer pending" when it's cancelled, rejected,
		// OR has a reply attached.
		var newPending = row.pendingQuestions.toMutableMap()
		if (isQuestionType(msg.type) && msg.request_id != null) {
			val isAnsweredViaSplice = msgId in answeredSet
			if (msg.cancelled || msg.rejected || isAnsweredViaSplice) {
				if (newPending.containsKey(msg.request_id)) {
					newPending.remove(msg.request_id!!)
				}
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
		// Also: when the new message itself is a reply (has attached_to_msg_id), the
		// question it points at must drop out of pending.
		msg.attached_to_msg_id?.let { targetMsgId ->
			val targetQuestion = sortedRaw.firstOrNull { it.first == targetMsgId }?.second
			val targetRequestId = targetQuestion?.request_id
			if (targetRequestId != null && newPending.containsKey(targetRequestId)) {
				newPending.remove(targetRequestId)
			}
		}

		val updated = row.copy(
			messages = displayMessages,
			pendingQuestions = newPending,
			answeredQuestionMsgIds = answeredSet,
		)
		val newMap = _conversationRows.value.toMutableMap()
		newMap[convId] = updated
		_conversationRows.value = newMap

		// Clear unread badge on the server-side counter when this is the open row.
		if (_selectedConversationId.value == convId) {
			conversationsRef.child(convId).child("unread_count").setValue(0)
		} else if (shouldAutoSelectOnMessageArrival(
				autoSelectOnMessageArrival, _selectedConversationId.value, row.hidden, row.state)) {
			_selectedConversationId.value = convId
			refreshSelectedCwdKey()
		}

		refreshChannelsProjection()

		if (msg.rejected) {
			Handler(Looper.getMainLooper()).post {
				Toast.makeText(getApplication(), msg.text, Toast.LENGTH_LONG).show()
			}
		}
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
				val summaries = snapshot.children.mapNotNull { convNode ->
					try {
						val convId = convNode.key ?: return@mapNotNull null
						val meta = convNode.child("meta")
						val title = meta.child("title").getValue(String::class.java) ?: convId
						val state = meta.child("state").getValue(String::class.java) ?: "active"
						val lastActivityAt = meta.child("last_activity_at").getValue(Double::class.java)
							?.let { java.time.Instant.ofEpochMilli((it * 1000.0).toLong()).toString() } ?: ""

						// Ended conversations stay visible in the list so users can review the
						// history and hide them manually when no longer wanted. SessionRowComposable
						// gates state-mutating actions (Combine, End) on isActive=state=="active".

						val membersNode = convNode.child("members_active")
						val members = membersNode.children.mapNotNull { memberNode ->
							try {
								ConversationMember(
									cliSessionId = memberNode.child("cli_session_id").getValue(String::class.java) ?: return@mapNotNull null,
									sender = memberNode.child("sender").getValue(String::class.java) ?: return@mapNotNull null,
									cwd = memberNode.child("cwd").getValue(String::class.java) ?: "",
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

						val pendingResponses = convNode.child("pending_responses").getValue(Int::class.java) ?: 0
						val preview = convNode.child("meta").child("preview").getValue(String::class.java)
						val hidden = convNode.child("meta").child("hidden").getValue(Boolean::class.java) ?: false
						val unreadCount = convNode.child("unread_count").getValue(Int::class.java) ?: 0

						ConversationSummary(
							id = convId,
							title = title,
							state = state,
							members = members,
							lastActivityAt = lastActivityAt,
							isOpenConversation = (convId == openId),
							hidden = hidden,
							unreadCount = unreadCount,
							pendingResponses = pendingResponses,
							preview = preview,
							agentStatuses = agentStatuses,
						)
					} catch (e: Exception) { null }
				}
				_activeConversations.value = summaries
				mergeSummariesIntoRows(summaries)
				refreshChannelsProjection()
			}

			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "conversations listener cancelled: $error")
			}
		})
	}

	/**
	 * Fold the latest list of active ConversationSummary objects into _conversationRows,
	 * preserving any existing per-conv runtime state. Conversations missing from the new
	 * summary list are dropped from _conversationRows.
	 */
	private fun mergeSummariesIntoRows(summaries: List<ConversationSummary>) {
		val current = _conversationRows.value
		val next = mutableMapOf<String, ConversationRow>()
		val incomingIds = summaries.map { it.id }.toSet()
		for (s in summaries) {
			val existing = current[s.id]
			if (existing == null) {
				next[s.id] = ConversationRow(summary = s)
			} else {
				next[s.id] = existing.copy(summary = s)
			}
		}
		// Drop rows for conversations no longer in the active set.
		// (Anything in `current` whose key is NOT in incomingIds is implicitly excluded.)
		_conversationRows.value = next

		// If the previously-selected conv is gone, pick a fresh visible one for any UI that
		// still cares about a default selection. We don't auto-select for phone UX
		// (navigation drives selection), but Wear's selectedCwdKey flow needs a non-null
		// fallback to avoid getting stuck. Skip hidden AND ended convs — auto-selecting an
		// ended conv would be confusing since it's read-only history.
		val sel = _selectedConversationId.value
		if (sel != null && sel !in incomingIds) {
			_selectedConversationId.value = next.values.firstOrNull { !it.hidden && it.state == "active" }?.id
			refreshSelectedCwdKey()
		}
	}

	/**
	 * Subscribe to /conversations/<id>/messages for each conversation as it appears/disappears.
	 * Messages route directly into the per-conversation row via addMessageToConversation.
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
			// Track requestId → convId so submitReply can write to the conversation-scoped
			// answer path even after the row's pendingQuestions entry is gone.
			if ((msg.type == "question" || msg.type == "ask_human") && msg.request_id != null) {
				requestIdToConvId[msg.request_id!!] = convId
			}
			addMessageToConversation(convId, msgId, msg)
		} catch (e: Exception) {
			android.util.Log.e("MainViewModel", "MALFORMED MESSAGE at conversations/$convId/messages/$msgId: ${e.message}")
		}
	}

	/**
	 * Rebuild the Wear-compat `_channels` map from the current `_conversationRows`.
	 * Each conversation maps to one synthetic Channel keyed by the first alive member's
	 * cwdKey, populated with the row's preview/title/unread/pending counts and message list.
	 *
	 * The `_admin` channel and any legacy /channels entries already populated by
	 * syncLegacyChannel survive: we only touch entries that resolve to a real conv.
	 */
	private fun refreshChannelsProjection() {
		val rows = _conversationRows.value
		val current = _channels.value.toMutableMap()

		// Track cwdKeys derived from current conversations so we can prune stale projections.
		val cwdKeysFromConvs = mutableSetOf<String>()

		for (row in rows.values) {
			// Pick the first alive member's cwd as the legacy display anchor.
			val firstAlive = row.members.firstOrNull { it.alive && it.cwd.isNotBlank() }
				?: row.members.firstOrNull { it.cwd.isNotBlank() }
				?: continue
			val memberCwd = firstAlive.cwd
			val cwdKey = toFirebaseKey(memberCwd)
			cwdKeysFromConvs += cwdKey

			val existing = current[cwdKey]
			val projected = (existing ?: Channel(cwd = memberCwd, cwdKey = cwdKey)).copy(
				cwd = memberCwd,
				cwdKey = cwdKey,
				title = row.title.takeIf { it.isNotBlank() } ?: existing?.title,
				cwdCanonical = if (existing?.cwdCanonical.isNullOrBlank()) memberCwd else existing!!.cwdCanonical,
				hidden = row.hidden,
				lastActivityAt = row.lastActivityAt.takeIf { it.isNotBlank() } ?: existing?.lastActivityAt,
				preview = row.preview ?: existing?.preview,
				unreadCount = row.summary.unreadCount,
				pendingResponses = row.summary.pendingResponses,
				pendingQuestions = row.pendingQuestions,
				messages = row.messages,
				answeredQuestionMsgIds = row.answeredQuestionMsgIds,
				agentStatus = row.agentStatus,
			)
			current[cwdKey] = projected
		}

		_channels.value = current
	}

	/** Re-emit the current conversations list with refreshed isOpenConversation flags. */
	private fun recomputeConversationOpenFlags() {
		val openId = _openConversationId.value
		val updated = _activeConversations.value.map { it.copy(isOpenConversation = (it.id == openId)) }
		_activeConversations.value = updated
		mergeSummariesIntoRows(updated)
		refreshChannelsProjection()
	}

	/** Sync selectedCwdKey from the currently-selected conv (Wear-compat flow). */
	private fun refreshSelectedCwdKey() {
		val convId = _selectedConversationId.value
		if (convId == null) {
			_selectedCwdKey.value = null
			return
		}
		val row = _conversationRows.value[convId]
		val firstAliveCwd = row?.members?.firstOrNull { it.alive && it.cwd.isNotBlank() }?.cwd
			?: row?.members?.firstOrNull { it.cwd.isNotBlank() }?.cwd
		_selectedCwdKey.value = firstAliveCwd?.let { toFirebaseKey(it) }
	}

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

	// --- Public actions ---

	/** Phone-side: select a conversation by convId. */
	fun selectConversation(convId: String) {
		_selectedConversationId.value = convId
		refreshSelectedCwdKey()
		// Clear the server-maintained unread badge so the indicator drops on every device.
		conversationsRef.child(convId).child("unread_count").setValue(0)
	}

	/**
	 * Wear-compat: select by cwdKey. Maps to the conversation whose first alive member's
	 * cwdKey matches. Falls back to no-op when the key is the synthetic `_admin` or has no
	 * corresponding conversation yet (cold-start race).
	 */
	fun selectChannel(cwdKey: String) {
		val convId = findConvIdForCwdKey(cwdKey)
		if (convId != null) {
			selectConversation(convId)
		} else {
			// Could be the _admin synthetic channel; just track the cwdKey for Wear.
			_selectedCwdKey.value = cwdKey
		}
	}

	fun clearSelectedChannel() {
		_selectedConversationId.value = null
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

	/** Phone-side: mark a message opened given the convId (no bridge lookup needed). */
	fun markMessageOpened(convId: String, msgId: String) {
		conversationsRef.child(convId).child("messages").child(msgId).child("opened").setValue(true)
	}

	/**
	 * Phone-side: submit a reply for a specific conversation. `requestId` must be non-null —
	 * the phone UI's ReplyInputBar only opens when an active pending question is selected,
	 * which always carries a request_id. A null requestId here would fall through to a
	 * /responses slot the server can't recover (slot parser expects `cwd_key`, not the
	 * conversation_id we'd write); we don't enter that path at all.
	 */
	fun submitReplyForConversation(convId: String, sender: String, text: String, requestId: String) {
		database.getReference("conversations/$convId/answers/$requestId").setValue(mapOf(
			"text" to text,
			"sender" to sender,
			"request_id" to requestId,
			"written_at" to nowIso(),
		))
		// Optimistically remove from pending so the row indicator clears immediately.
		val row = _conversationRows.value[convId] ?: return
		val newPending = row.pendingQuestions.filterKeys { it != requestId }
		val newMap = _conversationRows.value.toMutableMap()
		newMap[convId] = row.copy(pendingQuestions = newPending)
		_conversationRows.value = newMap
		requestIdToConvId.remove(requestId)
		refreshChannelsProjection()
	}

	/**
	 * Wear-compat: submit reply by cwdKey. Resolves the convId from the rows projection,
	 * falling back to the legacy /responses path when no conv is found (e.g. requests
	 * pre-dating this session, or _admin pseudo-channel).
	 */
	fun submitReply(cwdKey: String, sender: String, text: String, requestId: String?) {
		val viaRequest = if (requestId != null) requestIdToConvId[requestId] else null
		val convId = viaRequest ?: findConvIdForCwdKey(cwdKey)
		if (convId != null && requestId != null) {
			submitReplyForConversation(convId, sender, text, requestId)
		} else {
			// Pre-migration / admin / null-requestId fallback: write to legacy /responses path
			// with cwd_key so the server's slot parser can still route the answer.
			val key = requestId ?: "${cwdKey}__$sender"
			responsesRef.child(key).setValue(mapOf(
				"text" to text,
				"cwd_key" to cwdKey,
				"sender" to sender,
				"request_id" to requestId,
				"written_at" to nowIso(),
			))
		}
	}

	/** Return the convId whose first alive member's cwdKey matches, else null. */
	private fun findConvIdForCwdKey(cwdKey: String): String? {
		val rows = _conversationRows.value
		for ((convId, row) in rows) {
			val firstAlive = row.members.firstOrNull { it.alive && it.cwd.isNotBlank() }
				?: row.members.firstOrNull { it.cwd.isNotBlank() }
				?: continue
			if (toFirebaseKey(firstAlive.cwd) == cwdKey) return convId
		}
		return null
	}

	fun requestAwayModeToggle(cwdKey: String?, desired: Boolean) {
		// Per-cwd away mode was retired. Both global and per-channel toggles operate
		// on the global flag only. cwdKey parameter is retained for call-site compatibility
		// but is not used for routing.
		if (desired) {
			enterGlobalAway()
			return
		}
		val sections = buildBulkRespondSectionsForGlobal()
		if (sections.isEmpty()) {
			exitGlobalAway()
		} else {
			_pendingExitToggle.value = PendingExitToggle(
				payload = BulkRespondPayload(
					sections = sections,
					defaultText = BULK_RESPOND_DEFAULT_TEXT,
				),
			)
		}
	}

	private fun buildBulkRespondSectionForRow(row: ConversationRow): BulkRespondSection? {
		// Use the conversation row's pendingQuestions map (populated by addMessageToConversation
		// as messages stream in). cwd label is the first alive member's cwd, falling back to
		// the row's title for display when no cwd is set.
		val entries = row.pendingQuestions.values
			.filter { !it.cancelled }
			.map { BulkRespondEntry(it.requestId, it.sender, it.questionText) }
		if (entries.isEmpty()) return null
		val cwdLabel = row.members.firstOrNull { it.alive && it.cwd.isNotBlank() }?.cwd
			?: row.members.firstOrNull { it.cwd.isNotBlank() }?.cwd
			?: row.title
		return BulkRespondSection(cwd = cwdLabel, entries = entries)
	}

	private fun buildBulkRespondSectionsForGlobal(): List<BulkRespondSection> {
		// Aggregate sections across every conversation row that holds pending questions.
		// We do NOT filter by awayMode here: the server clears all per-channel overrides
		// on a successful global flip, so every pending IS in scope of the transition.
		return _conversationRows.value.values
			.mapNotNull { buildBulkRespondSectionForRow(it) }
	}

	/** Phone-side hide by convId. Dual-writes the legacy channels path for Wear-compat. */
	fun hideConversation(convId: String) {
		conversationsRef.child(convId).child("meta").child("hidden").setValue(true)
		// Wear-compat: legacy /channels/<key>/hidden also needs to flip so the Wear UI's
		// channels-list filter reflects the change. TODO Dispatch C: remove once Wear migrates.
		legacyCwdKeyForConv(convId)?.let { cwdKey ->
			channelsRef.child(cwdKey).child("hidden").setValue(true)
		}
	}

	/** Phone-side unhide by convId. */
	fun unhideConversation(convId: String) {
		conversationsRef.child(convId).child("meta").child("hidden").setValue(false)
		legacyCwdKeyForConv(convId)?.let { cwdKey ->
			channelsRef.child(cwdKey).child("hidden").setValue(false)
		}
		_selectedConversationId.value = convId
		refreshSelectedCwdKey()
	}

	/** First alive member's cwdKey for a conv, or null if none resolvable (Wear-compat dual-write). */
	private fun legacyCwdKeyForConv(convId: String): String? {
		val row = _conversationRows.value[convId] ?: return null
		val firstAlive = row.members.firstOrNull { it.alive && it.cwd.isNotBlank() }
			?: row.members.firstOrNull { it.cwd.isNotBlank() }
			?: return null
		return toFirebaseKey(firstAlive.cwd)
	}

	fun submitExitToggleDecision(decision: String, defaultText: String?) {
		val pending = _pendingExitToggle.value ?: return
		_pendingExitToggle.value = null
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
