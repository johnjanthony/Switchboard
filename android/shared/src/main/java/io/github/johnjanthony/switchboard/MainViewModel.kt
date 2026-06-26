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
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.database.ChildEventListener
import com.google.firebase.database.DataSnapshot
import com.google.firebase.database.DatabaseError
import com.google.firebase.database.FirebaseDatabase
import com.google.firebase.database.ValueEventListener
import io.github.johnjanthony.switchboard.network.BulkRespondEntry
import io.github.johnjanthony.switchboard.network.BulkRespondPayload
import io.github.johnjanthony.switchboard.network.BulkRespondSection
import io.github.johnjanthony.switchboard.network.ChannelMessage
import io.github.johnjanthony.switchboard.network.ConversationMember
import io.github.johnjanthony.switchboard.network.ConversationRow
import io.github.johnjanthony.switchboard.network.ConversationSummary
import io.github.johnjanthony.switchboard.network.Pending
import io.github.johnjanthony.switchboard.network.PendingExitToggle
import io.github.johnjanthony.switchboard.network.AgentStatus
import io.github.johnjanthony.switchboard.network.WidgetQuota
import io.github.johnjanthony.switchboard.network.WidgetRing
import io.github.johnjanthony.switchboard.network.WidgetStatus
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

	// Primary phone-side state: conversations keyed by conv_id with per-conversation
	// runtime state (messages, pendings, answered set) folded in.
	private val _conversationRows = MutableStateFlow<Map<String, ConversationRow>>(emptyMap())
	val conversationRows: StateFlow<Map<String, ConversationRow>> = _conversationRows.asStateFlow()

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

	/** Conversations dropped from the list because their node failed to parse (convId to error). */
	private val _conversationParseFailures = MutableStateFlow<Map<String, String>>(emptyMap())
	val conversationParseFailures: StateFlow<Map<String, String>> = _conversationParseFailures.asStateFlow()
	private var lastParseFailureNotice: String? = null

	private fun maybeToastParseFailures(failures: Map<String, String>) {
		val notice = conversationParseFailureNotice(failures)
		if (notice != null && notice != lastParseFailureNotice) {
			Handler(Looper.getMainLooper()).post {
				Toast.makeText(getApplication(), notice, Toast.LENGTH_LONG).show()
			}
		}
		lastParseFailureNotice = notice
	}

	private val _activeConversations = MutableStateFlow<List<ConversationSummary>>(emptyList())
	val activeConversations: StateFlow<List<ConversationSummary>> = _activeConversations.asStateFlow()

	private val _wslAvailable = MutableStateFlow(true)
	val wslAvailable: StateFlow<Boolean> = _wslAvailable.asStateFlow()

	private val _widgetRings = MutableStateFlow<Map<String, WidgetRing>>(emptyMap())
	val widgetRings: StateFlow<Map<String, WidgetRing>> = _widgetRings.asStateFlow()

	private val _widgetQuota = MutableStateFlow<WidgetQuota?>(null)
	val widgetQuota: StateFlow<WidgetQuota?> = _widgetQuota.asStateFlow()

	private val _widgetStatus = MutableStateFlow<WidgetStatus?>(null)
	val widgetStatus: StateFlow<WidgetStatus?> = _widgetStatus.asStateFlow()

	private val _widgetPushedAt = MutableStateFlow<String?>(null)
	val widgetPushedAt: StateFlow<String?> = _widgetPushedAt.asStateFlow()

	private val _openConversationId = MutableStateFlow<String?>(null)
	val openConversationId: StateFlow<String?> = _openConversationId.asStateFlow()

	private val _pendingDeepLinkMessageId = MutableStateFlow<String?>(null)
	val pendingDeepLinkMessageId: StateFlow<String?> = _pendingDeepLinkMessageId.asStateFlow()

	// Maps requestId → convId so submitReplyForConversation can write to the new
	// /conversations/<convId>/answers/<requestId> path. Populated by routeConversationMessage
	// as questions arrive; consumed by submitReplyForConversation when answers go out.
	private val requestIdToConvId = mutableMapOf<String, String>()

	private val database = FirebaseDatabase.getInstance()
	private val awayCommandsRef = database.getReference("away_mode_commands")
	private val globalAwayRef = database.getReference("global_settings/away_mode")
	private val conversationsRef = database.getReference("conversations")
	private val adminNotificationsRef = database.getReference("admin_notifications")

	private val conversationMessageListeners = mutableMapOf<String, ChildEventListener>()
	private var adminListener: ChildEventListener? = null

	init {
		loadProjectMru()
		attachFirebaseListenersWhenAuthed()
	}

	private var firebaseListenersAttached = false

	/**
	 * Attach the Firebase DB listeners only once an authenticated user exists.
	 * Attaching them in init (before the async Google sign-in completes) makes the
	 * unauthenticated listens fail Permission denied under auth-required rules, and
	 * Firebase cancels them with no auto-retry, leaving the UI empty. Uses an
	 * IdTokenListener, NOT an AuthStateListener: on a saved-login restore FirebaseAuth
	 * notifies only its id-token listeners, so an AuthStateListener can stay silent and
	 * the DB listeners would never attach. IdTokenListener fires on sign-in, restore,
	 * and token refresh, always with currentUser available; shouldAttachFirebaseListeners
	 * keeps attachment idempotent across the repeated fires.
	 */
	private fun attachFirebaseListenersWhenAuthed() {
		FirebaseAuth.getInstance().addIdTokenListener(FirebaseAuth.IdTokenListener { auth ->
			if (shouldAttachFirebaseListeners(auth.currentUser != null, firebaseListenersAttached)) {
				firebaseListenersAttached = true
				setupAwayModeListener()
				startOpenConversationListener()
				startWslAvailableListener()
				startConversationListener()
				startConversationMessageSubscriptions()
				setupAdminNotificationsListener()
				startWidgetListeners()
			}
		})
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
				// Surface via ConversationRow: synthesize a synthetic "_admin" row (R3).
				ensureAdminRowExists()
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

	/** Ensure the synthetic _admin ConversationRow exists in _conversationRows (R3). */
	private fun ensureAdminRowExists() {
		if (!_conversationRows.value.containsKey(ADMIN_CONVERSATION_ID)) {
			val newMap = _conversationRows.value.toMutableMap()
			newMap[ADMIN_CONVERSATION_ID] = ConversationRow(
				summary = ConversationSummary(
					id = ADMIN_CONVERSATION_ID,
					title = "Admin",
					state = "active",
					members = emptyList(),
					lastActivityAt = "",
				),
			)
			_conversationRows.value = newMap
		}
	}

	/** Append an admin message to the synthetic _admin ConversationRow. */
	private fun appendAdminMessage(msgId: String, msg: ChannelMessage) {
		val row = _conversationRows.value[ADMIN_CONVERSATION_ID] ?: return
		val rawMessages = row.messages.toMutableList()
		val idx = rawMessages.indexOfFirst { it.first == msgId }
		if (idx >= 0) rawMessages[idx] = msgId to msg else rawMessages.add(msgId to msg)
		val sortedRaw = rawMessages.sortedBy { it.first }
		val displayMessages = applySpliceOrder(sortedRaw)
		val updated = row.copy(messages = displayMessages)
		val newMap = _conversationRows.value.toMutableMap()
		newMap[ADMIN_CONVERSATION_ID] = updated
		_conversationRows.value = newMap
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
		if (_selectedConversationId.value == convId && !isSyntheticConversation(convId)) {
			conversationsRef.child(convId).child("unread_count").setValue(0)
		}

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

	/**
	 * Listen to the widget hub the server fans out from Watchtower: per-session context
	 * rings, plan quota, Anthropic service status, and the push-staleness timestamp. Rings
	 * are a map keyed by Claude Code session_id; a per-child parse failure is logged and
	 * skipped rather than dropping the whole map.
	 */
	private fun startWidgetListeners() {
		database.getReference("widget/rings").addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				val map = mutableMapOf<String, WidgetRing>()
				for (child in snapshot.children) {
					val sessionId = child.key ?: continue
					try {
						child.getValue(WidgetRing::class.java)?.let { map[sessionId] = it }
					} catch (e: Exception) {
						android.util.Log.w("MainViewModel", "widget ring parse failed: $sessionId", e)
					}
				}
				_widgetRings.value = map
			}
			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "widget/rings listener cancelled: $error")
			}
		})
		database.getReference("widget/quota").addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				_widgetQuota.value = try {
					snapshot.getValue(WidgetQuota::class.java)
				} catch (e: Exception) {
					android.util.Log.w("MainViewModel", "widget/quota parse failed", e)
					null
				}
			}
			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "widget/quota listener cancelled: $error")
			}
		})
		database.getReference("widget/status").addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				_widgetStatus.value = try {
					snapshot.getValue(WidgetStatus::class.java)
				} catch (e: Exception) {
					android.util.Log.w("MainViewModel", "widget/status parse failed", e)
					null
				}
			}
			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "widget/status listener cancelled: $error")
			}
		})
		database.getReference("widget/pushed_at").addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				_widgetPushedAt.value = snapshot.getValue(String::class.java)
			}
			override fun onCancelled(error: DatabaseError) {
				android.util.Log.w("MainViewModel", "widget/pushed_at listener cancelled: $error")
			}
		})
	}

	private fun startConversationListener() {
		val ref = database.getReference("conversations")
		ref.addValueEventListener(object : ValueEventListener {
			override fun onDataChange(snapshot: DataSnapshot) {
				val openId = _openConversationId.value
				val failures = mutableMapOf<String, String>()
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
							} catch (e: Exception) {
								android.util.Log.w("MainViewModel", "member parse failed in ${convNode.key}: ${memberNode.key}", e)
								null
							}
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
						val continuedFrom = convNode.child("meta").child("continued_from").getValue(String::class.java)
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
							continuedFrom = continuedFrom,
							agentStatuses = agentStatuses,
						)
					} catch (e: Exception) {
						val id = convNode.key ?: "?"
						android.util.Log.e("MainViewModel", "conversation parse failed: $id", e)
						failures[id] = e.javaClass.simpleName + ": " + (e.message ?: "")
						null
					}
				}
				_conversationParseFailures.value = failures
				maybeToastParseFailures(failures)
				_activeConversations.value = summaries
				mergeSummariesIntoRows(summaries)
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
		// Carry the synthetic _admin row forward: it has no backing ConversationSummary
		// in /conversations, so the incomingIds filter above would otherwise drop it (R3).
		current[ADMIN_CONVERSATION_ID]?.let { next[ADMIN_CONVERSATION_ID] = it }
		// Drop rows for conversations no longer in the active set.
		// (Anything in `current` whose key is NOT in incomingIds is implicitly excluded.)
		_conversationRows.value = next

		// If the previously-selected conv is gone, clear the selection so the
		// phone nav can fall back gracefully.
		val sel = _selectedConversationId.value
		if (sel != null && sel !in incomingIds) {
			_selectedConversationId.value = next.values.firstOrNull { !it.hidden && it.state == "active" }?.id
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
			// Track requestId → convId so submitReplyForConversation can write to the conversation-scoped
			// answer path even after the row's pendingQuestions entry is gone.
			if ((msg.type == "question" || msg.type == "ask_human") && msg.request_id != null) {
				requestIdToConvId[msg.request_id!!] = convId
			}
			addMessageToConversation(convId, msgId, msg)
		} catch (e: Exception) {
			android.util.Log.e("MainViewModel", "MALFORMED MESSAGE at conversations/$convId/messages/$msgId: ${e.message}")
		}
	}

	/** Re-emit the current conversations list with refreshed isOpenConversation flags. */
	private fun recomputeConversationOpenFlags() {
		val openId = _openConversationId.value
		val updated = _activeConversations.value.map { it.copy(isOpenConversation = (it.id == openId)) }
		_activeConversations.value = updated
		mergeSummariesIntoRows(updated)
	}

	// --- Public actions ---

	/** Phone-side: select a conversation by convId. */
	fun selectConversation(convId: String) {
		_selectedConversationId.value = convId
		// The synthetic _admin row has no Firebase node; never write under conversations/_admin (R3).
		if (isSyntheticConversation(convId)) return
		// Clear the server-maintained unread badge so the indicator drops on every device.
		conversationsRef.child(convId).child("unread_count").setValue(0)
	}

	fun clearSelectedChannel() {
		_selectedConversationId.value = null
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
		if (isSyntheticConversation(convId)) return  // no Firebase node for _admin (R3)
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
		// Group by conversation; label by the conversation title, falling back to the
		// member roster when the title is blank (R4). Client-only; the server returns a
		// flat pending list and does not build sections.
		val entries = row.pendingQuestions.values
			.filter { !it.cancelled }
			.map { BulkRespondEntry(it.requestId, it.sender, it.questionText) }
		if (entries.isEmpty()) return null
		return BulkRespondSection(label = bulkRespondSectionLabel(row.title, row.memberRoster), entries = entries)
	}

	private fun buildBulkRespondSectionsForGlobal(): List<BulkRespondSection> {
		// Aggregate sections across every conversation row that holds pending questions.
		// We do NOT filter by awayMode here: the server clears all per-channel overrides
		// on a successful global flip, so every pending IS in scope of the transition.
		return _conversationRows.value.values
			.mapNotNull { buildBulkRespondSectionForRow(it) }
	}

	/** Phone-side hide by convId. */
	fun hideConversation(convId: String) {
		if (isSyntheticConversation(convId)) return  // _admin is not hideable; no Firebase node (R3)
		conversationsRef.child(convId).child("meta").child("hidden").setValue(true)
	}

	/** Phone-side unhide by convId. */
	fun unhideConversation(convId: String) {
		conversationsRef.child(convId).child("meta").child("hidden").setValue(false)
		_selectedConversationId.value = convId
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

	// --- Claude-status request (phone -> server command queue) ---

	/**
	 * Trigger a fresh Anthropic status check. Pushes a command the server's status_request
	 * dispatcher (Plan 2a) routes into ClaudeStatusService.check(); the server publishes the
	 * result to widget/status, which this view-model reads back. Mirrors the away_mode_commands
	 * push pattern. This is the phone's trigger path - NOT an HTTP call.
	 */
	fun requestClaudeStatusCheck() {
		database.getReference("widget/status_request").push().setValue(
			mapOf("type" to "check", "issued_at" to nowIso())
		)
	}

	/** Stop the server's status watch loop (acknowledge), via the same command queue. */
	fun stopClaudeStatusWatch() {
		database.getReference("widget/status_request").push().setValue(
			mapOf("type" to "stop", "issued_at" to nowIso())
		)
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
}
