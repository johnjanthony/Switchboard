package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.SpawnCollisionData

@Composable
fun SpawnCollisionDialog(
	collision: SpawnCollisionData,
	onContinue: () -> Unit,
	onClear: () -> Unit,
	onCancel: () -> Unit,
) {
	AlertDialog(
		onDismissRequest = onCancel,
		title = { Text("Workspace already has a conversation") },
		text = {
			Column {
				Text(
					text = collision.channelTitle ?: leafName(collision.cwd),
					style = MaterialTheme.typography.titleMedium,
				)
				Spacer(Modifier.height(4.dp))
				Text(
					text = collision.cwd,
					style = MaterialTheme.typography.bodySmall,
					color = MaterialTheme.colorScheme.onSurfaceVariant,
				)
				Spacer(Modifier.height(8.dp))
				val activity = collision.lastActivityAt?.let { formatRelativeTime(it) } ?: "(no activity)"
				Text("Last activity: $activity")
				if (collision.hidden) {
					Spacer(Modifier.height(4.dp))
					Text(
						"(currently hidden)",
						style = MaterialTheme.typography.labelSmall,
						color = MaterialTheme.colorScheme.outline,
					)
				}
				Spacer(Modifier.height(12.dp))
				Text(
					text = "Continue keeps the existing conversation. Clear wipes the history and starts fresh.",
					style = MaterialTheme.typography.bodySmall,
					color = MaterialTheme.colorScheme.onSurfaceVariant,
				)
			}
		},
		confirmButton = {
			Row {
				TextButton(onClick = onContinue) { Text("Continue") }
				Spacer(Modifier.width(4.dp))
				TextButton(onClick = onClear) { Text("Clear & start fresh") }
			}
		},
		dismissButton = { TextButton(onClick = onCancel) { Text("Cancel") } },
	)
}
