package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Divider
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.AwayModePillChip
import io.github.johnjanthony.switchboard.isSessionResumable
import io.github.johnjanthony.switchboard.isSessionSelectable
import io.github.johnjanthony.switchboard.network.ConversationSummary
import io.github.johnjanthony.switchboard.network.RegistrySession
import io.github.johnjanthony.switchboard.partitionSessionBoard
import io.github.johnjanthony.switchboard.sessionNeedsAttention
import io.github.johnjanthony.switchboard.sessionWakeLabel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionsBoardScreen(
	sessions: Map<String, RegistrySession>,
	acks: Map<String, String>,
	activeConversations: List<ConversationSummary>,
	globalAway: Boolean,
	onBack: () -> Unit,
	onRowClick: (RegistrySession) -> Unit,
	onDetails: (RegistrySession) -> Unit,
	onResume: (RegistrySession) -> Unit,
	onConvene: (List<String>, String, String?) -> Unit,
	onEnterGlobalAway: () -> Unit,
	onExitGlobalAway: () -> Unit,
) {
	// Selection-mode state lives here for chunk 4 (T-8); the convene target sheet below (T-10)
	// reads selectedIds and calls onConvene.
	var selectionMode by remember { mutableStateOf(false) }
	var selectedIds by remember { mutableStateOf(setOf<String>()) }
	var menuForId by remember { mutableStateOf<String?>(null) }
	var endedExpanded by remember { mutableStateOf(false) }
	var showConveneSheet by remember { mutableStateOf(false) }

	fun toggleSelected(cliSessionId: String) {
		selectedIds = if (selectedIds.contains(cliSessionId)) {
			selectedIds - cliSessionId
		} else {
			selectedIds + cliSessionId
		}
	}

	val (live, ended) = partitionSessionBoard(sessions, acks)

	Scaffold(
		topBar = {
			TopAppBar(
				title = { Text(if (selectionMode) "${selectedIds.size} selected" else "Sessions") },
				navigationIcon = {
					IconButton(
						onClick = {
							if (selectionMode) {
								selectionMode = false
								selectedIds = emptySet()
							} else {
								onBack()
							}
						},
					) {
						if (selectionMode) {
							Icon(Icons.Default.Close, contentDescription = "Close")
						} else {
							Icon(Icons.Default.ArrowBack, contentDescription = "Back")
						}
					}
				},
				actions = {
					if (selectionMode) {
						TextButton(
							onClick = { showConveneSheet = true },
							enabled = selectedIds.isNotEmpty(),
						) {
							Text("CONVENE", color = MaterialTheme.colorScheme.primary)
						}
					} else {
						AwayModePillChip(
							active = globalAway,
							onLongPress = if (globalAway) onExitGlobalAway else onEnterGlobalAway,
						)
					}
				},
			)
		},
	) { padding ->
		if (sessions.isEmpty()) {
			Box(
				modifier = Modifier.fillMaxSize().padding(padding),
				contentAlignment = Alignment.Center,
			) {
				Text(
					text = "Sessions appear here as Claude Code runs them.",
					style = MaterialTheme.typography.bodySmall,
					color = MaterialTheme.colorScheme.onSurfaceVariant,
				)
			}
			return@Scaffold
		}

		LazyColumn(modifier = Modifier.fillMaxSize().padding(padding)) {
			item(key = "_live_header") {
				Text(
					text = "LIVE - ${live.size}",
					style = MaterialTheme.typography.labelSmall,
					color = MaterialTheme.colorScheme.primary,
					modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
				)
			}
			items(live, key = { it.cliSessionId }) { rec ->
				SessionBoardRowWithMenu(
					rec = rec,
					needsAttention = sessionNeedsAttention(rec, acks[rec.cliSessionId]),
					selectionMode = selectionMode,
					selected = selectedIds.contains(rec.cliSessionId),
					conversationTitle = rec.conversationId?.let { cid ->
						activeConversations.firstOrNull { it.id == cid }?.title
					},
					menuOpen = menuForId == rec.cliSessionId,
					onOpenMenu = { menuForId = rec.cliSessionId },
					onDismissMenu = { menuForId = null },
					onClick = {
						if (selectionMode) {
							// Non-selectable rows carry no checkbox; block the body-tap fallback too
							// so a stray tap can't sneak an unconvenable session into the set.
							if (isSessionSelectable(rec)) toggleSelected(rec.cliSessionId)
						} else {
							onRowClick(rec)
						}
					},
					onToggleSelected = { toggleSelected(rec.cliSessionId) },
					onConveneStart = {
						selectionMode = true
						selectedIds = setOf(rec.cliSessionId)
					},
					onDetails = onDetails,
					onResume = onResume,
				)
				Divider()
			}
			if (ended.isNotEmpty()) {
				item(key = "_ended_header") {
					Text(
						text = if (endedExpanded) "▾ RECENTLY ENDED" else "▸ RECENTLY ENDED (${ended.size})",
						style = MaterialTheme.typography.labelSmall,
						color = MaterialTheme.colorScheme.onSurfaceVariant,
						modifier = Modifier
							.fillMaxWidth()
							.clickable { endedExpanded = !endedExpanded }
							.padding(horizontal = 16.dp, vertical = 8.dp),
					)
				}
				if (endedExpanded) {
					items(ended, key = { it.cliSessionId }) { rec ->
						SessionBoardRowWithMenu(
							rec = rec,
							needsAttention = sessionNeedsAttention(rec, acks[rec.cliSessionId]),
							selectionMode = selectionMode,
							selected = selectedIds.contains(rec.cliSessionId),
							conversationTitle = rec.conversationId?.let { cid ->
								activeConversations.firstOrNull { it.id == cid }?.title
							},
							menuOpen = menuForId == rec.cliSessionId,
							onOpenMenu = { menuForId = rec.cliSessionId },
							onDismissMenu = { menuForId = null },
							onClick = {
								if (selectionMode) {
									if (isSessionSelectable(rec)) toggleSelected(rec.cliSessionId)
								} else {
									onRowClick(rec)
								}
							},
							onToggleSelected = { toggleSelected(rec.cliSessionId) },
							onConveneStart = {
								selectionMode = true
								selectedIds = setOf(rec.cliSessionId)
							},
							onDetails = onDetails,
							onResume = onResume,
						)
						Divider()
					}
				}
			}
		}
	}

	if (showConveneSheet) {
		ConveneTargetSheet(
			selectedCount = selectedIds.size,
			activeConversations = activeConversations,
			onDismiss = { showConveneSheet = false },
			onConvene = { target, title ->
				onConvene(selectedIds.toList(), target, title)
				selectionMode = false
				selectedIds = emptySet()
				showConveneSheet = false
			},
		)
	}
}

// Wraps a row with its long-press context menu, mirroring the ConversationRow idiom (Box hosting
// both the row content and a DropdownMenu anchored to it).
@Composable
private fun SessionBoardRowWithMenu(
	rec: RegistrySession,
	needsAttention: Boolean,
	selectionMode: Boolean,
	selected: Boolean,
	conversationTitle: String?,
	menuOpen: Boolean,
	onOpenMenu: () -> Unit,
	onDismissMenu: () -> Unit,
	onClick: () -> Unit,
	onToggleSelected: () -> Unit,
	onConveneStart: () -> Unit,
	onDetails: (RegistrySession) -> Unit,
	onResume: (RegistrySession) -> Unit,
) {
	Box {
		RegistrySessionRow(
			rec = rec,
			needsAttention = needsAttention,
			selectionMode = selectionMode,
			selected = selected,
			wakeLabel = sessionWakeLabel(rec),
			conversationTitle = conversationTitle,
			onClick = onClick,
			onLongPress = onOpenMenu,
			onToggleSelected = onToggleSelected,
		)
		DropdownMenu(expanded = menuOpen, onDismissRequest = onDismissMenu) {
			if (isSessionSelectable(rec)) {
				DropdownMenuItem(
					text = { Text("Convene into…") },
					onClick = { onConveneStart(); onDismissMenu() },
				)
			}
			DropdownMenuItem(
				text = { Text("Details") },
				onClick = { onDetails(rec); onDismissMenu() },
			)
			if (isSessionResumable(rec)) {
				DropdownMenuItem(
					text = { Text("Resume…") },
					onClick = { onResume(rec); onDismissMenu() },
				)
			}
		}
	}
}
