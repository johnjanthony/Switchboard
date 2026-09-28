package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.PendingSend
import io.github.johnjanthony.switchboard.PendingSendPolicy
import io.github.johnjanthony.switchboard.PendingSendState
import io.github.johnjanthony.switchboard.network.ChannelMessage
import io.github.johnjanthony.switchboard.ui.theme.AlertRed
import io.github.johnjanthony.switchboard.ui.theme.Amber

// A message John sent that the server has not written back yet (or never will):
// his own bubble, muted, with the delivery state and its actions underneath.
@Composable
fun PendingSendRow(
	send: PendingSend,
	conversationActive: Boolean,
	fontScale: Float,
	onCancel: () -> Unit,
	onRetry: () -> Unit,
	onDiscard: () -> Unit,
	onDismiss: () -> Unit,
) {
	val statusColor = when (send.state) {
		PendingSendState.NOT_PICKED_UP -> Amber
		PendingSendState.EXPIRED, PendingSendState.NOT_DELIVERED -> AlertRed
		else -> MaterialTheme.colorScheme.onSurfaceVariant
	}
	Column(modifier = Modifier.fillMaxWidth()) {
		Box(modifier = Modifier.alpha(0.6f)) {
			MessageBubble(
				message = ChannelMessage(
					sender = "John", type = "human", text = send.text,
					timestamp = send.issuedAt, format = "markdown",
				),
				fontScale = fontScale,
			)
		}
		Row(
			modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp),
			verticalAlignment = Alignment.CenterVertically,
		) {
			Text(
				text = PendingSendPolicy.statusText(send.state),
				style = MaterialTheme.typography.bodySmall,
				color = statusColor,
				modifier = Modifier.weight(1f),
			)
			when (send.state) {
				PendingSendState.NOT_PICKED_UP -> TextButton(onClick = onCancel) { Text("Cancel") }
				PendingSendState.EXPIRED -> {
					if (conversationActive) TextButton(onClick = onRetry) { Text("Retry") }
					TextButton(onClick = onDiscard) { Text("Discard") }
				}
				PendingSendState.NOT_DELIVERED -> {
					if (conversationActive) TextButton(onClick = onRetry) { Text("Retry") }
					TextButton(onClick = onDismiss) { Text("Dismiss") }
				}
				// A command queued offline cannot be recalled: the write replays before the delete.
				PendingSendState.SENDING, PendingSendState.OFFLINE -> {}
			}
		}
	}
}
