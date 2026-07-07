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
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
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
import io.github.johnjanthony.switchboard.listRowContextRing
import io.github.johnjanthony.switchboard.network.ConversationRow
import io.github.johnjanthony.switchboard.network.WidgetQuota
import io.github.johnjanthony.switchboard.network.WidgetRing
import io.github.johnjanthony.switchboard.network.WidgetStatus

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConversationListScreen(
	rows: List<ConversationRow>,
	hiddenRows: List<ConversationRow>,
	adminRow: ConversationRow?,
	showHidden: Boolean,
	globalAway: Boolean,
	onSessionClick: (ConversationRow) -> Unit,
	onAdminClick: (ConversationRow) -> Unit,
	onToggleShowHidden: () -> Unit,
	onEnterGlobalAway: () -> Unit,
	onExitGlobalAway: () -> Unit,
	onHideConversation: (ConversationRow) -> Unit,
	onUnhideConversation: (ConversationRow) -> Unit,
	onSpawnClick: () -> Unit,
	sessionBadgeCount: Int = 0,
	onSessionsClick: () -> Unit = {},
	resumableByConvId: Map<String, Boolean> = emptyMap(),
	onResumeClick: (conversationId: String) -> Unit = {},
	onCombineClick: (conversationId: String) -> Unit = {},
	onEndClick: (conversationId: String) -> Unit = {},
	rings: Map<String, WidgetRing> = emptyMap(),
	quota: WidgetQuota? = null,
	claudeStatus: WidgetStatus? = null,
	pushedAt: String? = null,
	onCheckStatus: () -> Unit = {},
	onStopStatus: () -> Unit = {},
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
					IconButton(onClick = onSessionsClick) {
						BadgedBox(
							badge = {
								if (sessionBadgeCount > 0) {
									Badge { Text(sessionBadgeCount.toString()) }
								}
							},
						) {
							Icon(Icons.Default.Hub, contentDescription = "Sessions")
						}
					}
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
		Column(modifier = Modifier.fillMaxSize().padding(padding)) {
		WidgetStatusHeader(
			quota = quota,
			status = claudeStatus,
			pushedAt = pushedAt,
			onCheck = onCheckStatus,
			onStop = onStopStatus,
		)
		val displayed = if (showHidden) (rows + hiddenRows) else rows
		val nothingToShow = displayed.isEmpty() && adminRow == null
		if (nothingToShow) {
			Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
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
			LazyColumn(modifier = Modifier.fillMaxSize()) {
				// Admin row stays at the top: it's a system-broadcast pseudo-conversation
				// rendered as a synthetic ConversationRow with id "_admin" (R3).
				if (adminRow != null) {
					item(key = "_admin_row") {
						AdminRow(
							row = adminRow,
							onClick = { onAdminClick(adminRow) },
						)
						Divider()
					}
				}
				items(displayed, key = { it.id }) { row ->
					ConversationRow(
						row = row,
						resumable = resumableByConvId[row.id] ?: false,
						onClick = { onSessionClick(row) },
						onHide = { onHideConversation(row) },
						onUnhide = { onUnhideConversation(row) },
						onResumeClick = onResumeClick,
						onCombineClick = onCombineClick,
						onEndClick = onEndClick,
						contextRing = listRowContextRing(row.members, rings),
					)
					Divider()
				}
			}
		}
		}
	}
}
