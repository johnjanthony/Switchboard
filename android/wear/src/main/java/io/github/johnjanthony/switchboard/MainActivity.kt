package io.github.johnjanthony.switchboard

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
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
import io.github.johnjanthony.switchboard.fcm.SwitchboardFirebaseMessagingService
import io.github.johnjanthony.switchboard.network.Channel

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
            WearApp(viewModel)
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

@Composable
fun WearApp(viewModel: MainViewModel) {
    val navController = rememberSwipeDismissableNavController()
    val selectedChannelId by viewModel.selectedChannelId.collectAsState()

    LaunchedEffect(selectedChannelId) {
        selectedChannelId?.let { channelId ->
            // Check if we're not already on the message list for this channel
            val currentRoute = navController.currentBackStackEntry?.destination?.route
            if (currentRoute != "message_list/$channelId") {
                navController.navigate("message_list/$channelId")
            }
        }
    }
    
    MaterialTheme {
        AppScaffold {
            SwipeDismissableNavHost(
                navController = navController,
                startDestination = "channel_list"
            ) {
                composable("channel_list") {
                    ChannelListScreen(viewModel, navController)
                }
                composable("message_list/{channelId}") { backStackEntry ->
                    val channelId = backStackEntry.arguments?.getString("channelId") ?: ""
                    MessageListScreen(channelId, viewModel, navController)
                }
                composable("reply/{channelId}/{msgId}/{requestId}") { backStackEntry ->
                    val channelId = backStackEntry.arguments?.getString("channelId") ?: ""
                    val msgId = backStackEntry.arguments?.getString("msgId") ?: ""
                    val requestId = backStackEntry.arguments?.getString("requestId") ?: ""
                    ReplyScreen(channelId, msgId, requestId, viewModel, navController)
                }
            }
        }
    }
}

@Composable
fun ChannelListScreen(viewModel: MainViewModel, navController: NavHostController) {
    val channels by viewModel.channels.collectAsState()
    val awayModeActive by viewModel.awayModeActive.collectAsState()
    val pendingQuestions by viewModel.pendingQuestions.collectAsState()
    val unseenChannels by viewModel.unseenChannels.collectAsState()
    
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
            item {
                Text(
                    text = "Switchboard",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(bottom = 8.dp)
                )
            }
            
            item {
                SwitchButton(
                    checked = awayModeActive,
                    onCheckedChange = { viewModel.requestAwayModeToggle(it) },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp),
                    label = { Text("Away Mode") }
                )
            }
            
            items(channels.values.toList().sortedBy { it.channelId }, key = { it.channelId }) { channel ->
                // Use channelId for consistency with the phone app
                val displayName = if (channel.channelId.length > 20) {
                    channel.channelId.substring(0, 17) + "..."
                } else {
                    channel.channelId
                }
                
                val hasPending = pendingQuestions.containsKey(channel.channelId)
                val isUnseen = unseenChannels.contains(channel.channelId)
                
                android.util.Log.d("MainActivity", "Rendering channel: ${channel.channelId} (projectKey=${channel.projectKey}, hidden=${channel.hidden})")
                
                Button(
                    onClick = { 
                        viewModel.selectChannel(channel.channelId)
                        navController.navigate("message_list/${channel.channelId}") 
                    },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 2.dp),
                    colors = if (hasPending) ButtonDefaults.filledTonalButtonColors() 
                             else ButtonDefaults.buttonColors(),
                    label = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            if (isUnseen) {
                                Text("• ", color = MaterialTheme.colorScheme.primary)
                            }
                            Text(
                                text = displayName,
                                overflow = TextOverflow.Ellipsis,
                                maxLines = 1
                            )
                        }
                    }
                )
            }
        }
    }
}

@Composable
fun MessageListScreen(channelId: String, viewModel: MainViewModel, navController: NavHostController) {
    val channels by viewModel.channels.collectAsState()
    val channel = channels[channelId] ?: return
    val sortedMessages = remember(channel.messages) {
        channel.messages.sortedBy { it.second.timestamp }
    }
    
    val listState = rememberScalingLazyListState()

    LaunchedEffect(sortedMessages.size) {
        if (sortedMessages.isNotEmpty()) {
            // Scroll to the last item. Index 0 is the header, 
            // so last message is at index sortedMessages.size
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
                    text = channel.projectKey,
                    style = MaterialTheme.typography.titleSmall,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(bottom = 8.dp)
                )
            }
            
            items(sortedMessages) { (_, msg) ->
                val isQuestion = msg.message_type == "question" && msg.response_text == null
                
                Card(
                    onClick = { 
                        if (isQuestion) {
                            navController.navigate("reply/${channelId}/${msg.request_id}/${msg.request_id}")
                        }
                    },
                    modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp)
                ) {
                    Column {
                        Text(
                            text = msg.sender,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.primary
                        )
                        MarkdownText(
                            content = msg.content,
                            format = msg.format,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                        if (isQuestion) {
                            Text(
                                text = "TAP TO REPLY",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.tertiary,
                                modifier = Modifier.padding(top = 4.dp)
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ReplyScreen(channelId: String, msgId: String, requestId: String, viewModel: MainViewModel, navController: NavHostController) {
    val pendingQuestions by viewModel.pendingQuestions.collectAsState()
    val pendingMsg = pendingQuestions[channelId]?.second
    val suggestions = pendingMsg?.suggestions ?: listOf("Yes", "No", "Maybe", "On it!", "Done")
    
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
            item {
                Text("Reply", style = MaterialTheme.typography.titleSmall)
            }
            
            items(suggestions) { text ->
                Button(
                    onClick = { 
                        viewModel.replyToQuestion(channelId, msgId, requestId, text)
                        navController.popBackStack()
                    },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 2.dp),
                    label = { Text(text) }
                )
            }
            
            item {
                Button(
                    onClick = { /* Launch input */ },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 2.dp),
                    colors = ButtonDefaults.filledTonalButtonColors(),
                    label = { Text("Custom...") }
                )
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
        Text(content, style = MaterialTheme.typography.bodySmall, color = color)
    }
}
