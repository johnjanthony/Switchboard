package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.RingSeverity
import io.github.johnjanthony.switchboard.ringSeverity
import kotlin.math.roundToInt

// Severity palette shared with Watchtower and Operator: green < 50%, amber 50-80%, red > 80%.
private val CtxGreen = Color(0xFF4CAF50)
private val CtxAmber = Color(0xFFE0A800)
private val CtxRed = Color(0xFFE05555)

/**
 * Small outlined context-window-fill badge ("83%"), colored by severity. Used on the
 * session-list rows (option C) and the conversation info popover member list (option B).
 */
@Composable
fun ContextBadge(pct: Double, modifier: Modifier = Modifier) {
	val color = when (ringSeverity(pct)) {
		RingSeverity.RED -> CtxRed
		RingSeverity.AMBER -> CtxAmber
		RingSeverity.GREEN -> CtxGreen
		RingSeverity.NONE -> MaterialTheme.colorScheme.outline
	}
	Text(
		text = "${(pct * 100).roundToInt()}%",
		style = MaterialTheme.typography.labelSmall,
		color = color,
		modifier = modifier
			.border(1.dp, color, RoundedCornerShape(8.dp))
			.padding(horizontal = 6.dp, vertical = 1.dp),
	)
}
