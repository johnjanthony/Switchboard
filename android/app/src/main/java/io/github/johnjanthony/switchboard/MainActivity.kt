package io.github.johnjanthony.switchboard

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.text.method.LinkMovementMethod
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.RoundedCornerShape
import io.github.johnjanthony.switchboard.fcm.SwitchboardFirebaseMessagingService
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.Channel
import io.github.johnjanthony.switchboard.network.ChannelMessage
import io.github.johnjanthony.switchboard.ui.theme.SwitchboardTheme

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* ignored */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNotificationPermission()
        handleNotificationIntent(intent)
        setContent {
            SwitchboardTheme {
                MainScreen(viewModel)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
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
        intent?.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_AGENT_ID)?.let { channelId ->
            viewModel.selectChannel(channelId)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(viewModel: MainViewModel) {
    val channels by viewModel.channels.collectAsState()
    val selectedChannelId by viewModel.selectedChannelId.collectAsState()
    val pendingQuestions by viewModel.pendingQuestions.collectAsState()
    var showSpawnDialog by remember { mutableStateOf(false) }

    val channelList = channels.keys.toList().sorted()

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("Switchboard") },
                    actions = {
                        IconButton(onClick = { showSpawnDialog = true }) {
                            Icon(Icons.Default.Add, contentDescription = "Spawn")
                        }
                    }
                )
                if (channelList.isNotEmpty()) {
                    ScrollableTabRow(
                        selectedTabIndex = channelList.indexOf(selectedChannelId).coerceAtLeast(0)
                    ) {
                        channelList.forEach { channelId ->
                            val hasPending = pendingQuestions.containsKey(channelId)
                            Tab(
                                selected = channelId == selectedChannelId,
                                onClick = { viewModel.selectChannel(channelId) },
                                text = {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(channelId, maxLines = 1)
                                        if (hasPending) {
                                            Spacer(Modifier.width(4.dp))
                                            Box(
                                                Modifier.size(8.dp)
                                                    .background(MaterialTheme.colorScheme.error, CircleShape)
                                            )
                                        }
                                    }
                                }
                            )
                        }
                    }
                }
            }
        }
    ) { padding ->
        val channel = selectedChannelId?.let { channels[it] }
        if (channel != null) {
            val context = androidx.compose.ui.platform.LocalContext.current
            ChannelView(
                channel = channel,
                pendingQuestion = pendingQuestions[channel.channelId],
                onReply = { msgId, requestId, text ->
                    viewModel.replyToQuestion(channel.channelId, msgId, requestId, text)
                },
                onInject = { text ->
                    viewModel.sendInjectMessage(channel.channelId, text)
                },
                onDownload = { url, name -> viewModel.downloadAndOpenFile(context, url, name) },
                modifier = Modifier.padding(padding),
            )
        } else {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text("No sessions yet. Use /spawn to start one.")
            }
        }
    }

    if (showSpawnDialog) {
        SpawnSessionDialog(
            onDismiss = { showSpawnDialog = false },
            onSpawn = { project, prompt, collab ->
                viewModel.spawnSession(project, prompt, collab)
                showSpawnDialog = false
            }
        )
    }
}


@Composable
fun ChannelView(
    channel: Channel,
    pendingQuestion: Pair<String, ChannelMessage>?,
    onReply: (msgId: String, requestId: String, text: String) -> Unit,
    onInject: (text: String) -> Unit,
    onDownload: (url: String, name: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val listState = rememberLazyListState()
    val messages = channel.messages.sortedBy { it.second.timestamp }
    var replyText by remember { mutableStateOf("") }
    var injectText by remember { mutableStateOf("") }

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.size - 1)
    }

    Column(modifier.fillMaxSize().then(modifier)) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
        ) {
            items(messages, key = { it.first }) { (_, msg) ->
                MessageBubble(msg = msg, onDownload = onDownload)
            }
        }

        // Compose area
        if (pendingQuestion != null) {
            val (msgId, qMsg) = pendingQuestion
            // Sticky reply banner
            Surface(
                color = MaterialTheme.colorScheme.errorContainer,
                modifier = Modifier.fillMaxWidth().padding(4.dp),
                shape = RoundedCornerShape(8.dp),
            ) {
                Column(Modifier.padding(8.dp)) {
                    Text("Reply to: ${qMsg.content}", style = MaterialTheme.typography.bodySmall,
                        maxLines = 2)
                    if (!qMsg.suggestions.isNullOrEmpty()) {
                        LazyRow(modifier = Modifier.padding(vertical = 4.dp)) {
                            items(qMsg.suggestions!!) { label ->
                                OutlinedButton(
                                    onClick = { onReply(msgId, qMsg.request_id ?: "", label) },
                                    modifier = Modifier.padding(end = 4.dp),
                                ) { Text(label) }
                            }
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            value = replyText,
                            onValueChange = { replyText = it },
                            modifier = Modifier.weight(1f),
                            placeholder = { Text("Type reply…") },
                            singleLine = true,
                        )
                        IconButton(onClick = {
                            if (replyText.isNotBlank()) {
                                onReply(msgId, qMsg.request_id ?: "", replyText.trim())
                                replyText = ""
                            }
                        }) {
                            Icon(Icons.Default.Send, contentDescription = "Send reply")
                        }
                    }
                }
            }
        } else if (channel.type == "collab") {
            // Inject input for collab channels when no pending question
            Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = injectText,
                    onValueChange = { injectText = it },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("Inject message to agents…") },
                    singleLine = true,
                )
                IconButton(onClick = {
                    if (injectText.isNotBlank()) {
                        onInject(injectText.trim())
                        injectText = ""
                    }
                }) {
                    Icon(Icons.Default.Send, contentDescription = "Inject")
                }
            }
        }
    }
}


@Composable
fun MessageBubble(
    msg: ChannelMessage,
    onDownload: (url: String, name: String) -> Unit,
) {
    val isQuestion = msg.message_type == "question"
    val isDocument = msg.message_type == "document"
    val bubbleColor = when {
        isQuestion -> MaterialTheme.colorScheme.errorContainer
        else -> MaterialTheme.colorScheme.surfaceVariant
    }
    val borderMod = if (isQuestion)
        Modifier.border(1.dp, MaterialTheme.colorScheme.error, RoundedCornerShape(8.dp))
    else Modifier

    Column(
        Modifier.fillMaxWidth().padding(vertical = 2.dp, horizontal = 4.dp)
    ) {
        Text(
            text = msg.sender,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 4.dp, bottom = 2.dp),
        )
        Box(
            Modifier
                .fillMaxWidth()
                .then(borderMod)
                .background(bubbleColor, RoundedCornerShape(8.dp))
                .padding(horizontal = 12.dp, vertical = 8.dp)
        ) {
            if (isDocument) {
                Column {
                    Text(msg.content, style = MaterialTheme.typography.bodyMedium)
                    if (msg.url != null) {
                        TextButton(onClick = { onDownload(msg.url!!, msg.content) }) {
                            Text("Download")
                        }
                    }
                }
            } else {
                MarkdownText(msg.content, msg.format)
            }
        }
    }
}


@Composable
fun MarkdownText(content: String, format: String) {
    if (format == "markdown") {
        AndroidView(
            factory = { ctx ->
                TextView(ctx).apply {
                    movementMethod = LinkMovementMethod.getInstance()
                }
            },
            update = { view ->
                val markwon = io.noties.markwon.Markwon.builder(view.context)
                    .usePlugin(io.noties.markwon.html.HtmlPlugin.create())
                    .build()
                markwon.setMarkdown(view, content)
            }
        )
    } else {
        Text(content, style = MaterialTheme.typography.bodyMedium)
    }
}


@Composable
fun SpawnSessionDialog(
    onDismiss: () -> Unit,
    onSpawn: (project: String, prompt: String, collab: Boolean) -> Unit,
) {
    var project by remember { mutableStateOf("") }
    var prompt by remember { mutableStateOf("") }
    var collab by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Spawn Session") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = project,
                    onValueChange = { project = it },
                    label = { Text("Project (optional)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = prompt,
                    onValueChange = { prompt = it },
                    label = { Text("Initial Prompt / Instructions") },
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 2,
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Checkbox(checked = collab, onCheckedChange = { collab = it })
                    Text("Collab mode (2 agents)")
                }
            }
        },
        confirmButton = {
            Button(
                onClick = { onSpawn(project.trim(), prompt.trim(), collab) },
                enabled = prompt.isNotBlank(),
            ) { Text("Spawn") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
}
