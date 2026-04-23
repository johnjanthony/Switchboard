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
import androidx.compose.ui.graphics.Color
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
    val unseenChannels by viewModel.unseenChannels.collectAsState()
    var showSpawnDialog by remember { mutableStateOf(false) }
    var closeTargetChannelId by remember { mutableStateOf<String?>(null) }

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
                        selectedTabIndex = channelList.indexOf(selectedChannelId).coerceAtLeast(0),
                        edgePadding = 8.dp,
                        divider = {}
                    ) {
                        channelList.forEach { channelId ->
                            val hasPending = pendingQuestions.containsKey(channelId)
                            val isUnseen = unseenChannels.contains(channelId)
                            val needsAttention = hasPending || isUnseen
                            
                            val indicatorColor = when {
                                hasPending -> MaterialTheme.colorScheme.error
                                isUnseen -> MaterialTheme.colorScheme.primary
                                else -> Color.Transparent
                            }

                            Tab(
                                selected = channelId == selectedChannelId,
                                onClick = { viewModel.selectChannel(channelId) },
                                text = {
                                    Row(
                                        verticalAlignment = Alignment.CenterVertically,
                                        modifier = Modifier
                                            .padding(vertical = 4.dp)
                                            .then(
                                                if (needsAttention) Modifier
                                                    .border(2.dp, indicatorColor, RoundedCornerShape(16.dp))
                                                    .background(indicatorColor.copy(alpha = 0.1f), RoundedCornerShape(16.dp))
                                                    .padding(horizontal = 8.dp, vertical = 4.dp)
                                                else Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                                            )
                                    ) {
                                        Text(
                                            text = channelId,
                                            maxLines = 1,
                                            style = if (needsAttention) MaterialTheme.typography.labelLarge else MaterialTheme.typography.labelMedium,
                                            color = if (needsAttention) indicatorColor else MaterialTheme.colorScheme.onSurface
                                        )
                                        Spacer(Modifier.width(8.dp))
                                        IconButton(
                                            onClick = { closeTargetChannelId = channelId },
                                            modifier = Modifier.size(16.dp)
                                        ) {
                                            Icon(
                                                Icons.Default.Close,
                                                contentDescription = "Close",
                                                modifier = Modifier.size(12.dp)
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
            onSpawn = { project, prompt, useClaude, useGemini ->
                viewModel.spawnSession(project, prompt, useClaude, useGemini)
                showSpawnDialog = false
            }
        )
    }

    if (closeTargetChannelId != null) {
        AlertDialog(
            onDismissRequest = { closeTargetChannelId = null },
            title = { Text("Close Session") },
            text = { Text("Are you sure you want to close `${closeTargetChannelId}`? The agent will receive a 'back at desk' message.") },
            confirmButton = {
                Button(
                    onClick = {
                        viewModel.closeChannel(closeTargetChannelId!!)
                        closeTargetChannelId = null
                    }
                ) { Text("Close") }
            },
            dismissButton = {
                TextButton(onClick = { closeTargetChannelId = null }) { Text("Cancel") }
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

    Column(modifier.fillMaxSize()) {
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
            Column(Modifier.fillMaxWidth().padding(8.dp)) {
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
    val isHuman = msg.message_type == "human"
    val isDocument = msg.message_type == "document"
    
    // Explicit color mapping
    val bubbleColor = if (isHuman) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant
    val textColor = if (isHuman) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant

    Column(
        Modifier.fillMaxWidth().padding(vertical = 2.dp, horizontal = 4.dp),
        horizontalAlignment = if (isHuman) Alignment.End else Alignment.Start
    ) {
        Text(
            text = msg.sender,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
            modifier = Modifier.padding(start = 6.dp, end = 6.dp, bottom = 2.dp),
        )
        Box(
            Modifier
                .widthIn(max = 320.dp)
                .background(bubbleColor, RoundedCornerShape(12.dp))
                .padding(horizontal = 12.dp, vertical = 8.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f, fill = false)) {
                    if (isDocument) {
                        Text(msg.content, style = MaterialTheme.typography.bodyMedium, color = textColor)
                        if (msg.url != null) {
                            TextButton(onClick = { onDownload(msg.url!!, msg.filename ?: msg.content) }) {
                                Text("Download")
                            }
                        }
                    } else {
                        MarkdownText(msg.content, msg.format, color = textColor)
                    }
                }
                if (isQuestion) {
                    Spacer(Modifier.width(8.dp))
                    Icon(
                        Icons.Default.Call,
                        contentDescription = "Question",
                        modifier = Modifier.size(16.dp).align(Alignment.Top),
                        tint = MaterialTheme.colorScheme.primary.copy(alpha = 0.8f)
                    )
                }
            }
        }
    }
}


@Composable
fun MarkdownText(content: String, format: String, color: androidx.compose.ui.graphics.Color = androidx.compose.ui.graphics.Color.Unspecified) {
    if (format == "markdown") {
        val textColor = color.toArgb()
        AndroidView(
            factory = { ctx ->
                TextView(ctx).apply {
                    movementMethod = LinkMovementMethod.getInstance()
                }
            },
            update = { view ->
                if (color != androidx.compose.ui.graphics.Color.Unspecified) {
                    view.setTextColor(textColor)
                }
                val markwon = io.noties.markwon.Markwon.builder(view.context)
                    .usePlugin(io.noties.markwon.html.HtmlPlugin.create())
                    .build()
                markwon.setMarkdown(view, content)
            }
        )
    } else {
        Text(content, style = MaterialTheme.typography.bodyMedium, color = color)
    }
}


@Composable
fun SpawnSessionDialog(
    onDismiss: () -> Unit,
    onSpawn: (project: String, prompt: String, useClaude: Boolean, useGemini: Boolean) -> Unit,
) {
    var project by remember { mutableStateOf("") }
    var prompt by remember { mutableStateOf("") }
    var useClaude by remember { mutableStateOf(true) }
    var useGemini by remember { mutableStateOf(false) }

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
                    Checkbox(checked = useClaude, onCheckedChange = { useClaude = it })
                    Text("Claude")
                    Spacer(Modifier.width(16.dp))
                    Checkbox(checked = useGemini, onCheckedChange = { useGemini = it })
                    Text("Gemini")
                }
            }
        },
        confirmButton = {
            Button(
                onClick = { onSpawn(project.trim(), prompt.trim(), useClaude, useGemini) },
                enabled = prompt.isNotBlank() && (useClaude || useGemini),
            ) { Text("Spawn") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
}
