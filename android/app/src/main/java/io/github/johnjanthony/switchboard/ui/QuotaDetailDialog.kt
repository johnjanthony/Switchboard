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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import io.github.johnjanthony.switchboard.formatAgyGroupName
import io.github.johnjanthony.switchboard.formatTimeElapsedPercentage
import io.github.johnjanthony.switchboard.groupSortKey
import io.github.johnjanthony.switchboard.isAgyGroupVisible
import io.github.johnjanthony.switchboard.network.WidgetQuota
import io.github.johnjanthony.switchboard.network.WidgetQuotaWindow
import kotlin.math.roundToInt

@Composable
fun QuotaDetailDialog(
	quota: WidgetQuota,
	onDismiss: () -> Unit,
) {
	val rawAgy = quota.antigravity ?: emptyList()
	val visibleAgy = rawAgy
		.filter { isAgyGroupVisible(it) }
		.sortedBy { groupSortKey(it.displayName) }

	val hasClaude = quota.session != null || quota.weekly != null

	AlertDialog(
		onDismissRequest = onDismiss,
		title = { Text("Service Quota", style = MaterialTheme.typography.titleMedium) },
		text = {
			Column(
				modifier = Modifier.verticalScroll(rememberScrollState()),
				verticalArrangement = Arrangement.spacedBy(20.dp)
			) {
				for (group in visibleAgy) {
					GroupQuotaSection(
						title = formatAgyGroupName(group.displayName),
						sessionWindow = group.session,
						weeklyWindow = group.weekly,
					)
				}
				if (hasClaude) {
					GroupQuotaSection(
						title = "Claude Code",
						sessionWindow = quota.session,
						weeklyWindow = quota.weekly,
					)
				}
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
private fun GroupQuotaSection(
	title: String,
	sessionWindow: WidgetQuotaWindow?,
	weeklyWindow: WidgetQuotaWindow?,
) {
	Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
		Text(
			title.uppercase(),
			style = MaterialTheme.typography.labelSmall,
			color = MaterialTheme.colorScheme.primary,
		)
		QuotaWindowBlock("Session (5h)", sessionWindow, 5L * 60L * 60L * 1000L)
		QuotaWindowBlock("Weekly (7d)", weeklyWindow, 7L * 24L * 60L * 60L * 1000L)
	}
}

@Composable
private fun QuotaWindowBlock(label: String, window: WidgetQuotaWindow?, durationMs: Long) {
	Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
		Text(
			label,
			style = MaterialTheme.typography.bodySmall,
			color = MaterialTheme.colorScheme.onSurfaceVariant,
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
