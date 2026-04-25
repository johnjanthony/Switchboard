package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Divider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import io.github.johnjanthony.switchboard.AwayModePillChip
import io.github.johnjanthony.switchboard.network.Channel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionListScreen(
	channels: List<Channel>,
	hiddenChannels: List<Channel>,
	showHidden: Boolean,
	globalAway: Boolean,
	cwdOverrides: Map<String, Boolean>,
	onSessionClick: (Channel) -> Unit,
	onToggleShowHidden: () -> Unit,
	onEnterGlobalAway: () -> Unit,
	onExitGlobalAway: () -> Unit,
	onHideChannel: (Channel) -> Unit,
	onUnhideChannel: (Channel) -> Unit,
) {
	var menuExpanded by remember { mutableStateOf(false) }

	Scaffold(
		topBar = {
			TopAppBar(
				title = { Text("Switchboard") },
				actions = {
					AwayModePillChip(
						active = globalAway,
						onLongPress = if (globalAway) onExitGlobalAway else onEnterGlobalAway,
					)
					IconButton(onClick = { menuExpanded = true }) {
						Icon(Icons.Default.MoreVert, contentDescription = "More")
					}
					DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
						HiddenChannelsToggleMenuItem(
							hiddenCount = hiddenChannels.size,
							showHidden = showHidden,
							onToggle = { onToggleShowHidden(); menuExpanded = false },
						)
					}
				},
			)
		},
	) { padding ->
		val displayed = if (showHidden) (channels + hiddenChannels) else channels
		if (displayed.isEmpty()) {
			Box(modifier = Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
				Text("No conversations yet.", color = MaterialTheme.colorScheme.onSurfaceVariant)
			}
		} else {
			LazyColumn(modifier = Modifier.fillMaxSize().padding(padding)) {
				items(displayed, key = { it.cwdKey }) { channel ->
					val awayActive = cwdOverrides[channel.cwdKey] ?: globalAway
					SessionRow(
						channel = channel,
						awayActive = awayActive,
						onClick = { onSessionClick(channel) },
						onHide = { onHideChannel(channel) },
						onUnhide = { onUnhideChannel(channel) },
					)
					Divider()
				}
			}
		}
	}
}
