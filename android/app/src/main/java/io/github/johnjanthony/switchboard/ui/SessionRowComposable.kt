package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Badge
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.Channel

@OptIn(ExperimentalFoundationApi::class, ExperimentalMaterial3Api::class)
@Composable
fun SessionRow(
	channel: Channel,
	awayActive: Boolean,
	onClick: () -> Unit,
	onHide: () -> Unit,
	onUnhide: () -> Unit,
) {
	var contextMenuOpen by remember { mutableStateOf(false) }

	Box {
		Row(
			modifier = Modifier
				.fillMaxWidth()
				.combinedClickable(onClick = onClick, onLongClick = { contextMenuOpen = true })
				.alpha(if (channel.hidden) 0.5f else 1f)
				.padding(horizontal = 16.dp, vertical = 12.dp),
			verticalAlignment = Alignment.CenterVertically,
		) {
			Column(modifier = Modifier.weight(1f)) {
				Text(
					text = buildAnnotatedString {
						withStyle(style = MaterialTheme.typography.titleMedium.toSpanStyle()) {
							append(channel.title ?: leafName(channel.cwdCanonical))
						}
						if (channel.cwdCanonical.isNotEmpty()) {
							withStyle(
								style = MaterialTheme.typography.bodySmall.toSpanStyle().copy(
									color = MaterialTheme.colorScheme.onSurfaceVariant,
									fontStyle = if (channel.hidden) FontStyle.Italic else FontStyle.Normal,
								)
							) {
								append(" (${leafName(channel.cwdCanonical)})")
							}
						}
					},
					maxLines = 1,
					overflow = TextOverflow.Ellipsis,
				)
				val preview = channel.preview
				if (!preview.isNullOrBlank()) {
					Text(
						text = preview,
						style = MaterialTheme.typography.bodySmall,
						color = MaterialTheme.colorScheme.onSurfaceVariant,
						maxLines = 1,
						overflow = TextOverflow.Ellipsis,
					)
				}
			}
			Column(horizontalAlignment = Alignment.End) {
				Text(
					text = formatRelativeTime(channel.lastActivityAt),
					style = MaterialTheme.typography.labelSmall,
					color = MaterialTheme.colorScheme.onSurfaceVariant,
				)
				Row(verticalAlignment = Alignment.CenterVertically) {
					if (channel.pendingQuestions.values.any { !it.cancelled }) {
						Box(
							modifier = Modifier.size(8.dp).background(
								MaterialTheme.colorScheme.primary, CircleShape,
							),
						)
						Spacer(Modifier.width(6.dp))
					}
					if (awayActive) {
						Text("AWAY", style = MaterialTheme.typography.labelSmall,
							color = MaterialTheme.colorScheme.tertiary)
						Spacer(Modifier.width(6.dp))
					}
					if (channel.hidden) {
						Text("hidden", style = MaterialTheme.typography.labelSmall,
							color = MaterialTheme.colorScheme.outline)
						Spacer(Modifier.width(6.dp))
					}
					if (channel.unreadCount > 0) {
						Badge { Text(channel.unreadCount.toString()) }
					}
				}
			}
		}
		DropdownMenu(expanded = contextMenuOpen, onDismissRequest = { contextMenuOpen = false }) {
			if (channel.hidden) {
				DropdownMenuItem(
					text = { Text("Unhide channel") },
					onClick = { onUnhide(); contextMenuOpen = false },
				)
			} else {
				DropdownMenuItem(
					text = { Text("Hide channel") },
					onClick = { onHide(); contextMenuOpen = false },
				)
			}
		}
	}
}

internal fun leafName(cwdCanonical: String): String {
	return cwdCanonical.trimEnd('/').substringAfterLast('/')
}
