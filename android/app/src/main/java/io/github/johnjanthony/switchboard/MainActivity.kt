package io.github.johnjanthony.switchboard

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DarkMode
import androidx.compose.material3.Icon
import androidx.compose.ui.Alignment
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.MutableState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.google.firebase.auth.FirebaseAuth
import io.github.johnjanthony.switchboard.fcm.BaseSwitchboardMessagingService
import io.github.johnjanthony.switchboard.pickerTargets
import io.github.johnjanthony.switchboard.shared.GoogleAuthHelper
import android.widget.Toast
import io.github.johnjanthony.switchboard.ui.BulkRespondDialog
import io.github.johnjanthony.switchboard.ui.CombineDialog
import io.github.johnjanthony.switchboard.ui.MarkdownViewerScreen
import io.github.johnjanthony.switchboard.ui.ConversationListScreen
import io.github.johnjanthony.switchboard.ui.ConversationViewScreen
import io.github.johnjanthony.switchboard.ui.ResumeSessionSheet
import io.github.johnjanthony.switchboard.ui.SessionDetailSheet
import io.github.johnjanthony.switchboard.ui.SessionsBoardScreen
import io.github.johnjanthony.switchboard.ui.SpawnResumeDialog
import io.github.johnjanthony.switchboard.ui.SpawnSessionDialog
import io.github.johnjanthony.switchboard.ui.TabInfoPopover
import io.github.johnjanthony.switchboard.ui.leafName
import io.github.johnjanthony.switchboard.ui.theme.SwitchboardTheme

class MainActivity : ComponentActivity() {
	private val viewModel: MainViewModel by viewModels()
	// Holds the conv_id from an FCM deep link until the UI can resolve and navigate.
	private val pendingDeepLinkConvId = mutableStateOf<String?>(null)
	private val pendingDeepLinkMessageId = mutableStateOf<String?>(null)

	private val requestPermissionLauncher = registerForActivityResult(
		ActivityResultContracts.RequestPermission()
	) { /* ignored */ }

	override fun onCreate(savedInstanceState: Bundle?) {
		super.onCreate(savedInstanceState)
		requestNotificationPermission()
		pendingDeepLinkConvId.value = intent.getStringExtra(BaseSwitchboardMessagingService.EXTRA_AGENT_ID)
		pendingDeepLinkMessageId.value = intent.getStringExtra(BaseSwitchboardMessagingService.EXTRA_MESSAGE_ID)
		// Scrub the consumed extras so activity recreation (rotation) does not
		// re-read them from the retained Intent and yank navigation back.
		intent.removeExtra(BaseSwitchboardMessagingService.EXTRA_AGENT_ID)
		intent.removeExtra(BaseSwitchboardMessagingService.EXTRA_MESSAGE_ID)
		setContent {
			SwitchboardTheme {
				SwitchboardNavHost(viewModel, pendingDeepLinkConvId, pendingDeepLinkMessageId)
			}
		}
	}

	override fun onNewIntent(intent: Intent) {
		super.onNewIntent(intent)
		setIntent(intent)
		val convId = intent.getStringExtra(BaseSwitchboardMessagingService.EXTRA_AGENT_ID)
		val messageId = intent.getStringExtra(BaseSwitchboardMessagingService.EXTRA_MESSAGE_ID)
		if (convId != null) pendingDeepLinkConvId.value = convId
		if (messageId != null) pendingDeepLinkMessageId.value = messageId
		intent.removeExtra(BaseSwitchboardMessagingService.EXTRA_AGENT_ID)
		intent.removeExtra(BaseSwitchboardMessagingService.EXTRA_MESSAGE_ID)
	}

	private fun requestNotificationPermission() {
		if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
			if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
				!= PackageManager.PERMISSION_GRANTED
			) {
				requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
			}
		}
	}
}

@Composable
private fun SwitchboardNavHost(
	viewModel: MainViewModel,
	deepLinkConvId: MutableState<String?>,
	deepLinkMessageId: MutableState<String?>,
) {
	val context = androidx.compose.ui.platform.LocalContext.current
	val navController = rememberNavController()
	val conversationRows by viewModel.conversationRows.collectAsState()
	val globalAway by viewModel.globalAway.collectAsState()
	val pendingExitToggle by viewModel.pendingExitToggle.collectAsState()
	val markdownViewerContent by viewModel.markdownViewerContent.collectAsState()
	val projectMru by viewModel.projectMru.collectAsState()
	val activeConversations by viewModel.activeConversations.collectAsState()
	val wslAvailable by viewModel.wslAvailable.collectAsState()
	val widgetRings by viewModel.widgetRings.collectAsState()
	val widgetQuota by viewModel.widgetQuota.collectAsState()
	val widgetStatus by viewModel.widgetStatus.collectAsState()
	val widgetPushedAt by viewModel.widgetPushedAt.collectAsState()
	val registrySessions by viewModel.registrySessions.collectAsState()
	val sessionAcks by viewModel.sessionAcks.collectAsState()
	var showHidden by remember { mutableStateOf(false) }
	var showSpawnDialog by remember { mutableStateOf(false) }
	// T-027 dialogs
	var resumeConversationId by remember { mutableStateOf<String?>(null) }
	var combineConversationId by remember { mutableStateOf<String?>(null) }
	var detailSession by remember {
		mutableStateOf<io.github.johnjanthony.switchboard.network.RegistrySession?>(null)
	}
	// Task 11: the resume-target sheet, shared by the board's long-press menu and the
	// detail sheet's Resume button.
	var resumeSheetSession by remember {
		mutableStateOf<io.github.johnjanthony.switchboard.network.RegistrySession?>(null)
	}

	// Automatic Google Sign-In on first start, with a retryable failure state (REV-206).
	var authState by remember {
		mutableStateOf(
			if (FirebaseAuth.getInstance().currentUser != null) AuthUiState.SIGNED_IN
			else AuthUiState.IN_PROGRESS
		)
	}
	var retryTick by remember { mutableStateOf(0) }
	LaunchedEffect(retryTick) {
		if (FirebaseAuth.getInstance().currentUser != null) {
			authState = AuthUiState.SIGNED_IN
			return@LaunchedEffect
		}
		authState = AuthUiState.IN_PROGRESS
		val ok = GoogleAuthHelper.signInWithGoogle(context)
		authState = if (ok) AuthUiState.SIGNED_IN else AuthUiState.FAILED
	}

	// FCM deep-link: server emits conv_id (Fix #9). Navigate directly to session/<convId>
	// once that conversation appears in conversationRows.
	val deepConvId by deepLinkConvId
	LaunchedEffect(deepConvId, conversationRows) {
		val convId = deepConvId ?: return@LaunchedEffect
		if (conversationRows.containsKey(convId)) {
			navController.navigate("session/$convId") { launchSingleTop = true }
			deepLinkConvId.value = null
		}
		// If the conversation is not yet hydrated (cold-start race), leave deepLinkConvId in
		// place so the next recomposition retries when conversationRows populates.
	}

	LaunchedEffect(markdownViewerContent) {
		if (markdownViewerContent != null) {
			navController.navigate("markdown_viewer") { launchSingleTop = true }
		}
	}

	NavHost(navController = navController, startDestination = "list") {
		composable("list") {
			val visibleRows = conversationRows.values
				.filter { it.id != "_admin" && !it.hidden }
				.sortedByDescending { it.lastActivityAt }
			val hiddenRows = conversationRows.values
				.filter { it.id != "_admin" && it.hidden }
				.sortedByDescending { it.lastActivityAt }
			val adminRow = conversationRows["_admin"]
			// Registry-backed resume enablement (Task 11 - replaces the deleted member
			// archaeology): a conversation is resumable if any member's session has a
			// terminal registry record.
			val resumableByConvId = conversationRows.values.associate {
				it.id to conversationResumable(it.members, registrySessions)
			}

			ConversationListScreen(
				rows = visibleRows,
				hiddenRows = hiddenRows,
				adminRow = adminRow,
				showHidden = showHidden,
				globalAway = globalAway,
				onSessionClick = { row -> navController.navigate("session/${row.id}") },
				onAdminClick = { _ ->
					// Admin row is currently a passive surface — clicking it doesn't navigate
					// anywhere meaningful in this dispatch. Backlog: dedicated admin notifications screen.
				},
				onToggleShowHidden = { showHidden = !showHidden },
				onEnterGlobalAway = { viewModel.requestAwayModeToggle(null, true) },
				onExitGlobalAway = { viewModel.requestAwayModeToggle(null, false) },
				onHideConversation = { viewModel.hideConversation(it.id) },
				onUnhideConversation = { viewModel.unhideConversation(it.id) },
				onSpawnClick = { showSpawnDialog = true },
				sessionBadgeCount = sessionBadgeCount(registrySessions, sessionAcks),
				onSessionsClick = { navController.navigate("sessions") },
				resumableByConvId = resumableByConvId,
				onResumeClick = { convId -> resumeConversationId = convId },
				onCombineClick = { convId -> combineConversationId = convId },
				onEndClick = { convId -> viewModel.endConversation(convId) },
				rings = widgetRings,
				quota = widgetQuota,
				claudeStatus = widgetStatus,
				pushedAt = widgetPushedAt,
				onCheckStatus = { viewModel.requestClaudeStatusCheck() },
				onStopStatus = { viewModel.stopClaudeStatusWatch() },
				authState = authState,
				onRetrySignIn = { retryTick++ },
			)
		}
		composable("sessions") {
			val onSessionDetails: (io.github.johnjanthony.switchboard.network.RegistrySession) -> Unit = { rec ->
				viewModel.ackSession(rec.cliSessionId)
				detailSession = rec
			}
			SessionsBoardScreen(
				sessions = registrySessions,
				acks = sessionAcks,
				activeConversations = activeConversations,
				globalAway = globalAway,
				onBack = { navController.popBackStack() },
				onRowClick = { rec ->
					viewModel.ackSession(rec.cliSessionId)
					val convId = rec.conversationId
					if (convId != null) {
						navController.navigate("session/$convId")
					} else {
						onSessionDetails(rec)
					}
				},
				onDetails = onSessionDetails,
				onResume = { rec -> resumeSheetSession = rec },
				onConvene = { ids, target, title ->
					// conveneSessions never touches away mode - no away-mode toast here, unlike spawn/resume.
					viewModel.conveneSessions(ids, target, title)
					Toast.makeText(context, "Convene sent", Toast.LENGTH_SHORT).show()
				},
				onEnterGlobalAway = { viewModel.requestAwayModeToggle(null, true) },
				onExitGlobalAway = { viewModel.requestAwayModeToggle(null, false) },
			)
		}
		composable(
			route = "session/{convId}",
			arguments = listOf(navArgument("convId") { type = NavType.StringType }),
		) { backStackEntry ->
			val convId = backStackEntry.arguments?.getString("convId") ?: return@composable
			val row = conversationRows[convId]
			if (row == null) {
				androidx.compose.foundation.layout.Box(
					modifier = Modifier.padding(24.dp),
				) {
					Text(
						text = "Loading…",
						style = MaterialTheme.typography.bodyMedium,
						color = MaterialTheme.colorScheme.onSurfaceVariant,
					)
				}
				return@composable
			}
			val awayActive = viewModel.isAwayActive(convId)
			var infoOpen by remember { mutableStateOf(false) }

			DisposableEffect(convId) {
				viewModel.selectConversation(convId)
				onDispose {
					viewModel.clearSelectedChannel()
				}
			}

			ConversationViewScreen(
				row = row,
				scrollToMessageId = deepLinkMessageId.value,
				onScrollConsumed = { deepLinkMessageId.value = null },
				awayActive = awayActive,
				predecessorTitle = predecessorTitle(row, conversationRows),
				onOpenPredecessor = {
					row.continuedFrom?.let { navController.navigate("session/$it") { launchSingleTop = true } }
				},
				onBack = { navController.popBackStack() },
				onLongPressPill = { viewModel.requestAwayModeToggle(null, !awayActive) },
				onSubmitReply = { sender, text, requestId ->
					// ReplyInputBar only opens for an actively-selected pending question,
					// which always carries a request_id. Drop the call if somehow null.
					if (requestId != null) {
						viewModel.submitReplyForConversation(convId, sender, text, requestId)
					}
				},
				onDownloadFile = { url, filename -> viewModel.downloadAndOpenFile(context, url, filename) },
				onLongPressDownloadFile = { url, filename -> viewModel.saveFileToDownloads(context, url, filename) },
				onMarkMessageOpened = { msgId -> viewModel.markMessageOpened(convId, msgId) },
				onShowTabInfo = { infoOpen = true },
			)
			if (infoOpen) {
				TabInfoPopover(
					row = row,
					awayActive = awayActive,
					rings = widgetRings,
					onDismiss = { infoOpen = false },
					onToggleHidden = {
						if (row.hidden) viewModel.unhideConversation(convId)
						else viewModel.hideConversation(convId)
					},
					onToggleAway = { viewModel.requestAwayModeToggle(null, !awayActive) },
				)
			}
		}
		composable("markdown_viewer") {
			val content = markdownViewerContent
			if (content != null) {
				MarkdownViewerScreen(
					title = content.first,
					content = content.second,
					onBack = {
						navController.popBackStack()
						viewModel.closeMarkdownViewer()
					},
				)
			}
		}
	}

	pendingExitToggle?.let { pending ->
		BulkRespondDialog(
			payload = pending.payload,
			onSendToAll = { text -> viewModel.submitExitToggleDecision("send_default", text) },
			onSkip = { viewModel.submitExitToggleDecision("skip", null) },
			onCancel = { viewModel.cancelExitToggle() },
		)
	}
	if (showSpawnDialog) {
		SpawnSessionDialog(
			mruList = projectMru,
			activeConversations = pickerTargets(activeConversations),
			wslAvailable = wslAvailable,
			onDismiss = { showSpawnDialog = false },
			onSpawn = { surface, project, prompt, targetConversationId ->
				val wasAwayOff = viewModel.spawnSession(surface, project, prompt, targetConversationId)
				if (wasAwayOff) {
					Toast.makeText(context, "Away mode enabled", Toast.LENGTH_SHORT).show()
				}
				showSpawnDialog = false
			},
			onRemoveFromMru = { viewModel.removeFromProjectMru(it) },
		)
	}

	resumeConversationId?.let { convId ->
		val conv = activeConversations.firstOrNull { it.id == convId }
		if (conv != null) {
			SpawnResumeDialog(
				sourceConversation = conv,
				onDismiss = { resumeConversationId = null },
				onResume = { newPrompt ->
					viewModel.resumeConversation(convId, newPrompt)
					resumeConversationId = null
				},
			)
		} else {
			resumeConversationId = null
		}
	}

	combineConversationId?.let { sourceId ->
		val source = activeConversations.firstOrNull { it.id == sourceId }
		val targets = pickerTargets(activeConversations, excludeId = sourceId)
		if (source != null) {
			CombineDialog(
				sourceConversation = source,
				activeConversations = targets,
				onDismiss = { combineConversationId = null },
				onCombine = { targetId ->
					viewModel.combineConversations(sourceId, targetId)
					combineConversationId = null
				},
			)
		} else {
			combineConversationId = null
		}
	}

	detailSession?.let { rec ->
		SessionDetailSheet(
			rec = rec,
			conversationTitle = activeConversations.firstOrNull { it.id == rec.conversationId }?.title,
			onDismiss = { detailSession = null },
			onOpenConversation = { convId ->
				detailSession = null
				navController.navigate("session/$convId")
			},
			onResume = if (isSessionResumable(rec)) {
				{ resumeSheetSession = rec; detailSession = null }
			} else null,
		)
	}

	resumeSheetSession?.let { rec ->
		ResumeSessionSheet(
			rec = rec,
			boardLabel = sessionBoardLabel(rec),
			oldConversation = rec.conversationId?.let { id -> activeConversations.firstOrNull { it.id == id } },
			activeConversations = pickerTargets(activeConversations),
			onDismiss = { resumeSheetSession = null },
			onResume = { targetId, prompt ->
				val wasAwayOff = viewModel.resumeSession(rec.cliSessionId, targetId, prompt)
				if (wasAwayOff) {
					Toast.makeText(context, "Away mode enabled", Toast.LENGTH_SHORT).show()
				}
				resumeSheetSession = null
			},
		)
	}
}


@OptIn(ExperimentalFoundationApi::class)
@Composable
fun AwayModePillChip(active: Boolean, onLongPress: () -> Unit) {
	val brass = MaterialTheme.colorScheme.primary
	val bg = if (active) brass.copy(alpha = 0.13f) else Color.Transparent
	val borderColor = if (active) brass.copy(alpha = 0.34f) else MaterialTheme.colorScheme.outline
	val contentColor = if (active) brass else MaterialTheme.colorScheme.onSurfaceVariant
	val label = if (active) "AWAY" else "AT DESK"

	androidx.compose.foundation.layout.Box(
		modifier = Modifier
			.padding(horizontal = 4.dp)
			.border(1.dp, borderColor, RoundedCornerShape(50))
			.background(bg, RoundedCornerShape(50))
			.combinedClickable(
				onClick = {},
				onLongClick = onLongPress,
			)
			.padding(horizontal = 10.dp, vertical = 4.dp)
	) {
		Row(verticalAlignment = Alignment.CenterVertically) {
			if (active) {
				Icon(
					imageVector = Icons.Filled.DarkMode,
					contentDescription = null,
					tint = brass,
					modifier = Modifier.size(12.dp),
				)
				Spacer(Modifier.width(5.dp))
			}
			Text(label, style = MaterialTheme.typography.labelSmall, color = contentColor)
		}
	}
}


@Composable
fun MarkdownText(
	content: String,
	format: String,
	color: Color = Color.Unspecified,
	isSelectable: Boolean = true,
	fontScale: Float = 1f,
	onInternalLinkClick: ((TextView, String) -> Unit)? = null,
) {
	if (format == "markdown") {
		val textColor = color.toArgb()
		val ctx = LocalContext.current
		val currentLink = rememberUpdatedState(onInternalLinkClick)
		val codePadPx = (12 * ctx.resources.displayMetrics.density).toInt()
		val codeRadiusPx = 8f * ctx.resources.displayMetrics.density
		val markwon = remember(ctx) {
			io.noties.markwon.Markwon.builder(ctx)
				.usePlugin(io.noties.markwon.html.HtmlPlugin.create())
				.usePlugin(io.noties.markwon.ext.tables.TablePlugin.create(ctx))
				.usePlugin(io.noties.markwon.ext.tasklist.TaskListPlugin.create(ctx))
				.usePlugin(io.noties.markwon.ext.strikethrough.StrikethroughPlugin.create())
				.usePlugin(io.noties.markwon.simple.ext.SimpleExtPlugin.create())
				.usePlugin(io.github.johnjanthony.switchboard.ui.SwitchboardSyntaxHighlightPlugin())
				.usePlugin(object : io.noties.markwon.AbstractMarkwonPlugin() {
					override fun configureSpansFactory(builder: io.noties.markwon.MarkwonSpansFactory.Builder) {
						val codeBlockFactory = io.noties.markwon.SpanFactory { config, _ ->
							io.github.johnjanthony.switchboard.ui.RoundedCodeBlockSpan(
								config.theme(), 0xFF0E1014.toInt(), codeRadiusPx,
							)
						}
						builder.setFactory(org.commonmark.node.FencedCodeBlock::class.java, codeBlockFactory)
						builder.setFactory(org.commonmark.node.IndentedCodeBlock::class.java, codeBlockFactory)
					}
				})
				.usePlugin(object : io.noties.markwon.AbstractMarkwonPlugin() {
					override fun configureTheme(builder: io.noties.markwon.core.MarkwonTheme.Builder) {
						builder.codeBlockBackgroundColor(0xFF0E1014.toInt())
						builder.codeBackgroundColor(0xFF0E1014.toInt())
						builder.codeBlockMargin(codePadPx)
					}
				})
				.usePlugin(object : io.noties.markwon.AbstractMarkwonPlugin() {
					override fun configureConfiguration(builder: io.noties.markwon.MarkwonConfiguration.Builder) {
						builder.linkResolver(object : io.noties.markwon.LinkResolver {
							override fun resolve(v: android.view.View, link: String) {
								val cb = currentLink.value
								if (link.startsWith("#") && cb != null) cb(v as TextView, link)
								else if (isAllowedLinkScheme(link)) io.noties.markwon.LinkResolverDef().resolve(v, link)
								// Disallowed scheme: deliberate no-op (see LinkSchemes.kt).
							}
						})
					}
				})
				.build()
		}
		val lastRendered = remember { androidx.compose.runtime.mutableStateOf<Triple<String, Float, Int>?>(null) }
		AndroidView(
			modifier = Modifier.fillMaxWidth(),
			factory = { ctx ->
				// Subclass TextView to swallow a known Android framework bug: long-press
				// on selectable text inside a Markwon table cell can call startDragAndDrop
				// with non-positive bounds, throwing IllegalStateException("Drag shadow
				// dimensions must be positive"). startDragAndDrop is final and can't be
				// overridden, so catch at performLongClick — suppresses just the long-click
				// fallout; the selection action bar still appears via the touch path.
				object : TextView(ctx) {
					override fun performLongClick(): Boolean = try {
						super.performLongClick()
					} catch (e: IllegalStateException) {
						// Surgical filter on the known framework signature. Anything else rethrows.
						if (e.message?.contains("Drag shadow") == true) {
							android.util.Log.w("MarkdownText", "Suppressed framework drag-shadow crash", e)
							false
						} else {
							throw e
						}
					}
				}.apply {
					android.text.method.LinkMovementMethod.getInstance().let { movementMethod = it }
					// setMovementMethod(...) auto-flips isClickable/isLongClickable to true
					// (Android-internal fixFocusableAndClickableSettings). That makes the
					// TextView consume every touch and prevents clicks from propagating out
					// to the outer Compose Surface's combinedClickable, breaking the
					// question-bubble click-to-select. Reset to false here — link taps still
					// work because LinkMovementMethod handles them via onTouchEvent
					// regardless of isClickable.
					isClickable = isSelectable
					isLongClickable = isSelectable
					setTextIsSelectable(isSelectable)
				}
			},
			update = { view ->
				if (view.isTextSelectable != isSelectable) {
					view.setTextIsSelectable(isSelectable)
					view.isClickable = isSelectable
					view.isLongClickable = isSelectable
				}
				if (color != Color.Unspecified) {
					view.setTextColor(textColor)
				}
				val token = Triple(content, fontScale, textColor)
				if (lastRendered.value != token) {
					view.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 14f * fontScale)
					markwon.setMarkdown(view, content)
					lastRendered.value = token
				}
			}
		)
	} else {
		Text(
			content,
			style = MaterialTheme.typography.bodyMedium,
			fontSize = 14.sp * fontScale,
			lineHeight = 17.sp * fontScale,
			color = color,
		)
	}
}
