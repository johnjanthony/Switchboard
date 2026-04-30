package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DesktopWindows
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Badge
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
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
	onExitAway: () -> Unit,
) {
	var contextMenuOpen by remember { mutableStateOf(false) }

	val swipeState = rememberSwipeToDismissBoxState(
		confirmValueChange = { value ->
			when (value) {
				SwipeToDismissBoxValue.StartToEnd -> {
					onExitAway()
					false // Snap back
				}
				SwipeToDismissBoxValue.EndToStart -> {
					onHide()
					false // Snap back — we don't want the red overlay stuck if it's shown in "Show Hidden"
				}
				else -> false
			}
		}
	)

	// K6: Reset swipe state if hidden status changes (e.g. user toggles "Show Hidden")
	LaunchedEffect(channel.hidden) {
		if (swipeState.currentValue != SwipeToDismissBoxValue.Settled) {
			swipeState.reset()
		}
	}

	SwipeToDismissBox(
		state = swipeState,
		backgroundContent = {
			val direction = swipeState.dismissDirection
			val color = when (direction) {
				SwipeToDismissBoxValue.StartToEnd -> Color(0xFF4CAF50) // Green for At Desk
				SwipeToDismissBoxValue.EndToStart -> Color(0xFFF44336) // Red for Hide
				else -> Color.Transparent
			}
			val alignment = when (direction) {
				SwipeToDismissBoxValue.StartToEnd -> Alignment.CenterStart
				SwipeToDismissBoxValue.EndToStart -> Alignment.CenterEnd
				else -> Alignment.Center
			}
			val icon = when (direction) {
				SwipeToDismissBoxValue.StartToEnd -> Icons.Default.DesktopWindows
				SwipeToDismissBoxValue.EndToStart -> Icons.Default.VisibilityOff
				else -> null
			}

			Box(
				modifier = Modifier
					.fillMaxSize()
					.background(color)
					.padding(horizontal = 24.dp),
				contentAlignment = alignment
			) {
				if (icon != null) {
					Icon(
						imageVector = icon,
						contentDescription = null,
						tint = Color.White
					)
				}
			}
		}
	) {
		Box(modifier = Modifier.background(MaterialTheme.colorScheme.surface)) {
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
						if (awayActive) {
							Text(
								"AWAY", style = MaterialTheme.typography.labelSmall,
								color = MaterialTheme.colorScheme.tertiary
							)
							Spacer(Modifier.width(6.dp))
						}
						if (channel.hidden) {
							Text(
								"hidden", style = MaterialTheme.typography.labelSmall,
								color = MaterialTheme.colorScheme.outline
							)
							Spacer(Modifier.width(6.dp))
						}
						if (channel.displayCount > 0) {
							Badge { Text(channel.displayCount.toString()) }
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
}

internal fun leafName(cwdCanonical: String): String {
	return cwdCanonical.trimEnd('/').substringAfterLast('/')
}
