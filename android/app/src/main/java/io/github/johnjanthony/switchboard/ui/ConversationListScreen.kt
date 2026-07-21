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
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Divider
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.foundation.layout.Row
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
import io.github.johnjanthony.switchboard.ui.theme.Jade

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
	authState: io.github.johnjanthony.switchboard.AuthUiState = io.github.johnjanthony.switchboard.AuthUiState.SIGNED_IN,
	onRetrySignIn: () -> Unit = {},
) {
	var menuExpanded by remember { mutableStateOf(false) }
	var showQuotaPopup by remember { mutableStateOf(false) }

	Scaffold(
		topBar = {
			CenterAlignedTopAppBar(
				navigationIcon = {
					Row(verticalAlignment = Alignment.CenterVertically) {
						Box {
							IconButton(onClick = { menuExpanded = true }) {
								Icon(Icons.Default.MoreVert, contentDescription = "More")
							}
							DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
								DropdownMenuItem(
									text = { Text("Check status") },
									onClick = { onCheckStatus(); menuExpanded = false }
								)
								if (claudeStatus?.watchState == "watching") {
									DropdownMenuItem(
										text = { Text("Stop status watch") },
										onClick = { onStopStatus(); menuExpanded = false }
									)
								}
								Divider()
								DropdownMenuItem(
									text = { Text("Sessions") },
									onClick = { onSessionsClick(); menuExpanded = false }
								)
								HiddenChannelsToggleMenuItem(
									hiddenCount = hiddenRows.size,
									showHidden = showHidden,
									onToggle = { onToggleShowHidden(); menuExpanded = false },
								)
							}
						}
						IconButton(onClick = onSpawnClick) {
							Icon(Icons.Default.Add, contentDescription = "Spawn")
						}
					}
				},
				title = {},
				actions = {
					io.github.johnjanthony.switchboard.OnlineOfflinePillChip(status = claudeStatus)
					AwayModePillChip(
						active = globalAway,
						onLongPress = if (globalAway) onExitGlobalAway else onEnterGlobalAway,
					)
				},
			)
		},
	) { padding ->
		Column(modifier = Modifier.fillMaxSize().padding(padding)) {
		WidgetStatusHeader(
			quota = quota,
			pushedAt = pushedAt,
			onClick = { if (quota != null) showQuotaPopup = true }
		)
		val displayed = if (showHidden) (rows + hiddenRows) else rows
		val hasContent = displayed.isNotEmpty() || adminRow != null
		when (io.github.johnjanthony.switchboard.emptyStateFor(hasContent, authState)) {
			io.github.johnjanthony.switchboard.EmptyStateKind.SIGN_IN_FAILED ->
				Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
					Column(
						horizontalAlignment = Alignment.CenterHorizontally,
						verticalArrangement = Arrangement.spacedBy(12.dp),
					) {
						Text("Sign-in failed", style = MaterialTheme.typography.titleMedium)
						Text(
							"Couldn't reach Google sign-in.",
							color = MaterialTheme.colorScheme.onSurfaceVariant,
						)
						Button(onClick = onRetrySignIn) { Text("Sign in") }
					}
				}
			io.github.johnjanthony.switchboard.EmptyStateKind.LOADING ->
				Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
					Text("Connecting…", color = MaterialTheme.colorScheme.onSurfaceVariant)
				}
			io.github.johnjanthony.switchboard.EmptyStateKind.NO_CONVERSATIONS ->
				Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
					Column(
						horizontalAlignment = Alignment.CenterHorizontally,
						verticalArrangement = Arrangement.spacedBy(12.dp),
					) {
						Text("No conversations yet.", color = MaterialTheme.colorScheme.onSurfaceVariant)
						Button(onClick = onSpawnClick) { Text("Spawn new session") }
					}
				}
			io.github.johnjanthony.switchboard.EmptyStateKind.NONE ->
				LazyColumn(modifier = Modifier.fillMaxSize()) {
					if (adminRow != null) {
						item(key = "_admin_row") {
							AdminRow(row = adminRow, onClick = { onAdminClick(adminRow) })
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

	if (showQuotaPopup && quota != null) {
		QuotaDetailDialog(quota = quota, onDismiss = { showQuotaPopup = false })
	}
}
