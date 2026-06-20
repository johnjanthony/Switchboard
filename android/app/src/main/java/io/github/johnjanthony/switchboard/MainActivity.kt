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
import androidx.compose.runtime.setValue
import androidx.compose.runtime.MutableState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
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
import io.github.johnjanthony.switchboard.fcm.SwitchboardFirebaseMessagingService
import io.github.johnjanthony.switchboard.shared.GoogleAuthHelper
import android.widget.Toast
import io.github.johnjanthony.switchboard.ui.BulkRespondDialog
import io.github.johnjanthony.switchboard.ui.CombineDialog
import io.github.johnjanthony.switchboard.ui.MarkdownViewerScreen
import io.github.johnjanthony.switchboard.ui.SessionListScreen
import io.github.johnjanthony.switchboard.ui.SessionViewScreen
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
		pendingDeepLinkConvId.value = intent.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_AGENT_ID)
		pendingDeepLinkMessageId.value = intent.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_MESSAGE_ID)
		setContent {
			SwitchboardTheme {
				SwitchboardNavHost(viewModel, pendingDeepLinkConvId, pendingDeepLinkMessageId)
			}
		}
	}

	override fun onNewIntent(intent: Intent) {
		super.onNewIntent(intent)
		setIntent(intent)
		val convId = intent.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_AGENT_ID)
		val messageId = intent.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_MESSAGE_ID)
		if (convId != null) pendingDeepLinkConvId.value = convId
		if (messageId != null) pendingDeepLinkMessageId.value = messageId
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
	var showHidden by remember { mutableStateOf(false) }
	var showSpawnDialog by remember { mutableStateOf(false) }
	// T-027 dialogs
	var resumeConversationId by remember { mutableStateOf<String?>(null) }
	var combineConversationId by remember { mutableStateOf<String?>(null) }

	// Automatic Google Sign-In on first start
	LaunchedEffect(Unit) {
		if (FirebaseAuth.getInstance().currentUser == null) {
			GoogleAuthHelper.signInWithGoogle(context)
		}
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

			SessionListScreen(
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
				onResumeClick = { convId -> resumeConversationId = convId },
				onCombineClick = { convId -> combineConversationId = convId },
				onEndClick = { convId -> viewModel.endConversation(convId) },
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

			SessionViewScreen(
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
			activeConversations = activeConversations,
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
		val targets = activeConversations.filter { it.id != sourceId }
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
}


@OptIn(ExperimentalFoundationApi::class)
@Composable
fun AwayModePillChip(active: Boolean, onLongPress: () -> Unit) {
	val bg = if (active) MaterialTheme.colorScheme.error else Color.Transparent
	val borderColor = if (active) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
	val textColor = if (active) Color.White else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.8f)
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
		Text(label, style = MaterialTheme.typography.labelSmall, color = textColor)
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
		AndroidView(
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
				// Apply fontScale BEFORE markwon.setMarkdown so RelativeSizeSpan-based
				// header/code sizing scales with the base.
				view.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 14f * fontScale)
				val markwon = io.noties.markwon.Markwon.builder(view.context)
					.usePlugin(io.noties.markwon.html.HtmlPlugin.create())
					.usePlugin(io.noties.markwon.ext.tables.TablePlugin.create(view.context))
					.usePlugin(io.noties.markwon.ext.tasklist.TaskListPlugin.create(view.context))
					.usePlugin(io.noties.markwon.ext.strikethrough.StrikethroughPlugin.create())
					.usePlugin(io.noties.markwon.simple.ext.SimpleExtPlugin.create())
					.usePlugin(io.github.johnjanthony.switchboard.ui.SwitchboardSyntaxHighlightPlugin())
					.usePlugin(object : io.noties.markwon.AbstractMarkwonPlugin() {
						override fun configureConfiguration(builder: io.noties.markwon.MarkwonConfiguration.Builder) {
							builder.linkResolver(object : io.noties.markwon.LinkResolver {
								override fun resolve(v: android.view.View, link: String) {
									if (link.startsWith("#") && onInternalLinkClick != null) {
										onInternalLinkClick(v as TextView, link)
									} else {
										io.noties.markwon.LinkResolverDef().resolve(v, link)
									}
								}
							})
						}
					})
					.build()
				markwon.setMarkdown(view, content)
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
