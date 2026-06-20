package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.ConversationSummary

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SpawnSessionDialog(
	mruList: List<String>,
	activeConversations: List<ConversationSummary>,
	wslAvailable: Boolean,
	onDismiss: () -> Unit,
	onSpawn: (surface: String, project: String, prompt: String, targetConversationId: String?) -> Unit,
	onRemoveFromMru: (String) -> Unit,
) {
	var surface by remember { mutableStateOf("windows") }
	var project by remember { mutableStateOf("") }
	var prompt by remember { mutableStateOf("") }
	var projectExpanded by remember { mutableStateOf(false) }

	// Conversation choice: null = "create new"; non-null = id of selected existing conversation
	var addToExisting by remember { mutableStateOf(false) }
	var selectedConversationId by remember { mutableStateOf<String?>(null) }
	var convExpanded by remember { mutableStateOf(false) }

	val selectedConversation = activeConversations.firstOrNull { it.id == selectedConversationId }

	val spawnEnabled = project.isNotBlank() && (!addToExisting || selectedConversationId != null)

	AlertDialog(
		onDismissRequest = onDismiss,
		title = { Text("Spawn Claude Session") },
		text = {
			Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
				// Surface radio
				Text("Surface", style = MaterialTheme.typography.labelMedium)
				Row(verticalAlignment = Alignment.CenterVertically) {
					RadioButton(
						selected = surface == "windows",
						onClick = { surface = "windows" },
					)
					Text("Windows")
					Spacer(Modifier.width(16.dp))
					RadioButton(
						selected = surface == "wsl",
						onClick = { if (wslAvailable) surface = "wsl" },
						enabled = wslAvailable,
					)
					Text(
						"WSL",
						color = if (wslAvailable) MaterialTheme.colorScheme.onSurface
						        else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
					)
				}

				// Project picker (MRU dropdown)
				ExposedDropdownMenuBox(
					expanded = projectExpanded,
					onExpandedChange = { projectExpanded = !projectExpanded },
				) {
					OutlinedTextField(
						value = project,
						onValueChange = { project = it },
						label = { Text("Project") },
						singleLine = true,
						modifier = Modifier.fillMaxWidth().menuAnchor(),
						trailingIcon = {
							ExposedDropdownMenuDefaults.TrailingIcon(expanded = projectExpanded)
						},
						colors = ExposedDropdownMenuDefaults.outlinedTextFieldColors(),
					)
					if (mruList.isNotEmpty()) {
						ExposedDropdownMenu(
							expanded = projectExpanded,
							onDismissRequest = { projectExpanded = false },
						) {
							mruList.forEach { item ->
								DropdownMenuItem(
									text = {
										Row(
											verticalAlignment = Alignment.CenterVertically,
											modifier = Modifier.fillMaxWidth(),
										) {
											Text(item, modifier = Modifier.weight(1f))
											IconButton(
												onClick = { onRemoveFromMru(item) },
												modifier = Modifier.size(24.dp),
											) {
												Icon(
													Icons.Default.Delete,
													contentDescription = "Remove",
													modifier = Modifier.size(16.dp),
													tint = MaterialTheme.colorScheme.error,
												)
											}
										}
									},
									onClick = {
										project = item
										projectExpanded = false
									},
								)
							}
						}
					}
				}

				// Initial Prompt
				OutlinedTextField(
					value = prompt,
					onValueChange = { prompt = it },
					label = { Text("Initial Prompt (optional)") },
					modifier = Modifier.fillMaxWidth(),
					minLines = 2,
				)

				// Conversation choice
				Text("Conversation", style = MaterialTheme.typography.labelMedium)
				Row(verticalAlignment = Alignment.CenterVertically) {
					RadioButton(
						selected = !addToExisting,
						onClick = { addToExisting = false; selectedConversationId = null },
					)
					Text("Create new")
				}
				Row(verticalAlignment = Alignment.CenterVertically) {
					RadioButton(
						selected = addToExisting,
						onClick = { addToExisting = true },
						enabled = activeConversations.isNotEmpty(),
					)
					Text(
						"Add to existing:",
						color = if (activeConversations.isNotEmpty()) MaterialTheme.colorScheme.onSurface
						        else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
					)
				}
				if (addToExisting) {
					Spacer(Modifier.height(4.dp))
					ExposedDropdownMenuBox(
						expanded = convExpanded,
						onExpandedChange = { convExpanded = !convExpanded },
					) {
						OutlinedTextField(
							value = selectedConversation?.let { "${it.title} (${it.memberRoster})" }
								?: "select Active conversation…",
							onValueChange = {},
							readOnly = true,
							modifier = Modifier.fillMaxWidth().menuAnchor(),
							trailingIcon = {
								ExposedDropdownMenuDefaults.TrailingIcon(expanded = convExpanded)
							},
							colors = ExposedDropdownMenuDefaults.outlinedTextFieldColors(),
						)
						ExposedDropdownMenu(
							expanded = convExpanded,
							onDismissRequest = { convExpanded = false },
						) {
							if (activeConversations.isEmpty()) {
								DropdownMenuItem(
									text = { Text("No active conversations") },
									onClick = { convExpanded = false },
									enabled = false,
								)
							} else {
								activeConversations.forEach { conv ->
									DropdownMenuItem(
										text = {
											Column {
												Text(conv.title, style = MaterialTheme.typography.bodyMedium)
												Text(
													conv.memberRoster,
													style = MaterialTheme.typography.bodySmall,
													color = MaterialTheme.colorScheme.onSurfaceVariant,
												)
											}
										},
										onClick = {
											selectedConversationId = conv.id
											convExpanded = false
										},
									)
								}
							}
						}
					}
				}
			}
		},
		confirmButton = {
			Button(
				onClick = {
					onSpawn(
						surface,
						project.trim(),
						prompt.trim(),
						if (addToExisting) selectedConversationId else null,
					)
				},
				enabled = spawnEnabled,
			) { Text("Spawn") }
		},
		dismissButton = {
			TextButton(onClick = onDismiss) { Text("Cancel") }
		},
	)
}
