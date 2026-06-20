package io.github.johnjanthony.switchboard

import android.Manifest
import android.app.Activity
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
import io.github.johnjanthony.switchboard.fcm.SwitchboardFirebaseMessagingService
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
		intent?.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_AGENT_ID)?.let { value ->
			pendingDeepLinkConvId.value = value
		}
		intent?.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_MESSAGE_ID)?.let { messageId ->
			viewModel.setPendingDeepLinkMessageId(messageId)
		}
	}
}

@Composable
fun WearApp(viewModel: MainViewModel, pendingDeepLinkConvId: MutableState<String?>) {
	val context = LocalContext.current
	val navController = rememberSwipeDismissableNavController()
	val conversationRows by viewModel.conversationRows.collectAsState()

	// Automatic Google Sign-In on first start.
	LaunchedEffect(Unit) {
		if (FirebaseAuth.getInstance().currentUser == null) {
			GoogleAuthHelper.signInWithGoogle(context)
		}
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
					ConversationListScreen(viewModel, navController)
				}
				composable("message_view/{convId}") { backStackEntry ->
					val convId = backStackEntry.arguments?.getString("convId") ?: ""
					MessageViewScreen(convId, viewModel, navController)
				}
				composable("reply/{convId}/{requestId}/{sender}") { backStackEntry ->
					val convId = backStackEntry.arguments?.getString("convId") ?: ""
					val requestId = backStackEntry.arguments?.getString("requestId") ?: ""
					val sender = backStackEntry.arguments?.getString("sender") ?: ""
					ReplyScreen(convId, requestId, sender, viewModel, navController)
				}
			}
		}
	}
}

@Composable
fun ConversationListScreen(viewModel: MainViewModel, navController: NavHostController) {
	val rows by viewModel.conversationRows.collectAsState()
	val (needsReply, others) = remember(rows) { partitionConversationsForWatch(rows.values) }
	val signedIn = rememberFirebaseSignedIn()

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
				item { WearListEmptyState(signedIn) }
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

/** Observe Firebase auth state reactively so the empty list can distinguish
 * "not signed in / connecting" from "signed in but no conversations". */
@Composable
private fun rememberFirebaseSignedIn(): Boolean {
	val auth = remember { FirebaseAuth.getInstance() }
	var signedIn by remember { mutableStateOf(auth.currentUser != null) }
	DisposableEffect(auth) {
		// IdTokenListener, not AuthStateListener: a saved-login restore notifies only
		// id-token listeners, so an AuthStateListener can stay silent and leave this
		// stuck at "not signed in". IdTokenListener fires on sign-in / restore / refresh.
		val listener = FirebaseAuth.IdTokenListener { signedIn = it.currentUser != null }
		auth.addIdTokenListener(listener)
		onDispose { auth.removeIdTokenListener(listener) }
	}
	return signedIn
}

@Composable
private fun WearListEmptyState(signedIn: Boolean) {
	Column(
		modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 24.dp),
		horizontalAlignment = Alignment.CenterHorizontally,
	) {
		Text(
			text = if (signedIn) "No conversations" else "Connecting...",
			style = MaterialTheme.typography.titleSmall,
			textAlign = TextAlign.Center,
		)
		Text(
			text = if (signedIn) "Conversations will appear here." else "Waiting for Google sign-in.",
			style = MaterialTheme.typography.bodySmall,
			color = MaterialTheme.colorScheme.onSurfaceVariant,
			textAlign = TextAlign.Center,
			modifier = Modifier.padding(top = 4.dp),
		)
	}
}

@Composable
fun MessageViewScreen(convId: String, viewModel: MainViewModel, navController: NavHostController) {
	val rows by viewModel.conversationRows.collectAsState()
	val row = rows[convId]
	val pendingMessageId by viewModel.pendingDeepLinkMessageId.collectAsState()
	val listState = rememberScalingLazyListState()

	// Opening a conversation clears its unread badge (sentinel-guarded for _admin).
	LaunchedEffect(convId) { viewModel.selectConversation(convId) }

	if (row == null) {
		// Cold-start race or unknown conv: show a loading state, do not dead-end (F-90).
		Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
			Text("Loading...", style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center)
		}
		return
	}

	val sortedMessages = remember(row.messages) { row.messages.sortedBy { it.second.timestamp } }
	val answeredMsgIds = remember(sortedMessages) {
		sortedMessages.mapNotNull { (_, m) -> m.attached_to_msg_id }.toSet()
	}

	LaunchedEffect(pendingMessageId, sortedMessages.size) {
		val targetId = pendingMessageId
		if (targetId != null) {
			val idx = sortedMessages.indexOfFirst { it.first == targetId }
			if (idx >= 0) {
				listState.scrollToItem(idx + 1)  // index 0 is the header
				viewModel.clearPendingDeepLinkMessageId()
			}
			return@LaunchedEffect
		}
		if (sortedMessages.isNotEmpty()) {
			listState.scrollToItem(sortedMessages.size)
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
			items(sortedMessages) { (msgId, msg) ->
				val answerable = isAnswerableQuestion(msg.type, msgId, answeredMsgIds, msg.cancelled, msg.rejected)
				if (answerable) {
					Card(
						onClick = { navController.navigate("reply/${convId}/${msg.request_id}/${msg.sender}") },
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
fun ReplyScreen(convId: String, requestId: String, sender: String, viewModel: MainViewModel, navController: NavHostController) {
	val rows by viewModel.conversationRows.collectAsState()
	val row = rows[convId]
	val pending = row?.pendingQuestions?.get(requestId)
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
		AndroidView(
			factory = { ctx ->
				TextView(ctx).apply {
					movementMethod = LinkMovementMethod.getInstance()
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
					.build()
				markwon.setMarkdown(view, content)
			}
		)
	} else {
		Text(content, style = MaterialTheme.typography.bodySmall, color = color)
	}
}
