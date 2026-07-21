package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.formatResetCountdown
import io.github.johnjanthony.switchboard.formatTimeElapsedPercentage
import io.github.johnjanthony.switchboard.network.WidgetQuota
import io.github.johnjanthony.switchboard.network.WidgetQuotaWindow
import kotlin.math.roundToInt

@Composable
fun QuotaDetailDialog(
	quota: WidgetQuota,
	onDismiss: () -> Unit,
) {
	AlertDialog(
		onDismissRequest = onDismiss,
		title = { Text("Service Quota", style = MaterialTheme.typography.titleMedium) },
		text = {
			Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
				QuotaSection("Weekly (7d)", quota.weekly, 7L * 24L * 60L * 60L * 1000L)
				QuotaSection("Session (5h)", quota.session, 5L * 60L * 60L * 1000L)
			}
		},
		confirmButton = {
			TextButton(onClick = onDismiss) {
				Text("Close")
			}
		}
	)
}

@Composable
private fun QuotaSection(label: String, window: WidgetQuotaWindow?, durationMs: Long) {
	Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
		Text(
			label.uppercase(),
			style = MaterialTheme.typography.labelSmall,
			color = MaterialTheme.colorScheme.primary,
		)
		if (window == null) {
			Text("No data", style = MaterialTheme.typography.bodySmall)
		} else {
			DetailRow("Usage", "${(window.pct * 100).roundToInt()}%")
			val nowMs = System.currentTimeMillis()
			val elapsedPct = formatTimeElapsedPercentage(nowMs, window.resetsAt, durationMs)
			if (elapsedPct != null) {
				DetailRow("Time elapsed", elapsedPct)
			}
			val countdown = formatResetCountdown(nowMs, window.resetsAt)
			DetailRow("Reset in", if (countdown.isNotEmpty()) countdown else "now")
			DetailRow("Reset at", formatBubbleTimestamp(window.resetsAt))
		}
	}
}

@Composable
private fun DetailRow(label: String, value: String) {
	Row(
		modifier = Modifier.fillMaxWidth(),
		horizontalArrangement = Arrangement.SpaceBetween,
		verticalAlignment = androidx.compose.ui.Alignment.CenterVertically
	) {
		Text(
			text = label,
			style = MaterialTheme.typography.bodySmall,
			color = MaterialTheme.colorScheme.onSurfaceVariant
		)
		Text(
			text = value,
			style = MaterialTheme.typography.bodySmall,
			fontFamily = FontFamily.Monospace
		)
	}
}
