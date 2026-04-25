package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.BulkRespondPayload

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun BulkRespondDialog(
	payload: BulkRespondPayload,
	onSendToAll: (text: String) -> Unit,
	onSkip: () -> Unit,
	onCancel: () -> Unit,
) {
	var defaultText by remember { mutableStateOf(payload.defaultText) }

	AlertDialog(
		onDismissRequest = onCancel,
		title = { Text("Pending questions across sessions") },
		text = {
			LazyColumn(modifier = Modifier.heightIn(max = 320.dp).fillMaxWidth()) {
				payload.sections.forEach { section ->
					stickyHeader {
						Text(
							text = leafName(section.cwd),
							style = MaterialTheme.typography.titleSmall,
							modifier = Modifier
								.fillMaxWidth()
								.background(MaterialTheme.colorScheme.surfaceVariant)
								.padding(horizontal = 8.dp, vertical = 4.dp),
						)
					}
					items(section.entries, key = { it.requestId }) { entry ->
						Column(
							modifier = Modifier
								.fillMaxWidth()
								.padding(horizontal = 12.dp, vertical = 6.dp),
						) {
							Text(
								text = entry.sender,
								style = MaterialTheme.typography.labelMedium,
								fontWeight = FontWeight.Bold,
							)
							Text(
								text = entry.questionText,
								style = MaterialTheme.typography.bodyMedium,
								maxLines = 3,
								overflow = TextOverflow.Ellipsis,
							)
						}
					}
				}
				item {
					Spacer(Modifier.height(12.dp))
					OutlinedTextField(
						value = defaultText,
						onValueChange = { defaultText = it },
						label = { Text("Default response") },
						modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp),
						maxLines = 3,
					)
				}
			}
		},
		confirmButton = {
			Row {
				TextButton(onClick = { onSendToAll(defaultText) }) { Text("Send to all") }
				Spacer(Modifier.width(4.dp))
				TextButton(onClick = onSkip) { Text("Skip") }
			}
		},
		dismissButton = { TextButton(onClick = onCancel) { Text("Cancel") } },
	)
}
