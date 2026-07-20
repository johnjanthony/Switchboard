package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.WidgetQuota
import io.github.johnjanthony.switchboard.network.WidgetQuotaWindow
import io.github.johnjanthony.switchboard.widgetStale
import java.time.OffsetDateTime

// Severity palette shared with Watchtower and Operator: green < 50%, amber 50-80%, red > 80%.
private val CtxGreen = Color(0xFF4CAF50)
private val CtxAmber = Color(0xFFE0A800)
private val CtxRed = Color(0xFFE05555)

/**
 * Compact global header for the session list: 5h / 7d graphical quota indicators mirroring
 * Watchtower. Clicking the status line opens a detail popup with textual data.
 */
@Composable
fun WidgetStatusHeader(
	quota: WidgetQuota?,
	pushedAt: String?,
	onClick: () -> Unit,
) {
	val stale = widgetStale(System.currentTimeMillis(), pushedAt)
	Row(
		modifier = Modifier
			.fillMaxWidth()
			.background(MaterialTheme.colorScheme.surfaceVariant)
			.clickable { onClick() }
			.padding(horizontal = 14.dp, vertical = 10.dp),
		verticalAlignment = Alignment.CenterVertically,
		horizontalArrangement = Arrangement.spacedBy(16.dp),
	) {
		if (quota == null) {
			Text(
				"No quota data",
				style = MaterialTheme.typography.labelSmall,
				color = MaterialTheme.colorScheme.onSurfaceVariant,
			)
		} else {
			// 7d graph first (left), 5h graph second (right)
			QuotaGraph(
				window = quota.weekly,
				durationMs = 7L * 24 * 60 * 60 * 1000,
				stale = stale,
				modifier = Modifier.weight(1f)
			)
			QuotaGraph(
				window = quota.session,
				durationMs = 5L * 60 * 60 * 1000,
				stale = stale,
				modifier = Modifier.weight(1f)
			)
		}
	}
}

@Composable
private fun QuotaGraph(
	window: WidgetQuotaWindow?,
	durationMs: Long,
	stale: Boolean,
	modifier: Modifier = Modifier,
) {
	val pct = window?.pct ?: 0.0
	val resetsAt = window?.resetsAt ?: ""
	val now = System.currentTimeMillis()

	val pace = try {
		val resetMs = OffsetDateTime.parse(resetsAt).toInstant().toEpochMilli()
		val startMs = resetMs - durationMs
		val elapsed = now - startMs
		(elapsed.toDouble() / durationMs).coerceIn(0.0, 1.0)
	} catch (_: Exception) {
		0.0
	}

	val barBrush = if (stale) {
		Brush.linearGradient(listOf(MaterialTheme.colorScheme.outline, MaterialTheme.colorScheme.outline))
	} else {
		Brush.linearGradient(
			0.0f to CtxGreen,
			0.6f to CtxAmber,
			1.0f to CtxRed
		)
	}

	val indicatorColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)

	Column(modifier = modifier) {
		Box(
			modifier = Modifier
				.fillMaxWidth()
				.height(6.dp)
				.background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.3f), RoundedCornerShape(3.dp))
				.drawWithContent {
					drawContent()
					drawRoundRect(
						brush = barBrush,
						size = size.copy(width = size.width * pct.toFloat()),
						cornerRadius = CornerRadius(3.dp.toPx(), 3.dp.toPx())
					)
				}
		)
		Spacer(Modifier.height(4.dp))
		// Pace indicator
		Box(
			modifier = Modifier
				.fillMaxWidth()
				.height(2.dp)
				.background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.2f), RoundedCornerShape(1.dp))
				.drawBehind {
					val x = size.width * pace.toFloat()
					drawLine(
						color = indicatorColor,
						start = Offset(0f, size.height / 2),
						end = Offset(x, size.height / 2),
						strokeWidth = size.height,
						cap = StrokeCap.Round
					)
				}
		)
	}
}
