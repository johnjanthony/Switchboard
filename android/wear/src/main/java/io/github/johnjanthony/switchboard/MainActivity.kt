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
        intent?.getStringExtra(SwitchboardFirebaseMessagingService.EXTRA_AGENT_ID)?.let { cwdKey ->
            viewModel.selectChannel(cwdKey)
        }
    }
}

@Composable
fun WearApp(viewModel: MainViewModel) {
    val navController = rememberSwipeDismissableNavController()
    val selectedCwdKey by viewModel.selectedCwdKey.collectAsState()

    LaunchedEffect(selectedCwdKey) {
        selectedCwdKey?.let { cwdKey ->
            // Check if we're not already on the message list for this channel
            val currentRoute = navController.currentBackStackEntry?.destination?.route
            if (currentRoute != "message_list/$cwdKey") {
                navController.navigate("message_list/$cwdKey")
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
                composable("message_list/{cwdKey}") { backStackEntry ->
                    val cwdKey = backStackEntry.arguments?.getString("cwdKey") ?: ""
                    MessageListScreen(cwdKey, viewModel, navController)
                }
                composable("reply/{cwdKey}/{requestId}/{sender}") { backStackEntry ->
                    val cwdKey = backStackEntry.arguments?.getString("cwdKey") ?: ""
                    val requestId = backStackEntry.arguments?.getString("requestId") ?: ""
                    val sender = backStackEntry.arguments?.getString("sender") ?: ""
                    ReplyScreen(cwdKey, requestId, sender, viewModel, navController)
                }
            }
        }
    }
}

@Composable
fun ChannelListScreen(viewModel: MainViewModel, navController: NavHostController) {
    val channels by viewModel.channels.collectAsState()
    val awayModeActive by viewModel.globalAway.collectAsState()
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
                    onCheckedChange = { viewModel.requestAwayModeToggle(null, it) },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp),
                    label = { Text("Away Mode") }
                )
            }
            
            items(channels.values.toList().sortedBy { it.cwdKey }, key = { it.cwdKey }) { channel ->
                // Use cwdKey for consistency with the phone app
                val displayName = if (channel.cwdKey.length > 20) {
                    channel.cwdKey.substring(0, 17) + "..."
                } else {
                    channel.cwdKey
                }
                
                val hasPending = channel.pendingQuestions.isNotEmpty()
                val isUnseen = unseenChannels.contains(channel.cwdKey)
                
                android.util.Log.d("MainActivity", "Rendering channel: ${channel.cwdKey} (cwdCanonical=${channel.cwdCanonical}, hidden=${channel.hidden})")
                
                Button(
                    onClick = { 
                        viewModel.selectChannel(channel.cwdKey)
                        navController.navigate("message_list/${channel.cwdKey}") 
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
fun MessageListScreen(cwdKey: String, viewModel: MainViewModel, navController: NavHostController) {
    val channels by viewModel.channels.collectAsState()
    val channel = channels[cwdKey] ?: return
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
                    text = channel.title ?: channel.cwdCanonical,
                    style = MaterialTheme.typography.titleSmall,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(bottom = 8.dp)
                )
            }
            
            items(sortedMessages) { (_, msg) ->
                val isQuestion = msg.type == "question" && msg.response_text == null
                
                Card(
                    onClick = { 
                        if (isQuestion) {
                            navController.navigate("reply/${cwdKey}/${msg.request_id}/${msg.sender}")
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
                            content = msg.text,
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
fun ReplyScreen(cwdKey: String, requestId: String, sender: String, viewModel: MainViewModel, navController: NavHostController) {
    val channels by viewModel.channels.collectAsState()
    val channel = channels[cwdKey]
    val pending = channel?.pendingQuestions?.get(requestId)
    val suggestions = channel?.messages?.find { it.first == pending?.msgId }?.second?.suggestions
        ?: listOf("Yes", "No", "Maybe", "On it!", "Done")
    
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
                        viewModel.submitReply(cwdKey, sender, text)
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
