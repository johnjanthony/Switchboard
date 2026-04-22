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
import io.github.johnjanthony.switchboard.network.CollabMessage
import io.github.johnjanthony.switchboard.network.CollabSession
import io.github.johnjanthony.switchboard.network.CollabSessionMeta
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
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.MainViewModel
import io.github.johnjanthony.switchboard.network.Question
import io.github.johnjanthony.switchboard.ui.theme.SwitchboardTheme

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* permission granted or denied — notifications work either way if already granted */ }

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
        intent?.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_AGENT_ID)?.let { agentId ->
            viewModel.selectAgent(agentId)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(viewModel: MainViewModel) {
    val questions by viewModel.questions
    val history by viewModel.history
    val selectedAgentId by viewModel.selectedAgentId
    val waitingAgents by viewModel.waitingAgents
    var showSpawnDialog by remember { mutableStateOf(false) }
    var closeTargetAgentId by remember { mutableStateOf<String?>(null) }

    val agents = history.keys.toList().sorted()
    val collabSessions by viewModel.collabSessions.collectAsState()
    val pendingSessionQuestions by viewModel.pendingSessionQuestions.collectAsState()

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("Switchboard") },
                    actions = {
                        IconButton(onClick = { showSpawnDialog = true }) {
                            Icon(Icons.Default.Add, contentDescription = "New Session")
                        }
                    }
                )
                val allTabIds = agents + collabSessions.keys.toList().sorted()
                if (allTabIds.isNotEmpty()) {
                    ScrollableTabRow(
                        selectedTabIndex = allTabIds.indexOf(selectedAgentId).coerceAtLeast(0),
                        edgePadding = 16.dp,
                        containerColor = MaterialTheme.colorScheme.surface,
                        contentColor = MaterialTheme.colorScheme.primary
                    ) {
                        agents.forEach { agentId ->
                            val isWaiting = waitingAgents.contains(agentId)
                            Tab(
                                selected = selectedAgentId == agentId,
                                onClick = { viewModel.selectAgent(agentId) },
                                text = {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        if (isWaiting) {
                                            Box(
                                                modifier = Modifier
                                                    .size(8.dp)
                                                    .padding(end = 4.dp)
                                                    .background(MaterialTheme.colorScheme.error, CircleShape)
                                            )
                                        }
                                        Text(agentId)
                                        Spacer(modifier = Modifier.width(8.dp))
                                        IconButton(
                                            onClick = { closeTargetAgentId = agentId },
                                            modifier = Modifier.size(16.dp)
                                        ) {
                                            Icon(
                                                Icons.Default.Close,
                                                contentDescription = "Close Tab",
                                                modifier = Modifier.size(12.dp)
                                            )
                                        }
                                    }
                                }
                            )
                        }
                        collabSessions.values.sortedBy { it.sessionId }.forEach { session ->
                            val projectName = session.sessionId.substringBeforeLast("-").let {
                                if (it.contains("-")) it.substringBefore("-") else it
                            }
                            Tab(
                                selected = selectedAgentId == session.sessionId,
                                onClick = { viewModel.selectAgent(session.sessionId) },
                                text = { Text("$projectName [collab]") }
                            )
                        }
                    }
                }
            }
        },
        floatingActionButton = {
            if (agents.isEmpty()) {
                ExtendedFloatingActionButton(
                    onClick = { showSpawnDialog = true },
                    icon = { Icon(Icons.Default.Add, "Add") },
                    text = { Text("New Session") }
                )
            }
        }
    ) { padding ->
        Box(modifier = Modifier.padding(padding)) {
            val selectedSession = collabSessions[selectedAgentId]
            if (selectedSession != null) {
                SessionChatView(
                    session = selectedSession,
                    pendingQuestions = pendingSessionQuestions[selectedAgentId!!] ?: emptyList(),
                    onReply = { msgId, requestId, text ->
                        viewModel.replyToSessionQuestion(selectedAgentId!!, msgId, requestId, text)
                    },
                    onInject = { text ->
                        viewModel.sendInjectMessage(selectedAgentId!!, text)
                    },
                )
            } else if (selectedAgentId != null) {
                val context = androidx.compose.ui.platform.LocalContext.current
                ChatView(
                    agentId = selectedAgentId!!,
                    messages = history[selectedAgentId] ?: emptyList(),
                    activeQuestions = questions.filter { it.agent_id == selectedAgentId },
                    onAnswer = { id, text -> viewModel.answerQuestion(id, selectedAgentId!!, text) },
                    onTyping = { viewModel.setUserTyping(it) },
                    onOpenDocument = { url, name -> viewModel.downloadAndOpenFile(context, url, name) }
                )
            } else {
                Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("No active sessions. Start a new one!", style = MaterialTheme.typography.bodyLarge)
                }
            }
        }
    }

    if (showSpawnDialog) {
        SpawnSessionDialog(
            onDismiss = { showSpawnDialog = false },
            onSpawn = { project, prompt, agents, relay ->
                viewModel.spawnSession(project, prompt, agents, relay)
                showSpawnDialog = false
            }
        )
    }

    if (closeTargetAgentId != null) {
        AlertDialog(
            onDismissRequest = { closeTargetAgentId = null },
            title = { Text("Close Session") },
            text = { Text("Are you sure you want to close the session for ${closeTargetAgentId}?") },
            confirmButton = {
                Button(
                    onClick = {
                        viewModel.closeSession(closeTargetAgentId!!)
                        closeTargetAgentId = null
                    }
                ) {
                    Text("Close")
                }
            },
            dismissButton = {
                TextButton(onClick = { closeTargetAgentId = null }) {
                    Text("Cancel")
                }
            }
        )
    }
}

@Composable
fun ChatView(
    agentId: String,
    messages: List<Message>,
    activeQuestions: List<Question>,
    onAnswer: (String, String) -> Unit,
    onTyping: (Boolean) -> Unit,
    onOpenDocument: (String, String) -> Unit
) {
    val listState = rememberLazyListState()
    
    // Auto-scroll to bottom when new messages arrive
    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.size - 1)
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Box(modifier = Modifier.weight(1f).background(MaterialTheme.colorScheme.surfaceVariant)) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                items(messages) { message ->
                    MessageBubble(message, onOpenDocument)
                }
            }
        }

        // Only show input if there's an active question for this agent
        val latestQuestion = activeQuestions.lastOrNull()
        if (latestQuestion != null) {
            ChatInput(
                question = latestQuestion,
                onAnswer = onAnswer,
                onTyping = onTyping
            )
        }
    }
}

@Composable
fun MessageBubble(
    message: Message,
    onOpenDocument: (String, String) -> Unit
) {
    val isMe = message.sender == "Me"
    val alignment = if (isMe) Alignment.End else Alignment.Start
    val color = if (isMe) MaterialTheme.colorScheme.primaryContainer else androidx.compose.ui.graphics.Color.Black
    val textColor = if (isMe) MaterialTheme.colorScheme.onPrimaryContainer else androidx.compose.ui.graphics.Color.White

    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalAlignment = alignment) {
        Surface(
            shape = MaterialTheme.shapes.medium,
            color = color,
            tonalElevation = 1.dp
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                if (message.documentUrl != null && message.fileName != null) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Email, contentDescription = null, tint = textColor)
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = message.fileName,
                            style = MaterialTheme.typography.bodyLarge,
                            color = textColor,
                            modifier = Modifier.weight(1f, fill = false)
                        )
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    if (message.text.isNotEmpty() && !message.text.startsWith("Sent a document")) {
                        Text(
                            text = message.text,
                            style = MaterialTheme.typography.bodyMedium,
                            color = textColor
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                    }
                    Button(
                        onClick = { onOpenDocument(message.documentUrl, message.fileName) },
                        modifier = Modifier.align(Alignment.End)
                    ) {
                        Text("Open")
                    }
                } else if (message.format == "markdown" || message.format == "html") {
                    AndroidView(
                        factory = { ctx ->
                            val markwon = io.noties.markwon.Markwon.builder(ctx)
                                .usePlugin(io.noties.markwon.html.HtmlPlugin.create())
                                .usePlugin(object : io.noties.markwon.AbstractMarkwonPlugin() {
                                    override fun configureTheme(builder: io.noties.markwon.core.MarkwonTheme.Builder) {
                                        builder
                                            .codeTextColor(android.graphics.Color.argb(255, 0x4D, 0xD0, 0xE1))
                                            .codeBackgroundColor(android.graphics.Color.argb(255, 0x2D, 0x2D, 0x2D))
                                    }
                                })
                                .build()
                            TextView(ctx).apply {
                                textSize = 16f
                                movementMethod = LinkMovementMethod.getInstance()
                                tag = markwon
                            }
                        },
                        update = { tv ->
                            val markwon = tv.tag as io.noties.markwon.Markwon
                            markwon.setMarkdown(tv, message.text)
                            tv.setTextColor(textColor.toArgb())
                        }
                    )
                } else {
                    Text(
                        text = message.text,
                        style = MaterialTheme.typography.bodyLarge,
                        color = textColor
                    )
                }
            }
        }
        Text(
            text = message.sender,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f),
            modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp)
        )
    }
}

@Composable
fun ChatInput(
    question: Question,
    onAnswer: (String, String) -> Unit,
    onTyping: (Boolean) -> Unit
) {
    var replyText by remember { mutableStateOf("") }
    var showSuggestions by remember { mutableStateOf(true) }

    LaunchedEffect(replyText) {
        onTyping(replyText.isNotBlank())
    }

    Surface(
        tonalElevation = 8.dp,
        shadowElevation = 8.dp
    ) {
        Column(modifier = Modifier.padding(bottom = 16.dp, start = 16.dp, end = 16.dp, top = 8.dp)) {
            val suggestions = question.suggestions
            if (suggestions != null && suggestions.isNotEmpty()) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text(
                        "Suggestions",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.secondary
                    )
                    IconButton(
                        onClick = { showSuggestions = !showSuggestions },
                        modifier = Modifier.size(24.dp)
                    ) {
                        Icon(
                            if (showSuggestions) Icons.Default.ArrowDropDown else Icons.Default.ArrowDropDown,
                            contentDescription = "Toggle Suggestions"
                        )
                    }
                }

                if (showSuggestions) {
                    LazyRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        modifier = Modifier.padding(vertical = 4.dp).fillMaxWidth()
                    ) {
                        items(suggestions) { suggestion ->
                            AssistChip(
                                onClick = { onAnswer(question.request_id, suggestion) },
                                label = { Text(suggestion) },
                                colors = AssistChipDefaults.assistChipColors(
                                    containerColor = MaterialTheme.colorScheme.secondaryContainer
                                )
                            )
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(4.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = replyText,
                    onValueChange = { replyText = it },
                    label = { Text("Reply to ${question.agent_id}...") },
                    modifier = Modifier.weight(1f),
                    maxLines = 4
                )
                Spacer(modifier = Modifier.width(8.dp))
                IconButton(
                    onClick = {
                        onAnswer(question.request_id, replyText)
                        replyText = ""
                    },
                    enabled = replyText.isNotBlank()
                ) {
                    Icon(Icons.Default.Send, contentDescription = "Send", tint = MaterialTheme.colorScheme.primary)
                }
            }
        }
    }
}

@Composable
fun SpawnSessionDialog(
    onDismiss: () -> Unit,
    onSpawn: (String, String, Int, Boolean) -> Unit
) {
    var project by remember { mutableStateOf("") }
    var prompt by remember { mutableStateOf("") }
    var agents by remember { mutableStateOf(1) }
    var relay by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New Session") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                OutlinedTextField(
                    value = project,
                    onValueChange = { project = it },
                    label = { Text("Project (optional)") },
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = prompt,
                    onValueChange = { prompt = it },
                    label = { Text("Initial Prompt / Instructions") },
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 3
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text("Agents:", style = MaterialTheme.typography.bodyMedium)
                    IconButton(
                        onClick = {
                            if (agents > 1) {
                                agents--
                                if (agents == 1) relay = false
                            }
                        },
                        enabled = agents > 1
                    ) {
                        Text("−", style = MaterialTheme.typography.titleLarge)
                    }
                    Text(
                        text = agents.toString(),
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.defaultMinSize(minWidth = 24.dp),
                    )
                    IconButton(onClick = { agents++ }) {
                        Text("+", style = MaterialTheme.typography.titleLarge)
                    }
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Checkbox(
                        checked = relay,
                        onCheckedChange = { if (agents > 1) relay = it },
                        enabled = agents > 1
                    )
                    Text(
                        text = "Relay messages between agents",
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (agents > 1) MaterialTheme.colorScheme.onSurface
                                else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                    )
                }
            }
        },
        confirmButton = {
            Button(
                onClick = { onSpawn(project, prompt, agents, relay) },
                enabled = prompt.isNotBlank()
            ) {
                Text("Spawn")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Cancel")
            }
        }
    )
}

@Composable
fun SessionChatView(
    session: CollabSession,
    pendingQuestions: List<Pair<String, CollabMessage>>,
    onReply: (msgId: String, requestId: String, text: String) -> Unit,
    onInject: (text: String) -> Unit,
) {
    val agentLabels = mapOf(
        session.meta.agent_ids.getOrNull(0) to "Agent 1",
        session.meta.agent_ids.getOrNull(1) to "Agent 2",
    )
    val listState = rememberLazyListState()
    LaunchedEffect(session.messages.size) {
        if (session.messages.isNotEmpty()) {
            listState.animateScrollToItem(session.messages.size - 1)
        }
    }
    Column(Modifier.fillMaxSize()) {
        Box(modifier = Modifier.weight(1f).background(MaterialTheme.colorScheme.surfaceVariant)) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(session.messages, key = { it.first }) { (_, msg) ->
                    SessionMessageBubble(msg, agentLabels[msg.speaker] ?: msg.speaker)
                }
            }
        }
        SessionComposeArea(
            pendingQuestion = pendingQuestions.firstOrNull()?.second,
            pendingMsgId = pendingQuestions.firstOrNull()?.first,
            agentLabels = agentLabels,
            onReply = onReply,
            onInject = onInject,
        )
    }
}

@Composable
fun SessionMessageBubble(msg: CollabMessage, speakerLabel: String) {
    val isHuman = msg.speaker == "human"
    val isAskHuman = msg.type == "ask_human"
    val bubbleColor = when {
        isHuman -> MaterialTheme.colorScheme.primaryContainer
        isAskHuman -> MaterialTheme.colorScheme.errorContainer
        else -> androidx.compose.ui.graphics.Color.Black
    }
    val borderMod = if (isAskHuman)
        Modifier.border(2.dp, MaterialTheme.colorScheme.error, RoundedCornerShape(8.dp))
    else Modifier

    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp, vertical = 4.dp),
        horizontalArrangement = if (isHuman) Arrangement.End else Arrangement.Start,
    ) {
        Column(
            Modifier
                .then(borderMod)
                .background(bubbleColor, RoundedCornerShape(8.dp))
                .padding(8.dp)
                .widthIn(max = 280.dp)
        ) {
            if (!isHuman) {
                Text(
                    speakerLabel,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            Text(
                msg.content,
                style = MaterialTheme.typography.bodyLarge,
                color = if (isHuman) MaterialTheme.colorScheme.onPrimaryContainer
                        else androidx.compose.ui.graphics.Color.White,
            )
        }
    }
}

@Composable
fun SessionComposeArea(
    pendingQuestion: CollabMessage?,
    pendingMsgId: String?,
    agentLabels: Map<String?, String>,
    onReply: (msgId: String, requestId: String, text: String) -> Unit,
    onInject: (text: String) -> Unit,
) {
    var text by remember { mutableStateOf("") }
    val isReplying = pendingQuestion != null

    Surface(tonalElevation = 8.dp, shadowElevation = 8.dp) {
        Column {
            if (isReplying) {
                val agentLabel = agentLabels[pendingQuestion!!.speaker] ?: pendingQuestion.speaker
                Surface(color = MaterialTheme.colorScheme.errorContainer) {
                    Text(
                        "$agentLabel is waiting for your reply",
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(8.dp),
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                }
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 8.dp, vertical = 4.dp),
                    shape = RoundedCornerShape(4.dp),
                ) {
                    Column(Modifier.padding(8.dp)) {
                        Text(
                            agentLabel,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Text(
                            pendingQuestion.content.take(120) +
                                if (pendingQuestion.content.length > 120) "…" else "",
                            style = MaterialTheme.typography.bodySmall,
                            maxLines = 3,
                        )
                    }
                }
            }
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    placeholder = {
                        Text(
                            if (isReplying) {
                                val agentLabel = agentLabels[pendingQuestion!!.speaker] ?: pendingQuestion.speaker
                                "Reply to $agentLabel…"
                            } else "Inject into conversation…"
                        )
                    },
                    modifier = Modifier.weight(1f),
                    maxLines = 4,
                )
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = {
                        val trimmed = text.trim()
                        if (trimmed.isNotEmpty()) {
                            if (isReplying) {
                                onReply(pendingMsgId!!, pendingQuestion!!.request_id ?: "", trimmed)
                            } else {
                                onInject(trimmed)
                            }
                            text = ""
                        }
                    },
                ) { Text("Send") }
            }
        }
    }
}

@Preview
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreenPreview() {
    SwitchboardTheme {
        Scaffold(
            topBar = {
                Column {
                    CenterAlignedTopAppBar(title = { Text("Switchboard") })
                    ScrollableTabRow(selectedTabIndex = 0) {
                        Tab(
                            selected = true,
                            onClick = {},
                            text = {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Box(
                                        modifier = Modifier
                                            .size(8.dp)
                                            .padding(end = 4.dp)
                                            .background(MaterialTheme.colorScheme.error, CircleShape)
                                    )
                                    Text("Agent1")
                                    Spacer(modifier = Modifier.width(8.dp))
                                    IconButton(
                                        onClick = { },
                                        modifier = Modifier.size(16.dp)
                                    ) {
                                        Icon(
                                            Icons.Default.Close,
                                            contentDescription = "Close Tab",
                                            modifier = Modifier.size(12.dp)
                                        )
                                    }
                                }
                            }
                        )
                    }
                }
            }
        ) { padding ->
            Box(modifier = Modifier.padding(padding)) {
                Column(modifier = Modifier.fillMaxSize()) {
                    Box(modifier = Modifier.weight(1f)) {
                        LazyColumn(
                            modifier = Modifier.fillMaxSize(),
                            contentPadding = PaddingValues(16.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            item {
                                MessageBubble(Message("1", "Hello?", "Agent1", 0L, true), { _, _ -> })
                            }
                            item {
                                MessageBubble(Message("2", "I answered this previously", "Me", 0L, false), { _, _ -> })
                            }
                        }
                    }
                    ChatInput(
                        question = Question(request_id = "1", agent_id = "Agent1", question = "How are you?", suggestions = listOf("Good", "Busy", "Tired", "Excited")),
                        onAnswer = { _, _ -> },
                        onTyping = {}
                    )
                }
            }
        }
    }
}
