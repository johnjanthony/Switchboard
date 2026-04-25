package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.Channel

@Composable
fun TabInfoPopover(
	channel: Channel,
	awayActive: Boolean,
	onDismiss: () -> Unit,
	onToggleHidden: () -> Unit,
	onToggleAway: () -> Unit,
) {
	AlertDialog(
		onDismissRequest = onDismiss,
		title = { Text(channel.title ?: "Channel") },
		text = {
			Column {
				Text(channel.cwdCanonical, style = MaterialTheme.typography.bodySmall,
					color = MaterialTheme.colorScheme.onSurfaceVariant)
				Spacer(Modifier.height(12.dp))
				Row(verticalAlignment = Alignment.CenterVertically) {
					Text("Hidden", modifier = Modifier.weight(1f))
					Switch(checked = channel.hidden, onCheckedChange = { onToggleHidden() })
				}
				Row(verticalAlignment = Alignment.CenterVertically) {
					Text("Away mode", modifier = Modifier.weight(1f))
					Switch(checked = awayActive, onCheckedChange = { onToggleAway() })
				}
			}
		},
		confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
	)
}
