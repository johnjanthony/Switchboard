package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.ConversationSummary
import io.github.johnjanthony.switchboard.network.RegistrySession

// Target modes for resuming a dormant registry session (convening chunk 4, Task 11):
// a fresh standalone conversation, back into the conversation it last belonged to, or
// any other Active conversation - mirrors SpawnSessionDialog's conversation-choice idiom.
private enum class ResumeTargetMode { STANDALONE, BACK_INTO_OLD, EXISTING }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ResumeSessionSheet(
	rec: RegistrySession,
	boardLabel: String,
	oldConversation: ConversationSummary?,
	activeConversations: List<ConversationSummary>,
	onDismiss: () -> Unit,
	onResume: (targetConversationId: String?, prompt: String?) -> Unit,
) {
	val sheetState = rememberModalBottomSheetState()
	var mode by remember { mutableStateOf(ResumeTargetMode.STANDALONE) }
	var selectedExistingId by remember { mutableStateOf<String?>(null) }
	var existingExpanded by remember { mutableStateOf(false) }
	var prompt by remember { mutableStateOf("") }

	val selectedExisting = activeConversations.firstOrNull { it.id == selectedExistingId }

	ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
		Column(
			modifier = Modifier
				.fillMaxWidth()
				.padding(horizontal = 20.dp)
				.padding(bottom = 24.dp),
			verticalArrangement = Arrangement.spacedBy(8.dp),
		) {
			Text(text = "Resume $boardLabel", style = MaterialTheme.typography.titleMedium)

			Text("Conversation", style = MaterialTheme.typography.labelMedium)
			Row(verticalAlignment = Alignment.CenterVertically) {
				RadioButton(
					selected = mode == ResumeTargetMode.STANDALONE,
					onClick = { mode = ResumeTargetMode.STANDALONE },
				)
				Text("Standalone (no conversation)")
			}
			if (oldConversation != null) {
				Row(verticalAlignment = Alignment.CenterVertically) {
					RadioButton(
						selected = mode == ResumeTargetMode.BACK_INTO_OLD,
						onClick = { mode = ResumeTargetMode.BACK_INTO_OLD },
					)
					Text("Back into \"${oldConversation.title}\"")
				}
			}
			Row(verticalAlignment = Alignment.CenterVertically) {
				RadioButton(
					selected = mode == ResumeTargetMode.EXISTING,
					onClick = { mode = ResumeTargetMode.EXISTING },
					enabled = activeConversations.isNotEmpty(),
				)
				Text(
					"Into existing…",
					color = if (activeConversations.isNotEmpty()) MaterialTheme.colorScheme.onSurface
					        else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
				)
			}
			if (mode == ResumeTargetMode.EXISTING) {
				ExposedDropdownMenuBox(
					expanded = existingExpanded,
					onExpandedChange = { existingExpanded = !existingExpanded },
				) {
					OutlinedTextField(
						value = selectedExisting?.let { "${it.title} (${it.memberRoster})" }
							?: "select Active conversation…",
						onValueChange = {},
						readOnly = true,
						modifier = Modifier.fillMaxWidth().menuAnchor(),
						trailingIcon = {
							ExposedDropdownMenuDefaults.TrailingIcon(expanded = existingExpanded)
						},
						colors = ExposedDropdownMenuDefaults.outlinedTextFieldColors(),
					)
					ExposedDropdownMenu(
						expanded = existingExpanded,
						onDismissRequest = { existingExpanded = false },
					) {
						if (activeConversations.isEmpty()) {
							DropdownMenuItem(
								text = { Text("No active conversations") },
								onClick = { existingExpanded = false },
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
										selectedExistingId = conv.id
										existingExpanded = false
									},
								)
							}
						}
					}
				}
			}

			OutlinedTextField(
				value = prompt,
				onValueChange = { prompt = it },
				label = { Text("New prompt (optional)") },
				modifier = Modifier.fillMaxWidth(),
				minLines = 2,
			)

			Row(
				modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
				horizontalArrangement = Arrangement.End,
			) {
				TextButton(onClick = onDismiss) { Text("Cancel") }
				Spacer(Modifier.width(8.dp))
				val resumeEnabled = mode != ResumeTargetMode.EXISTING || selectedExistingId != null
				Button(
					onClick = {
						val targetId = when (mode) {
							ResumeTargetMode.STANDALONE -> null
							ResumeTargetMode.BACK_INTO_OLD -> oldConversation?.id
							ResumeTargetMode.EXISTING -> selectedExistingId
						}
						onResume(targetId, prompt.trim().ifBlank { null })
					},
					enabled = resumeEnabled,
				) { Text("Resume") }
			}
		}
	}
}
