package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.RadioButtonUnchecked
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.cwdTail
import io.github.johnjanthony.switchboard.isSessionSelectable
import io.github.johnjanthony.switchboard.network.RegistrySession
import io.github.johnjanthony.switchboard.parseIsoMs
import io.github.johnjanthony.switchboard.sessionBoardLabel

// Small lamp for the session board, matching the ConversationRow StatusLamp idiom (which is
// private to that file - replicated rather than shared). No pulsing here: the session board
// already surfaces urgency via the needs-attention dot, so a steady bead is enough.
@Composable
private fun SessionLamp(state: String) {
	val color = when (state) {
		"awaiting_human" -> MaterialTheme.colorScheme.tertiary
		"active", "awaiting_agent" -> MaterialTheme.colorScheme.secondary
		"ended", "lost" -> MaterialTheme.colorScheme.outline
		else -> null // idle -> hollow bead, below
	}
	if (color != null) {
		Box(modifier = Modifier.size(9.dp).background(color, CircleShape))
	} else {
		Box(modifier = Modifier.size(9.dp).border(1.5.dp, MaterialTheme.colorScheme.outline, CircleShape))
	}
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun RegistrySessionRow(
	rec: RegistrySession,
	needsAttention: Boolean,
	selectionMode: Boolean,
	selected: Boolean,
	wakeLabel: String,
	conversationTitle: String?,
	onClick: () -> Unit,
	onLongPress: () -> Unit,
	onToggleSelected: () -> Unit,
) {
	// formatRelativeTime uses Instant.parse, which rejects the server's "+00:00" offset stamps.
	// Normalize through parseIsoMs (OffsetDateTime-based) then back through Instant.ofEpochMilli,
	// which always emits a Z-suffixed string that Instant.parse accepts.
	val relTime = formatRelativeTime(
		parseIsoMs(rec.lastEventAt)?.let { java.time.Instant.ofEpochMilli(it).toString() }
	)
	// Non-selectable sessions (neither live nor resumable) can't be convene targets - dim the
	// whole row in selection mode so their ineligibility reads at a glance, and fall back to the
	// lamp instead of a check circle since there's nothing to check.
	val selectable = isSessionSelectable(rec)

	Row(
		modifier = Modifier
			.fillMaxWidth()
			.alpha(if (selectionMode && !selectable) 0.5f else 1f)
			.combinedClickable(onClick = onClick, onLongClick = onLongPress)
			.padding(horizontal = 16.dp, vertical = 12.dp),
		verticalAlignment = Alignment.CenterVertically,
	) {
		Box(
			modifier = Modifier
				.size(width = 20.dp, height = 20.dp)
				.padding(end = 6.dp),
			contentAlignment = Alignment.CenterStart,
		) {
			if (selectionMode && selectable) {
				Icon(
					imageVector = if (selected) Icons.Default.CheckCircle else Icons.Default.RadioButtonUnchecked,
					contentDescription = null,
					tint = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline,
					// Own clickable, separate from the row's combinedClickable above: Compose
					// delivers the tap to this inner target first, so it never also fires onClick.
					modifier = Modifier.size(18.dp).clickable { onToggleSelected() },
				)
			} else {
				SessionLamp(rec.state)
			}
		}
		Column(modifier = Modifier.weight(1f)) {
			Row(verticalAlignment = Alignment.CenterVertically) {
				Text(
					text = sessionBoardLabel(rec),
					style = MaterialTheme.typography.titleMedium,
					maxLines = 1,
					overflow = TextOverflow.Ellipsis,
				)
				if (needsAttention) {
					Spacer(Modifier.width(6.dp))
					Box(modifier = Modifier.size(6.dp).background(MaterialTheme.colorScheme.tertiary, CircleShape))
				}
			}
			Text(
				text = if (selectionMode) wakeLabel
					else "${cwdTail(rec.cwd)} · ${rec.surface}${rec.stateDetail?.let { " · $it" } ?: ""}",
				style = MaterialTheme.typography.bodySmall,
				color = MaterialTheme.colorScheme.onSurfaceVariant,
				maxLines = 1,
				overflow = TextOverflow.Ellipsis,
			)
			if (conversationTitle != null) {
				Text(
					text = conversationTitle,
					style = MaterialTheme.typography.bodySmall,
					color = MaterialTheme.colorScheme.secondary,
					maxLines = 1,
					overflow = TextOverflow.Ellipsis,
				)
			}
		}
		Column(horizontalAlignment = Alignment.End) {
			Text(
				text = rec.state,
				style = MaterialTheme.typography.labelSmall,
				modifier = Modifier
					.border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(8.dp))
					.padding(horizontal = 6.dp, vertical = 1.dp),
			)
			Spacer(Modifier.height(4.dp))
			val contextPct = rec.contextPct
			if (contextPct != null && contextPct > 0.50) {
				ContextBadge(pct = contextPct, modifier = Modifier.padding(bottom = 2.dp))
			}
			Text(
				text = relTime,
				style = MaterialTheme.typography.labelSmall,
				color = MaterialTheme.colorScheme.onSurfaceVariant,
			)
		}
	}
}
