package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
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

@Composable
fun CombineDialog(
	sourceConversation: ConversationSummary,
	activeConversations: List<ConversationSummary>,  // already excludes source
	onDismiss: () -> Unit,
	onCombine: (targetId: String) -> Unit,
) {
	var selectedTargetId by remember { mutableStateOf<String?>(null) }
	var showConfirm by remember { mutableStateOf(false) }

	if (showConfirm && selectedTargetId != null) {
		val target = activeConversations.firstOrNull { it.id == selectedTargetId }
		AlertDialog(
			onDismissRequest = { showConfirm = false },
			title = { Text("Confirm Combine") },
			text = {
				Text(
					"Combine '${sourceConversation.title}' into '${target?.title ?: selectedTargetId}'? " +
					"Source will end; its members move to target.",
				)
			},
			confirmButton = {
				Button(onClick = { onCombine(selectedTargetId!!) }) { Text("Combine") }
			},
			dismissButton = {
				TextButton(onClick = { showConfirm = false }) { Text("Cancel") }
			},
		)
	} else {
		AlertDialog(
			onDismissRequest = onDismiss,
			title = { Text("Combine '${sourceConversation.title}' into…") },
			text = {
				Column {
					Text(
						"Select target conversation:",
						style = MaterialTheme.typography.labelMedium,
						modifier = Modifier.padding(bottom = 8.dp),
					)
					Column(
						verticalArrangement = Arrangement.spacedBy(4.dp),
						modifier = Modifier.verticalScroll(rememberScrollState()),
					) {
						if (activeConversations.isEmpty()) {
							Text(
								"No other active conversations.",
								style = MaterialTheme.typography.bodySmall,
								color = MaterialTheme.colorScheme.onSurfaceVariant,
							)
						} else {
							activeConversations.forEach { conv ->
								Row(
									verticalAlignment = Alignment.CenterVertically,
									modifier = Modifier.fillMaxWidth(),
								) {
									RadioButton(
										selected = selectedTargetId == conv.id,
										onClick = { selectedTargetId = conv.id },
									)
									Column(modifier = Modifier.weight(1f)) {
										Text(conv.title, style = MaterialTheme.typography.bodyMedium)
										Text(
											buildString {
												append(conv.memberRoster)
												val t = formatRelativeTime(conv.lastActivityAt)
												if (t.isNotBlank()) append(" · $t ago")
											},
											style = MaterialTheme.typography.bodySmall,
											color = MaterialTheme.colorScheme.onSurfaceVariant,
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
					onClick = { showConfirm = true },
					enabled = selectedTargetId != null,
				) { Text("Combine") }
			},
			dismissButton = {
				TextButton(onClick = onDismiss) { Text("Cancel") }
			},
		)
	}
}
