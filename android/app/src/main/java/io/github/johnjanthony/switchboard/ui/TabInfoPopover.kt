package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.ringForMember
import io.github.johnjanthony.switchboard.network.ConversationRow
import io.github.johnjanthony.switchboard.network.WidgetRing

@Composable
fun TabInfoPopover(
	row: ConversationRow,
	awayActive: Boolean,
	rings: Map<String, WidgetRing>,
	onDismiss: () -> Unit,
	onToggleHidden: () -> Unit,
	onToggleAway: () -> Unit,
) {
	AlertDialog(
		onDismissRequest = onDismiss,
		title = { Text(row.title) },
		text = {
			Column {
				val members = row.members
				if (members.isEmpty()) {
					val roster = row.memberRoster
					if (roster.isNotEmpty()) {
						Text(roster, style = MaterialTheme.typography.bodySmall,
							color = MaterialTheme.colorScheme.onSurfaceVariant)
					}
				} else {
					members.forEach { member ->
						Row(
							modifier = Modifier.fillMaxWidth(),
							verticalAlignment = Alignment.CenterVertically,
							horizontalArrangement = Arrangement.SpaceBetween,
						) {
							Text(
								member.sender,
								style = MaterialTheme.typography.bodyMedium,
								maxLines = 1,
								overflow = TextOverflow.Ellipsis,
								modifier = Modifier.weight(1f),
							)
							val ring = ringForMember(member, rings)
							if (ring != null) {
								ContextBadge(pct = ring.pct)
							}
						}
					}
				}
				Spacer(Modifier.height(12.dp))
				Row(verticalAlignment = Alignment.CenterVertically) {
					Text("Hidden", modifier = Modifier.weight(1f))
					Switch(checked = row.hidden, onCheckedChange = { onToggleHidden() })
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
