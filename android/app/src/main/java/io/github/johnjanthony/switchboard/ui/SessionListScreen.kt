package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Divider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import io.github.johnjanthony.switchboard.AwayModePillChip
import io.github.johnjanthony.switchboard.network.Channel
import io.github.johnjanthony.switchboard.network.ConversationRow

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionListScreen(
	rows: List<ConversationRow>,
	hiddenRows: List<ConversationRow>,
	adminChannel: Channel?,
	showHidden: Boolean,
	globalAway: Boolean,
	onSessionClick: (ConversationRow) -> Unit,
	onAdminClick: (Channel) -> Unit,
	onToggleShowHidden: () -> Unit,
	onEnterGlobalAway: () -> Unit,
	onExitGlobalAway: () -> Unit,
	onHideConversation: (ConversationRow) -> Unit,
	onUnhideConversation: (ConversationRow) -> Unit,
	onSpawnClick: () -> Unit,
	onResumeClick: (conversationId: String) -> Unit = {},
	onCombineClick: (conversationId: String) -> Unit = {},
	onEndClick: (conversationId: String) -> Unit = {},
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
					IconButton(onClick = onSpawnClick) {
						Icon(Icons.Default.Add, contentDescription = "Spawn")
					}
					IconButton(onClick = { menuExpanded = true }) {
						Icon(Icons.Default.MoreVert, contentDescription = "More")
					}
					DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
						HiddenChannelsToggleMenuItem(
							hiddenCount = hiddenRows.size,
							showHidden = showHidden,
							onToggle = { onToggleShowHidden(); menuExpanded = false },
						)
					}
				},
			)
		},
	) { padding ->
		val displayed = if (showHidden) (rows + hiddenRows) else rows
		val nothingToShow = displayed.isEmpty() && adminChannel == null
		if (nothingToShow) {
			Box(modifier = Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
				Column(
					horizontalAlignment = Alignment.CenterHorizontally,
					verticalArrangement = Arrangement.spacedBy(12.dp),
				) {
					Text("No conversations yet.", color = MaterialTheme.colorScheme.onSurfaceVariant)
					Button(onClick = onSpawnClick) {
						Text("Spawn new session")
					}
				}
			}
		} else {
			LazyColumn(modifier = Modifier.fillMaxSize().padding(padding)) {
				// Admin row stays at the top: it's a system-broadcast pseudo-conversation that
				// doesn't fit the conversation model. Rendered via the legacy SessionRow signature
				// (taking a Channel) since admin has no ConversationSummary to back a Row.
				if (adminChannel != null) {
					item(key = "_admin_row") {
						AdminRow(
							channel = adminChannel,
							onClick = { onAdminClick(adminChannel) },
						)
						Divider()
					}
				}
				items(displayed, key = { it.id }) { row ->
					SessionRow(
						row = row,
						awayActive = globalAway,
						onClick = { onSessionClick(row) },
						onHide = { onHideConversation(row) },
						onUnhide = { onUnhideConversation(row) },
						onResumeClick = onResumeClick,
						onCombineClick = onCombineClick,
						onEndClick = onEndClick,
					)
					Divider()
				}
			}
		}
	}
}
