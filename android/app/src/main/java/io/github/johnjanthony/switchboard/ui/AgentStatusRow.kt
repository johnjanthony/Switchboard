package io.github.johnjanthony.switchboard.ui

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.AgentStatus

/**
 * Inline transient status row, rendered as the LAST item in a channel's
 * LazyColumn when the channel has a fresh agent_status. Mimics the
 * Slack/Messenger "is typing..." pattern.
 */
@Composable
fun AgentStatusRow(status: AgentStatus) {
	val transition = rememberInfiniteTransition(label = "agentStatusDot")
	val alphaAnim by transition.animateFloat(
		initialValue = 0.4f,
		targetValue = 1f,
		animationSpec = infiniteRepeatable(
			animation = tween(durationMillis = 1500, easing = FastOutSlowInEasing),
			repeatMode = RepeatMode.Reverse,
		),
		label = "alpha",
	)
	val scaleAnim by transition.animateFloat(
		initialValue = 0.85f,
		targetValue = 1.05f,
		animationSpec = infiniteRepeatable(
			animation = tween(durationMillis = 1500, easing = FastOutSlowInEasing),
			repeatMode = RepeatMode.Reverse,
		),
		label = "scale",
	)
	Row(
		modifier = Modifier
			.fillMaxWidth()
			.padding(horizontal = 16.dp, vertical = 6.dp),
		verticalAlignment = Alignment.CenterVertically,
	) {
		Box(
			modifier = Modifier
				.size(10.dp)
				.scale(scaleAnim)
				.alpha(alphaAnim)
				.background(MaterialTheme.colorScheme.secondary, CircleShape),
		)
		Spacer(Modifier.width(8.dp))
		Text(
			text = renderStatusText(status),
			style = MaterialTheme.typography.bodySmall.copy(fontStyle = FontStyle.Italic),
			color = MaterialTheme.colorScheme.secondary,
			maxLines = 1,
			overflow = TextOverflow.Ellipsis,
		)
	}
}

private fun renderStatusText(status: AgentStatus): String {
	val s = status.state
	return when {
		s == "thinking" -> "${status.sender} · thinking"
		s == "waiting"  -> "${status.sender} · waiting"
		s.startsWith("tool:") -> {
			val toolName = s.removePrefix("tool:")
			val detail = status.detail
			if (!detail.isNullOrBlank())
				"${status.sender} · running $toolName: $detail"
			else
				"${status.sender} · running $toolName"
		}
		else -> "${status.sender} · ${s}"
	}
}
