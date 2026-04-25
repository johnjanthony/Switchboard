package io.github.johnjanthony.switchboard

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import androidx.wear.compose.material3.*
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.items
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.navigation.SwipeDismissableNavHost
import androidx.wear.compose.navigation.composable
import androidx.wear.compose.navigation.rememberSwipeDismissableNavController
import io.github.johnjanthony.switchboard.network.Channel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            WearApp()
        }
    }
}

@Composable
fun WearApp(viewModel: MainViewModel = viewModel()) {
    val navController = rememberSwipeDismissableNavController()
    
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
                    text = channel.projectKey,
                    style = MaterialTheme.typography.titleSmall,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(bottom = 8.dp)
                )
            }
            
            items(channel.messages.reversed()) { (_, msg) ->
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
                        Text(
                            text = msg.content,
                            style = MaterialTheme.typography.bodySmall
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
            
            val suggestions = listOf("Yes", "No", "Maybe", "On it!", "Done")
            
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
