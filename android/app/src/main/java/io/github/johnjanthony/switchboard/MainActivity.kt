package io.github.johnjanthony.switchboard

import android.os.Bundle
import android.text.method.LinkMovementMethod
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.text.HtmlCompat
import androidx.compose.foundation.background
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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            SwitchboardTheme {
                MainScreen(viewModel)
            }
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
                if (agents.isNotEmpty()) {
                    ScrollableTabRow(
                        selectedTabIndex = agents.indexOf(selectedAgentId).coerceAtLeast(0),
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
            if (selectedAgentId != null) {
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
            onSpawn = { project, prompt ->
                viewModel.spawnSession(project, prompt)
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
    onSpawn: (String, String) -> Unit
) {
    var project by remember { mutableStateOf("") }
    var prompt by remember { mutableStateOf("") }

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
            }
        },
        confirmButton = {
            Button(
                onClick = { onSpawn(project, prompt) },
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
