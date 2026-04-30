package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun PerCwdAwayPill(
	awayActive: Boolean,
	isOverride: Boolean,
	globalAway: Boolean,
	onLongPress: () -> Unit,
) {
	val label = when {
		awayActive && isOverride && !globalAway -> "Away"
		awayActive && !isOverride && globalAway -> "Away"
		!awayActive && isOverride && globalAway -> "At desk"
		else -> if (awayActive) "Away" else "At desk"
	}
	Surface(
		shape = RoundedCornerShape(50),
		color = if (awayActive) MaterialTheme.colorScheme.tertiary
		        else MaterialTheme.colorScheme.surfaceVariant,
		modifier = Modifier
			.padding(horizontal = 4.dp)
			.combinedClickable(onClick = {}, onLongClick = onLongPress),
	) {
		Text(
			text = label,
			style = MaterialTheme.typography.labelMedium,
			modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
			color = if (awayActive) MaterialTheme.colorScheme.onTertiary
			        else MaterialTheme.colorScheme.onSurfaceVariant,
		)
	}
}
