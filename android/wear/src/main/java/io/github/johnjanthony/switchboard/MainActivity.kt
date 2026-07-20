package io.github.johnjanthony.switchboard

import android.Manifest
import android.app.Activity
import android.net.Uri
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.speech.RecognizerIntent
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.navigation.NavHostController
import androidx.wear.compose.material3.*
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.items
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.navigation.SwipeDismissableNavHost
import androidx.wear.compose.navigation.composable
import androidx.wear.compose.navigation.rememberSwipeDismissableNavController
import android.text.method.LinkMovementMethod
import android.widget.TextView
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.viewinterop.AndroidView
import com.google.firebase.auth.FirebaseAuth
import io.github.johnjanthony.switchboard.fcm.BaseSwitchboardMessagingService
import io.github.johnjanthony.switchboard.shared.GoogleAuthHelper

class MainActivity : ComponentActivity() {
	private val viewModel: MainViewModel by viewModels()
	// Holds a conv_id from an FCM deep link until WearApp's LaunchedEffect can
	// navigate to it once the conversation appears in conversationRows.
	private val pendingDeepLinkConvId = mutableStateOf<String?>(null)

	private val requestPermissionLauncher = registerForActivityResult(
		ActivityResultContracts.RequestPermission()
	) { /* ignored */ }

	override fun onCreate(savedInstanceState: Bundle?) {
		super.onCreate(savedInstanceState)
		requestNotificationPermission()
		handleNotificationIntent(intent)
		setContent {
			WearApp(viewModel, pendingDeepLinkConvId)
		}
	}

	override fun onNewIntent(intent: Intent) {
		super.onNewIntent(intent)
		setIntent(intent)
		handleNotificationIntent(intent)
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

	private fun handleNotificationIntent(intent: Intent?) {
		intent?.getStringExtra(BaseSwitchboardMessagingService.EXTRA_AGENT_ID)?.let { value ->
			pendingDeepLinkConvId.value = value
		}
		intent?.getStringExtra(BaseSwitchboardMessagingService.EXTRA_MESSAGE_ID)?.let { messageId ->
			viewModel.setPendingDeepLinkMessageId(messageId)
		}
		// Scrub the consumed extras so activity recreation does not re-fire them.
		intent?.removeExtra(BaseSwitchboardMessagingService.EXTRA_AGENT_ID)
		intent?.removeExtra(BaseSwitchboardMessagingService.EXTRA_MESSAGE_ID)
	}
}

@Composable
fun WearApp(viewModel: MainViewModel, pendingDeepLinkConvId: MutableState<String?>) {
	val context = LocalContext.current
	val navController = rememberSwipeDismissableNavController()
	val conversationRows by viewModel.conversationRows.collectAsState()

	// Automatic Google Sign-In on first start, with a retryable failure state (REV-206).
	var authState by remember {
		mutableStateOf(
			if (FirebaseAuth.getInstance().currentUser != null) AuthUiState.SIGNED_IN
			else AuthUiState.IN_PROGRESS
		)
	}
	var retryTick by remember { mutableStateOf(0) }
	LaunchedEffect(retryTick) {
		if (FirebaseAuth.getInstance().currentUser != null) { authState = AuthUiState.SIGNED_IN; return@LaunchedEffect }
		authState = AuthUiState.IN_PROGRESS
		val ok = GoogleAuthHelper.signInWithGoogle(context)
		authState = if (ok) AuthUiState.SIGNED_IN else AuthUiState.FAILED
	}

	// FCM deep-link: navigate to the conversation once it is present in the rows.
	// If it is not present yet (cold-start race), wait for the next rows tick
	// rather than dead-ending (F-90).
	val pendingDeepConvId by pendingDeepLinkConvId
	LaunchedEffect(pendingDeepConvId, conversationRows) {
		val convId = pendingDeepConvId ?: return@LaunchedEffect
		if (!conversationRows.containsKey(convId)) return@LaunchedEffect
		val currentRoute = navController.currentBackStackEntry?.destination?.route
		if (currentRoute != "message_view/$convId") {
			navController.navigate("message_view/$convId") {
				popUpTo("conversation_list") { saveState = true }
				launchSingleTop = true
				restoreState = true
			}
		}
		pendingDeepLinkConvId.value = null
	}

	MaterialTheme {
		AppScaffold {
			SwipeDismissableNavHost(
				navController = navController,
				startDestination = "conversation_list"
			) {
				composable("conversation_list") {
					ConversationListScreen(viewModel, navController, authState, { retryTick++ })
				}
				composable("message_view/{convId}") { backStackEntry ->
					val convId = backStackEntry.arguments?.getString("convId") ?: ""
					MessageViewScreen(convId, viewModel, navController)
				}
				composable("reply/{convId}/{requestId}") { backStackEntry ->
					val convId = backStackEntry.arguments?.getString("convId") ?: ""
					val requestId = backStackEntry.arguments?.getString("requestId") ?: ""
					ReplyScreen(convId, requestId, viewModel, navController)
				}
			}
		}
	}
}

@Composable
fun ConversationListScreen(
	viewModel: MainViewModel,
	navController: NavHostController,
	authState: AuthUiState,
	onRetry: () -> Unit,
) {
	val rows by viewModel.conversationRows.collectAsState()
	val (needsReply, others) = remember(rows) { partitionConversationsForWatch(rows.values) }

	val listState = rememberScalingLazyListState()

	ScreenScaffold(
		scrollState = listState,
		timeText = { TimeText() },
		scrollIndicator = { ScrollIndicator(state = listState) }
	) {
		ScalingLazyColumn(
			state = listState,
			modifier = Modifier.fillMaxSize(),
			horizontalAlignment = Alignment.CenterHorizontally
		) {
			if (needsReply.isEmpty() && others.isEmpty()) {
				// Never show a bare black screen: signed-out / connecting / genuinely-empty
				// all land here with a hint instead of nothing (the message view has its
				// own loading branch; the list needs one too).
				val kind = emptyStateFor(needsReply.isNotEmpty() || others.isNotEmpty(), authState)
				item { WearListEmptyState(kind, onRetry) }
			} else {
				if (needsReply.isNotEmpty()) {
					item { SectionHeader("Needs reply") }
					items(needsReply, key = { it.id }) { row ->
						ConversationRowButton(row, pending = true) {
							navController.navigate("message_view/${row.id}")
						}
					}
				}
				if (others.isNotEmpty()) {
					item { SectionHeader("Conversations") }
					items(others, key = { it.id }) { row ->
						ConversationRowButton(row, pending = false) {
							navController.navigate("message_view/${row.id}")
						}
					}
				}
			}
		}
	}
}

@Composable
private fun SectionHeader(text: String) {
	Text(
		text = text,
		style = MaterialTheme.typography.labelSmall,
		color = MaterialTheme.colorScheme.primary,
		modifier = Modifier.fillMaxWidth().padding(start = 6.dp, top = 6.dp, bottom = 2.dp),
	)
}

@Composable
private fun ConversationRowButton(
	row: io.github.johnjanthony.switchboard.network.ConversationRow,
	pending: Boolean,
	onClick: () -> Unit,
) {
	val count = if (pending) pendingReplyCount(row) else row.displayCount
	Button(
		onClick = onClick,
		modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 2.dp),
		colors = if (pending) ButtonDefaults.buttonColors() else ButtonDefaults.filledTonalButtonColors(),
		label = {
			Row(verticalAlignment = Alignment.CenterVertically) {
				if (count > 0) {
					Text("[$count] ", color = MaterialTheme.colorScheme.primary)
				}
				Text(
					text = row.title,
					overflow = TextOverflow.Ellipsis,
					maxLines = 1,
					style = MaterialTheme.typography.labelMedium,
				)
			}
		}
	)
}

@Composable
private fun WearListEmptyState(kind: EmptyStateKind, onRetry: () -> Unit) {
	Column(
		modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 24.dp),
		horizontalAlignment = Alignment.CenterHorizontally,
	) {
		when (kind) {
			EmptyStateKind.SIGN_IN_FAILED -> {
				Text("Sign-in failed", style = MaterialTheme.typography.titleSmall, textAlign = TextAlign.Center)
				Button(onClick = onRetry, modifier = Modifier.padding(top = 8.dp)) { Text("Sign in") }
			}
			EmptyStateKind.NO_CONVERSATIONS -> {
				Text("No conversations", style = MaterialTheme.typography.titleSmall, textAlign = TextAlign.Center)
				Text("Conversations will appear here.", style = MaterialTheme.typography.bodySmall,
					color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
					modifier = Modifier.padding(top = 4.dp))
			}
			else -> {
				Text("Connecting…", style = MaterialTheme.typography.titleSmall, textAlign = TextAlign.Center)
				Text("Waiting for Google sign-in.", style = MaterialTheme.typography.bodySmall,
					color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
					modifier = Modifier.padding(top = 4.dp))
			}
		}
	}
}

@Composable
fun MessageViewScreen(convId: String, viewModel: MainViewModel, navController: NavHostController) {
	val rows by viewModel.conversationRows.collectAsState()
	val row = rows[convId]
	val pendingMessageId by viewModel.pendingDeepLinkMessageId.collectAsState()
	val listState = rememberScalingLazyListState()

	// Opening a conversation clears its unread badge (sentinel-guarded for _admin).
	DisposableEffect(convId) {
		viewModel.selectConversation(convId)
		onDispose { viewModel.clearSelectedChannel() }
	}

	if (row == null) {
		// Cold-start race or unknown conv: show a loading state, do not dead-end (F-90).
		Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
			Text("Loading...", style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center)
		}
		return
	}

	// Consume the shared VM's spliced order and authoritative answered set directly; no
	// local re-derivation (REV-205).
	val messages = row.messages
	val answeredMsgIds = row.answeredQuestionMsgIds

	LaunchedEffect(pendingMessageId, messages.size) {
		val targetId = pendingMessageId
		if (targetId != null) {
			val idx = messages.indexOfFirst { it.first == targetId }
			if (idx >= 0) {
				listState.scrollToItem(idx + 1)  // index 0 is the header
				viewModel.clearPendingDeepLinkMessageId()
			}
			return@LaunchedEffect
		}
		if (messages.isNotEmpty()) {
			listState.scrollToItem(messages.size)
		}
	}

	ScreenScaffold(
		scrollState = listState,
		timeText = { TimeText() },
		scrollIndicator = { ScrollIndicator(state = listState) }
	) {
		ScalingLazyColumn(
			state = listState,
			modifier = Modifier.fillMaxSize(),
			horizontalAlignment = Alignment.CenterHorizontally
		) {
			item {
				Text(
					text = row.title,
					style = MaterialTheme.typography.titleSmall,
					textAlign = TextAlign.Center,
					modifier = Modifier.padding(bottom = 6.dp),
				)
			}
			items(messages) { (msgId, msg) ->
				val answerable = msg.request_id != null &&
					isAnswerableQuestion(msg.type, msgId, answeredMsgIds, msg.cancelled, msg.rejected)
				if (answerable) {
					Card(
						onClick = {
							navController.navigate("reply/${Uri.encode(convId)}/${Uri.encode(msg.request_id)}")
						},
						modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp),
						colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primary),
					) {
						Column {
							Text(msg.sender, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onPrimary)
							MarkdownText(content = msg.text, format = msg.format, color = MaterialTheme.colorScheme.onPrimary)
							Text(
								text = "TAP TO REPLY",
								style = MaterialTheme.typography.labelMedium,
								color = MaterialTheme.colorScheme.onPrimary,
								modifier = Modifier.padding(top = 4.dp),
							)
						}
					}
				} else {
					Card(
						onClick = {},
						modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp),
					) {
						Column {
							Text(msg.sender, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
							MarkdownText(content = msg.text, format = msg.format, color = MaterialTheme.colorScheme.onSurface)
						}
					}
				}
			}
		}
	}
}

@Composable
fun ReplyScreen(convId: String, requestId: String, viewModel: MainViewModel, navController: NavHostController) {
	val rows by viewModel.conversationRows.collectAsState()
	val row = rows[convId]
	val pending = row?.pendingQuestions?.get(requestId)
	val sender = pending?.sender ?: "user"
	val recap = pending?.questionText ?: "Reply"
	val suggestions = row?.messages?.find { it.first == pending?.msgId }?.second?.suggestions
		?: listOf("Yes", "No", "Maybe", "On it!", "Done")

	val listState = rememberScalingLazyListState()

	val launcher = rememberLauncherForActivityResult(
		ActivityResultContracts.StartActivityForResult()
	) { result ->
		if (result.resultCode == Activity.RESULT_OK) {
			val results = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
			val text = results?.get(0)
			if (!text.isNullOrBlank()) {
				viewModel.submitReplyForConversation(convId, sender, text, requestId)
				navController.popBackStack()
			}
		}
	}

	ScreenScaffold(
		scrollState = listState,
		timeText = { TimeText() },
		scrollIndicator = { ScrollIndicator(state = listState) }
	) {
		ScalingLazyColumn(
			state = listState,
			modifier = Modifier.fillMaxSize(),
			horizontalAlignment = Alignment.CenterHorizontally
		) {
			item {
				Text(
					text = recap,
					style = MaterialTheme.typography.titleSmall,
					textAlign = TextAlign.Center,
					modifier = Modifier.padding(bottom = 4.dp),
				)
			}
			items(suggestions) { text ->
				Button(
					onClick = {
						viewModel.submitReplyForConversation(convId, sender, text, requestId)
						navController.popBackStack()
					},
					modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 2.dp),
					label = { Text(text) }
				)
			}
			item {
				CompactButton(
					onClick = {
						val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
							putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
							putExtra(RecognizerIntent.EXTRA_PROMPT, "Dictate reply")
						}
						launcher.launch(intent)
					},
					modifier = Modifier.padding(top = 8.dp),
					colors = ButtonDefaults.filledTonalButtonColors(),
				) {
					Icon(imageVector = Icons.Default.Mic, contentDescription = "Dictate reply")
				}
			}
		}
	}
}

@Composable
fun MarkdownText(content: String, format: String, color: Color = Color.Unspecified) {
	if (format == "markdown") {
		val textColor = color.toArgb()
		val ctx = LocalContext.current
		val markwon = remember(ctx) {
			io.noties.markwon.Markwon.builder(ctx)
				.usePlugin(io.noties.markwon.html.HtmlPlugin.create())
				.usePlugin(io.noties.markwon.ext.tables.TablePlugin.create(ctx))
				.usePlugin(io.noties.markwon.ext.tasklist.TaskListPlugin.create(ctx))
				.usePlugin(io.noties.markwon.ext.strikethrough.StrikethroughPlugin.create())
				.usePlugin(io.noties.markwon.simple.ext.SimpleExtPlugin.create())
				.usePlugin(object : io.noties.markwon.AbstractMarkwonPlugin() {
					override fun configureConfiguration(builder: io.noties.markwon.MarkwonConfiguration.Builder) {
						builder.linkResolver(object : io.noties.markwon.LinkResolver {
							override fun resolve(v: android.view.View, link: String) {
								if (isAllowedLinkScheme(link)) io.noties.markwon.LinkResolverDef().resolve(v, link)
								// Disallowed scheme: deliberate no-op (see LinkSchemes.kt).
							}
						})
					}
				})
				.build()
		}
		val lastRendered = remember { mutableStateOf<Pair<String, Int>?>(null) }
		AndroidView(
			factory = { ctx ->
				TextView(ctx).apply {
					movementMethod = LinkMovementMethod.getInstance()
					// setMovementMethod auto-flips isClickable/isLongClickable to true, which
					// swallows Card clicks. Reset to false here; links still work via onTouchEvent.
					isClickable = false
					isLongClickable = false
				}
			},
			update = { view ->
				val token = content to textColor
				if (lastRendered.value != token) {
					if (color != Color.Unspecified) view.setTextColor(textColor)
					markwon.setMarkdown(view, content)
					lastRendered.value = token
				}
			}
		)
	} else {
		Text(content, style = MaterialTheme.typography.bodySmall, color = color)
	}
}
