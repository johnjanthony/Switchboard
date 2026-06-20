package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.ConversationSummary

@Composable
fun SpawnResumeDialog(
	sourceConversation: ConversationSummary,
	onDismiss: () -> Unit,
	onResume: (newPrompt: String) -> Unit,
) {
	var newPrompt by remember { mutableStateOf("") }

	AlertDialog(
		onDismissRequest = onDismiss,
		title = { Text("Resume Conversation") },
		text = {
			Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
				Text(
					text = sourceConversation.title,
					style = MaterialTheme.typography.titleMedium,
				)
				Text(
					text = buildString {
						append(sourceConversation.memberRoster)
						val lastActivity = formatRelativeTime(sourceConversation.lastActivityAt)
						if (lastActivity.isNotBlank()) append(" · Last activity $lastActivity ago")
					},
					style = MaterialTheme.typography.bodySmall,
					color = MaterialTheme.colorScheme.onSurfaceVariant,
				)
				OutlinedTextField(
					value = newPrompt,
					onValueChange = { newPrompt = it },
					label = { Text("New prompt (optional)") },
					modifier = Modifier.fillMaxWidth(),
					minLines = 2,
				)
			}
		},
		confirmButton = {
			Button(onClick = { onResume(newPrompt.trim()) }) { Text("Resume") }
		},
		dismissButton = {
			TextButton(onClick = onDismiss) { Text("Cancel") }
		},
	)
}
