package io.github.johnjanthony.switchboard.ui

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DesktopWindows
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.ConversationRow
import io.github.johnjanthony.switchboard.network.WidgetRing

// Status lamp: one object, three states. Waiting (coral, pulsing) outranks live (jade,
// steady); a line with neither sits as a hollow idle bead. Mirrors the conversation feed.
@Composable
private fun StatusLamp(color: Color, pulsing: Boolean) {
	if (!pulsing) {
		Box(modifier = Modifier.size(9.dp).background(color, CircleShape))
		return
	}
	val transition = rememberInfiniteTransition(label = "statusLamp")
	val a by transition.animateFloat(
		initialValue = 0.5f, targetValue = 1f,
		animationSpec = infiniteRepeatable(tween(1400, easing = FastOutSlowInEasing), RepeatMode.Reverse),
		label = "alpha",
	)
	val s by transition.animateFloat(
		initialValue = 0.85f, targetValue = 1.12f,
		animationSpec = infiniteRepeatable(tween(1400, easing = FastOutSlowInEasing), RepeatMode.Reverse),
		label = "scale",
	)
	Box(modifier = Modifier.size(9.dp).scale(s).alpha(a).background(color, CircleShape))
}

@Composable
private fun StatusLampIdle() {
	Box(modifier = Modifier.size(9.dp).border(1.5.dp, MaterialTheme.colorScheme.outline, CircleShape))
}

// Brass-outlined count token (unread / pending), quieter than a filled badge.
@Composable
private fun CountChip(count: Int) {
	Box(
		modifier = Modifier
			.border(1.5.dp, MaterialTheme.colorScheme.primary, CircleShape)
			.padding(horizontal = 6.dp, vertical = 1.dp),
		contentAlignment = Alignment.Center,
	) {
		Text(
			text = count.toString(),
			style = MaterialTheme.typography.labelSmall,
			color = MaterialTheme.colorScheme.primary,
		)
	}
}

@OptIn(ExperimentalFoundationApi::class, ExperimentalMaterial3Api::class)
@Composable
fun ConversationRow(
	row: ConversationRow,
	resumable: Boolean,
	onClick: () -> Unit,
	onHide: () -> Unit,
	onUnhide: () -> Unit,
	onResumeClick: (conversationId: String) -> Unit = {},
	onCombineClick: (conversationId: String) -> Unit = {},
	onEndClick: (conversationId: String) -> Unit = {},
	contextRing: WidgetRing? = null,
) {
	var contextMenuOpen by remember { mutableStateOf(false) }
	var showHideConfirm by remember { mutableStateOf(false) }
	var showEndConfirm by remember { mutableStateOf(false) }

	val isActive = row.state == "active"
	val agentStatus = row.agentStatus
	val displayTitle = row.title
	val roster = row.memberRoster

	if (showHideConfirm) {
		AlertDialog(
			onDismissRequest = { showHideConfirm = false },
			title = { Text("Hide conversation?") },
			text = { Text("Hide '$displayTitle'? You can unhide it later from the menu.") },
			confirmButton = {
				TextButton(onClick = { onHide(); showHideConfirm = false }) { Text("Hide") }
			},
			dismissButton = {
				TextButton(onClick = { showHideConfirm = false }) { Text("Cancel") }
			},
		)
	}

	if (showEndConfirm) {
		AlertDialog(
			onDismissRequest = { showEndConfirm = false },
			title = { Text("End conversation?") },
			text = {
				Text(
					"End conversation '$displayTitle'? Members will fall back to their home conversation " +
					"(if away mode is on) or to terminal output (if off).",
				)
			},
			confirmButton = {
				TextButton(onClick = { onEndClick(row.id); showEndConfirm = false }) { Text("End") }
			},
			dismissButton = {
				TextButton(onClick = { showEndConfirm = false }) { Text("Cancel") }
			},
		)
	}

	val swipeState = rememberSwipeToDismissBoxState(
		confirmValueChange = { value ->
			when (value) {
				SwipeToDismissBoxValue.StartToEnd -> {
					showEndConfirm = true
					false
				}
				SwipeToDismissBoxValue.EndToStart -> {
					showHideConfirm = true
					false
				}
				else -> false
			}
		}
	)

	LaunchedEffect(row.hidden) {
		if (swipeState.currentValue != SwipeToDismissBoxValue.Settled) {
			swipeState.reset()
		}
	}

	SwipeToDismissBox(
		state = swipeState,
		backgroundContent = {
			val direction = swipeState.dismissDirection
			// End is destructive (loud red); hide is benign (quiet neutral).
			val color = when (direction) {
				SwipeToDismissBoxValue.StartToEnd -> MaterialTheme.colorScheme.error
				SwipeToDismissBoxValue.EndToStart -> MaterialTheme.colorScheme.surfaceVariant
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
					Icon(imageVector = icon, contentDescription = null, tint = MaterialTheme.colorScheme.onSurface)
				}
			}
		}
	) {
		val rowModifier = Modifier
			.background(MaterialTheme.colorScheme.surface)

		Box(modifier = rowModifier) {
			Row(
				modifier = Modifier
					.fillMaxWidth()
					.combinedClickable(onClick = onClick, onLongClick = { contextMenuOpen = true })
					.alpha(if (row.hidden) 0.5f else 1f)
					.padding(horizontal = 16.dp, vertical = 12.dp),
				verticalAlignment = Alignment.CenterVertically,
			) {
				val hasPending = row.summary.pendingResponses > 0
				val isLive = agentStatus?.isFresh() == true
				Box(
					modifier = Modifier
						.size(width = 14.dp, height = 14.dp)
						.padding(end = 6.dp),
					contentAlignment = Alignment.CenterStart,
				) {
					when {
						hasPending -> StatusLamp(MaterialTheme.colorScheme.tertiary, pulsing = true)
						isLive -> StatusLamp(MaterialTheme.colorScheme.secondary, pulsing = false)
						else -> StatusLampIdle()
					}
				}
				Column(modifier = Modifier.weight(1f)) {
					Text(
						text = buildAnnotatedString {
							withStyle(style = MaterialTheme.typography.titleMedium.toSpanStyle()) {
								append(displayTitle)
							}
							if (roster.isNotEmpty()) {
								withStyle(
									style = MaterialTheme.typography.bodySmall.toSpanStyle().copy(
										color = MaterialTheme.colorScheme.onSurfaceVariant,
										fontStyle = if (row.hidden) FontStyle.Italic else FontStyle.Normal,
									)
								) {
									append(" ($roster)")
								}
							}
						},
						maxLines = 1,
						overflow = TextOverflow.Ellipsis,
					)
					val preview = row.preview
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
					if (contextRing != null) {
						ContextBadge(pct = contextRing.pct, modifier = Modifier.padding(bottom = 2.dp))
					}
					Row(verticalAlignment = Alignment.CenterVertically) {
						Text(
							text = formatRelativeTime(row.lastActivityAt),
							style = MaterialTheme.typography.labelSmall,
							color = MaterialTheme.colorScheme.onSurfaceVariant,
						)
					}
					Row(verticalAlignment = Alignment.CenterVertically) {
						if (row.hidden) {
							Text(
								"hidden", style = MaterialTheme.typography.labelSmall,
								color = MaterialTheme.colorScheme.outline
							)
							Spacer(Modifier.width(6.dp))
						}
						if (row.displayCount > 0) {
							CountChip(row.displayCount)
						}
					}
				}
			}
			DropdownMenu(expanded = contextMenuOpen, onDismissRequest = { contextMenuOpen = false }) {
				DropdownMenuItem(
					text = { Text("Resume") },
					enabled = resumable,
					onClick = { onResumeClick(row.id); contextMenuOpen = false },
				)
				if (isActive) {
					DropdownMenuItem(
						text = { Text("Combine into…") },
						onClick = { onCombineClick(row.id); contextMenuOpen = false },
					)
				}
				if (row.hidden) {
					DropdownMenuItem(
						text = { Text("Unhide channel") },
						onClick = { onUnhide(); contextMenuOpen = false },
					)
				} else {
					DropdownMenuItem(
						text = { Text("Hide channel") },
						onClick = { showHideConfirm = true; contextMenuOpen = false },
					)
				}
				if (isActive) {
					DropdownMenuItem(
						text = { Text("End conversation") },
						onClick = { showEndConfirm = true; contextMenuOpen = false },
					)
				}
			}
		}
	}
}

/**
 * Static row for the synthetic `_admin` pseudo-conversation (admin_notifications surface).
 * Lives outside the conversation model and renders as a passive notification banner -
 * no swipe-to-hide, no resume/combine/end menu. Clicking opens the legacy session screen
 * (admin route) to view notifications.
 */
@Composable
fun AdminRow(
	row: ConversationRow,
	onClick: () -> Unit,
) {
	Box(
		modifier = Modifier
			.fillMaxWidth()
			.background(MaterialTheme.colorScheme.surface)
	) {
		Row(
			modifier = Modifier
				.fillMaxWidth()
				.padding(horizontal = 16.dp, vertical = 12.dp),
			verticalAlignment = Alignment.CenterVertically,
		) {
			Column(modifier = Modifier.weight(1f)) {
				Text(
					text = row.title,
					style = MaterialTheme.typography.titleMedium,
					maxLines = 1,
					overflow = TextOverflow.Ellipsis,
				)
				val preview = row.preview
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
			if (row.displayCount > 0) {
				CountChip(row.displayCount)
			}
		}
		// Click target overlay so the row still feels clickable when tapped.
		Box(
			modifier = Modifier
				.fillMaxSize()
				.combinedClickable(onClick = onClick, onLongClick = onClick)
		)
	}
}

internal fun leafName(cwdCanonical: String): String {
	return cwdCanonical.trimEnd('/').substringAfterLast('/')
}
