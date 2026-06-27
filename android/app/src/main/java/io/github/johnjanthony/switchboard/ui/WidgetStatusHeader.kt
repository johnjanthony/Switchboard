package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt
import io.github.johnjanthony.switchboard.network.WidgetQuota
import io.github.johnjanthony.switchboard.network.WidgetStatus
import io.github.johnjanthony.switchboard.network.WidgetQuotaWindow
import io.github.johnjanthony.switchboard.widgetStale

// Service-status dot colors, keyed by the server's status level.
private fun levelColor(level: String): Color = when (level) {
	"operational" -> Color(0xFF4CAF50)
	"minor" -> Color(0xFFE0A800)
	"major", "critical" -> Color(0xFFE05555)
	else -> Color(0xFF8A909C) // unknown
}

/**
 * Compact global header for the session list: 5h / 7d quota with reset countdowns, and the
 * Anthropic service-status dot with a tap control. The control pushes a check or stop command
 * (server fulfills it; see Plan 2a) based on the published button hint. When Watchtower has
 * gone stale the quota dims and a staleness note replaces the countdowns.
 */
@Composable
fun WidgetStatusHeader(
	quota: WidgetQuota?,
	status: WidgetStatus?,
	pushedAt: String?,
	onCheck: () -> Unit,
	onStop: () -> Unit,
) {
	val stale = widgetStale(System.currentTimeMillis(), pushedAt)
	Row(
		modifier = Modifier
			.fillMaxWidth()
			.background(MaterialTheme.colorScheme.surfaceVariant)
			.padding(horizontal = 14.dp, vertical = 8.dp),
		verticalAlignment = Alignment.CenterVertically,
		horizontalArrangement = Arrangement.spacedBy(14.dp),
	) {
		if (quota == null) {
			Text(
				"No quota data",
				style = MaterialTheme.typography.labelSmall,
				color = MaterialTheme.colorScheme.onSurfaceVariant,
			)
		} else {
			QuotaChip("5h", quota.session, stale)
			QuotaChip("7d", quota.weekly, stale)
		}
		Spacer(Modifier.width(0.dp))
		ServiceStatusControl(status = status, onCheck = onCheck, onStop = onStop, modifier = Modifier.weight(1f))
	}
}

@Composable
private fun QuotaChip(label: String, window: WidgetQuotaWindow?, stale: Boolean) {
	val muted = MaterialTheme.colorScheme.onSurfaceVariant
	val pct = window?.pct ?: 0.0
	Row(verticalAlignment = Alignment.CenterVertically) {
		Text("$label ", style = MaterialTheme.typography.labelSmall, color = muted)
		Text(
			"${(pct * 100).roundToInt()}%",
			style = MaterialTheme.typography.labelSmall,
			color = if (stale) muted else MaterialTheme.colorScheme.onSurface,
		)
		val countdown = if (stale || window == null) "" else
			io.github.johnjanthony.switchboard.formatResetCountdown(System.currentTimeMillis(), window.resetsAt)
		if (countdown.isNotEmpty()) {
			Text(" ($countdown)", style = MaterialTheme.typography.labelSmall, color = muted)
		}
	}
}

@Composable
private fun ServiceStatusControl(
	status: WidgetStatus?,
	onCheck: () -> Unit,
	onStop: () -> Unit,
	modifier: Modifier = Modifier,
) {
	val brass = MaterialTheme.colorScheme.primary
	val level = status?.level ?: "unknown"
	val watching = status?.watchState == "watching"
	val button = status?.button ?: "check"
	val label = if (watching) "watching" else (status?.let { levelLabel(it) } ?: "check status")
	Row(
		modifier = modifier,
		verticalAlignment = Alignment.CenterVertically,
		horizontalArrangement = Arrangement.End,
	) {
		Box(
			modifier = Modifier
				.size(9.dp)
				.background(levelColor(level), CircleShape),
		)
		Spacer(Modifier.width(6.dp))
		Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
		Spacer(Modifier.width(8.dp))
		// Drive the control off the server's published button hint (check|stop|clear),
		// matching Operator: check fires a fresh fetch; stop and clear both acknowledge
		// (the server's stop action clears watching, resolved, and capped alike).
		val controlText = when (button) { "stop" -> "stop"; "clear" -> "clear"; else -> "check" }
		Text(
			controlText,
			style = MaterialTheme.typography.labelSmall,
			color = brass,
			modifier = Modifier
				.border(1.dp, brass.copy(alpha = 0.4f), RoundedCornerShape(50))
				.clickable { if (button == "check") onCheck() else onStop() }
				.padding(horizontal = 8.dp, vertical = 2.dp),
		)
	}
}

private fun levelLabel(status: WidgetStatus): String =
	status.description.ifBlank { status.level }
