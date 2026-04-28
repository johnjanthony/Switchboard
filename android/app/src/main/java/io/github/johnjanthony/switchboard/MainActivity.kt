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
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import io.github.johnjanthony.switchboard.fcm.SwitchboardFirebaseMessagingService
import io.github.johnjanthony.switchboard.ui.BulkRespondDialog
import io.github.johnjanthony.switchboard.ui.MarkdownViewerScreen
import io.github.johnjanthony.switchboard.ui.SessionListScreen
import io.github.johnjanthony.switchboard.ui.SessionViewScreen
import io.github.johnjanthony.switchboard.ui.SpawnCollisionDialog
import io.github.johnjanthony.switchboard.ui.SpawnSessionDialog
import io.github.johnjanthony.switchboard.ui.TabInfoPopover
import io.github.johnjanthony.switchboard.ui.theme.SwitchboardTheme

class MainActivity : ComponentActivity() {
	private val viewModel: MainViewModel by viewModels()
	private val pendingDeepLinkCwdKey = mutableStateOf<String?>(null)
	private val pendingDeepLinkMessageId = mutableStateOf<String?>(null)

	private val requestPermissionLauncher = registerForActivityResult(
		ActivityResultContracts.RequestPermission()
	) { /* ignored */ }

	override fun onCreate(savedInstanceState: Bundle?) {
		super.onCreate(savedInstanceState)
		requestNotificationPermission()
		pendingDeepLinkCwdKey.value = intent.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_AGENT_ID)
		pendingDeepLinkMessageId.value = intent.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_MESSAGE_ID)
		setContent {
			SwitchboardTheme {
				SwitchboardNavHost(viewModel, pendingDeepLinkCwdKey, pendingDeepLinkMessageId)
			}
		}
	}

	override fun onNewIntent(intent: Intent) {
		super.onNewIntent(intent)
		setIntent(intent)
		val cwdKey = intent.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_AGENT_ID)
		val messageId = intent.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_MESSAGE_ID)
		if (cwdKey != null) pendingDeepLinkCwdKey.value = cwdKey
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
	deepLinkCwdKey: MutableState<String?>,
	deepLinkMessageId: MutableState<String?>,
) {
	val context = androidx.compose.ui.platform.LocalContext.current
	val navController = rememberNavController()
	val channels by viewModel.channels.collectAsState()
	val globalAway by viewModel.globalAway.collectAsState()
	val cwdOverrides by viewModel.cwdOverrides.collectAsState()
	val pendingCollision by viewModel.pendingCollision.collectAsState()
	val bulkRespond by viewModel.bulkRespondDialog.collectAsState()
	val markdownViewerContent by viewModel.markdownViewerContent.collectAsState()
	val projectMru by viewModel.projectMru.collectAsState()
	var showHidden by remember { mutableStateOf(false) }
	var showSpawnDialog by remember { mutableStateOf(false) }

	// K5: deep-link navigation from FCM tap
	val cwdKey by deepLinkCwdKey
	LaunchedEffect(cwdKey) {
		val key = cwdKey
		if (key != null) {
			navController.navigate("session/$key") { launchSingleTop = true }
			deepLinkCwdKey.value = null
		}
	}

	// Navigation to markdown viewer when content is loaded
	LaunchedEffect(markdownViewerContent) {
		if (markdownViewerContent != null) {
			navController.navigate("markdown_viewer") { launchSingleTop = true }
		}
	}

	NavHost(navController = navController, startDestination = "list") {
		composable("list") {
			val visibleChannels = channels.values
				.filter { !it.hidden }
				.sortedByDescending { it.lastActivityAt ?: "" }
			val hiddenChannels = channels.values
				.filter { it.hidden }
				.sortedByDescending { it.lastActivityAt ?: "" }

			SessionListScreen(
				channels = visibleChannels,
				hiddenChannels = hiddenChannels,
				showHidden = showHidden,
				globalAway = globalAway,
				cwdOverrides = cwdOverrides,
				onSessionClick = { ch -> navController.navigate("session/${ch.cwdKey}") },
				onToggleShowHidden = { showHidden = !showHidden },
				onEnterGlobalAway = { viewModel.requestAwayModeToggle(null, true) },
				onExitGlobalAway = { viewModel.requestAwayModeToggle(null, false) },
				onHideChannel = { viewModel.hideChannel(it.cwdKey) },
				onUnhideChannel = { viewModel.unhideChannel(it.cwdKey) },
				onAwayToggle = { viewModel.requestAwayModeToggle(it.cwdKey, false) },
				onSpawnClick = { showSpawnDialog = true },
			)
		}
		composable(
			route = "session/{cwdKey}",
			arguments = listOf(navArgument("cwdKey") { type = NavType.StringType }),
		) { backStackEntry ->
			val cwdKey = backStackEntry.arguments?.getString("cwdKey") ?: return@composable
			val channel = channels[cwdKey]
			if (channel == null) {
				// Cold-start race: notification deep link can navigate here before
				// the Firebase channels listener has populated. Show a brief loading
				// state and let recomposition pick up the channel once it lands.
				// Don't popBackStack — that would yank the user back to the list and
				// strand the deep link.
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
			val awayActive = viewModel.isAwayActive(cwdKey)
			val isOverride = cwdOverrides.containsKey(cwdKey)
			var infoOpen by remember { mutableStateOf(false) }

			SessionViewScreen(
				channel = channel,
				messages = channel.messages.sortedBy { it.second.timestamp },
				awayActive = awayActive,
				isAwayOverride = isOverride,
				globalAway = globalAway,
				currentPending = channel.pendingQuestions,
				scrollToMessageId = deepLinkMessageId.value,
				onScrollConsumed = { deepLinkMessageId.value = null },
				onBack = { navController.popBackStack() },
				onTapPill = { viewModel.requestAwayModeToggle(cwdKey, !awayActive) },
				onLongPressPillConfirm = { viewModel.requestAwayModeToggle(cwdKey, !awayActive) },
				onSubmitReply = { sender, text -> viewModel.submitReply(cwdKey, sender, text) },
				onDownloadFile = { url, filename -> viewModel.downloadAndOpenFile(context, url, filename) },
				onLongPressDownloadFile = { url, filename -> viewModel.saveFileToDownloads(context, url, filename) },
				onShowTabInfo = { infoOpen = true },
			)
			if (infoOpen) {
				TabInfoPopover(
					channel = channel,
					awayActive = awayActive,
					onDismiss = { infoOpen = false },
					onToggleHidden = {
						if (channel.hidden) viewModel.unhideChannel(cwdKey)
						else viewModel.hideChannel(cwdKey)
					},
					onToggleAway = { viewModel.requestAwayModeToggle(cwdKey, !awayActive) },
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

	// K4: surface dialogs regardless of which page is active
	if (pendingCollision != null) {
		SpawnCollisionDialog(
			collision = pendingCollision!!,
			onContinue = { viewModel.resolveSpawnCollision(pendingCollision!!.spawnId, "continue") },
			onClear = { viewModel.resolveSpawnCollision(pendingCollision!!.spawnId, "clear") },
			onCancel = { viewModel.resolveSpawnCollision(pendingCollision!!.spawnId, "cancel") },
		)
	}
	if (bulkRespond != null) {
		BulkRespondDialog(
			payload = bulkRespond!!,
			onSendToAll = { text -> viewModel.submitBulkRespond("send_to_all", text) },
			onSkip = { viewModel.submitBulkRespond("skip") },
			onCancel = { viewModel.submitBulkRespond("cancel") },
		)
	}
	if (showSpawnDialog) {
		SpawnSessionDialog(
			mruList = projectMru,
			onDismiss = { showSpawnDialog = false },
			onSpawn = { project, prompt, useClaude, useGemini ->
				viewModel.spawnSession(project, prompt, useClaude, useGemini)
				showSpawnDialog = false
			},
			onRemoveFromMru = { viewModel.removeFromProjectMru(it) },
		)
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
				onClick = onLongPress,
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
	onInternalLinkClick: ((TextView, String) -> Unit)? = null,
) {
	if (format == "markdown") {
		val textColor = color.toArgb()
		AndroidView(
			factory = { ctx ->
				TextView(ctx).apply {
					android.text.method.LinkMovementMethod.getInstance().let { movementMethod = it }
					// setMovementMethod(...) auto-flips isClickable/isLongClickable to true
					// (Android-internal fixFocusableAndClickableSettings). That makes the
					// TextView consume every touch and prevents clicks from propagating out
					// to the outer Compose Surface's combinedClickable, breaking the
					// question-bubble click-to-select. Reset to false here — link taps still
					// work because LinkMovementMethod handles them via onTouchEvent
					// regardless of isClickable.
					isClickable = false
					isLongClickable = false
				}
			},
			update = { view ->
				if (color != Color.Unspecified) {
					view.setTextColor(textColor)
				}
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
		Text(content, style = MaterialTheme.typography.bodyMedium, color = color)
	}
}
