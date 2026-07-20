package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.RegistrySession
import io.github.johnjanthony.switchboard.sessionBoardLabel
import kotlin.math.roundToInt

/**
 * Provenance surface for a single session row: every field the registry tracks, laid out as
 * label/value pairs. Resume wiring lands in a later task - onResume stays nullable so the
 * button only shows once a caller has one to offer.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionDetailSheet(
	rec: RegistrySession,
	conversationTitle: String?,
	onDismiss: () -> Unit,
	onOpenConversation: (String) -> Unit,
	onResume: (() -> Unit)?,
) {
	val sheetState = rememberModalBottomSheetState()

	ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
		Column(
			modifier = Modifier
				.fillMaxWidth()
				.padding(horizontal = 20.dp)
				.padding(bottom = 24.dp),
			verticalArrangement = Arrangement.spacedBy(4.dp),
		) {
			Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
				Text(text = sessionBoardLabel(rec), style = MaterialTheme.typography.titleMedium)
				if (!rec.nameSource.isNullOrBlank()) {
					Text(
						text = "  (${rec.nameSource})",
						style = MaterialTheme.typography.labelSmall,
						color = MaterialTheme.colorScheme.onSurfaceVariant,
					)
				}
			}
			if (conversationTitle != null) {
				Text(
					text = conversationTitle,
					style = MaterialTheme.typography.bodySmall,
					color = MaterialTheme.colorScheme.secondary,
				)
			}

			DetailRow("State", rec.state + (rec.stateDetail?.let { " · $it" } ?: ""))
			DetailRow("Provenance", rec.lastTransitionSource ?: "-")
			DetailRow("Model", rec.model ?: "-")
			DetailRow("Context", rec.contextPct?.let { "${(it * 100).roundToInt()}%" } ?: "-")
			DetailRow("Path", rec.cwd)
			DetailRow("Surface", rec.surface)
			DetailRow("Session ID", rec.cliSessionId, monospace = true)
			DetailRow("Started", formatBubbleTimestamp(rec.startedAt))
			DetailRow("Last event", formatBubbleTimestamp(rec.lastEventAt))
			if (rec.endReason != null) {
				DetailRow("End reason", rec.endReason!!)
			}

			Row(
				modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
				horizontalArrangement = Arrangement.End,
			) {
				if (rec.conversationId != null) {
					TextButton(onClick = { onOpenConversation(rec.conversationId!!) }) {
						Text("Open conversation")
					}
				}
				if (onResume != null) {
					Button(onClick = onResume) { Text("Resume…") }
				}
			}
		}
	}
}

@Composable
private fun DetailRow(label: String, value: String, monospace: Boolean = false) {
	Row(modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
		Text(
			text = "$label: ",
			style = MaterialTheme.typography.bodySmall,
			color = MaterialTheme.colorScheme.onSurfaceVariant,
		)
		Text(
			text = value,
			style = MaterialTheme.typography.bodySmall,
			fontFamily = if (monospace) FontFamily.Monospace else null,
		)
	}
}
