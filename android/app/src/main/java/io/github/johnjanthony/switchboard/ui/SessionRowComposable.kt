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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DesktopWindows
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Badge
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
import io.github.johnjanthony.switchboard.network.Channel
import io.github.johnjanthony.switchboard.network.ConversationSummary

@OptIn(ExperimentalFoundationApi::class, ExperimentalMaterial3Api::class)
@Composable
fun SessionRow(
	channel: Channel,
	awayActive: Boolean,
	onClick: () -> Unit,
	onHide: () -> Unit,
	onUnhide: () -> Unit,
	onExitAway: () -> Unit,
	// New T-027 callbacks — default no-ops preserve backward compat with existing callers
	conversationSummary: ConversationSummary? = null,
	onResumeClick: (conversationId: String) -> Unit = {},
	onCombineClick: (conversationId: String) -> Unit = {},
	onEndClick: (conversationId: String) -> Unit = {},
) {
	var contextMenuOpen by remember { mutableStateOf(false) }
	var showHideConfirm by remember { mutableStateOf(false) }
	var showEndConfirm by remember { mutableStateOf(false) }

	val isOpenConversation = conversationSummary?.isOpenConversation == true
	val isResumable = conversationSummary?.isResumable == true
	val isActive = conversationSummary?.state == "active" || conversationSummary == null
	val staleWarning = conversationSummary?.staleSessionWarning == true

	// Resolve agent status from the conversation path (new) or fall back to the legacy
	// channel path when no conversationSummary is available.
	val agentStatus: io.github.johnjanthony.switchboard.network.AgentStatus? = if (conversationSummary != null) {
		val memberSender = conversationSummary.members
			.firstOrNull { it.cwd.equals(channel.cwdCanonical, ignoreCase = true) }
			?.sender
		memberSender?.let { conversationSummary.agentStatuses[it] }
	} else {
		channel.agentStatus
	}

	// Hide confirmation dialog
	if (showHideConfirm) {
		val label = channel.title ?: leafName(channel.cwdCanonical)
		AlertDialog(
			onDismissRequest = { showHideConfirm = false },
			title = { Text("Hide conversation?") },
			text = { Text("Hide '$label'? You can unhide it later from the menu.") },
			confirmButton = {
				TextButton(onClick = { onHide(); showHideConfirm = false }) { Text("Hide") }
			},
			dismissButton = {
				TextButton(onClick = { showHideConfirm = false }) { Text("Cancel") }
			},
		)
	}

	// End conversation confirmation dialog — Task 37 wording
	if (showEndConfirm) {
		val label = channel.title ?: leafName(channel.cwdCanonical)
		val convId = conversationSummary?.id ?: ""
		AlertDialog(
			onDismissRequest = { showEndConfirm = false },
			title = { Text("End conversation?") },
			text = {
				Text(
					"End conversation '$label'? Members will fall back to their home conversation " +
					"(if away mode is on) or to terminal output (if off).",
				)
			},
			confirmButton = {
				TextButton(onClick = { onEndClick(convId); showEndConfirm = false }) { Text("End") }
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
					// Swipe-right → end conversation (Task 40)
					showEndConfirm = true
					false // Snap back
				}
				SwipeToDismissBoxValue.EndToStart -> {
					// Swipe-left → hide with confirmation (Task 40)
					showHideConfirm = true
					false // Snap back
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
				SwipeToDismissBoxValue.StartToEnd -> Color(0xFFF44336) // Red for End
				SwipeToDismissBoxValue.EndToStart -> Color(0xFF9E9E9E) // Grey for Hide
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
		// Task 39: openConversation accent border
		val rowModifier = if (isOpenConversation) {
			Modifier
				.background(MaterialTheme.colorScheme.surface)
				.border(2.dp, MaterialTheme.colorScheme.primary, RoundedCornerShape(0.dp))
		} else {
			Modifier.background(MaterialTheme.colorScheme.surface)
		}

		Box(modifier = rowModifier) {
			Row(
				modifier = Modifier
					.fillMaxWidth()
					.combinedClickable(onClick = onClick, onLongClick = { contextMenuOpen = true })
					.alpha(if (channel.hidden) 0.5f else 1f)
					.padding(horizontal = 16.dp, vertical = 12.dp),
				verticalAlignment = Alignment.CenterVertically,
			) {
				// Task 39: "open" badge on leading edge when this is the open conversation
				if (isOpenConversation) {
					Text(
						"open",
						style = MaterialTheme.typography.labelSmall,
						color = MaterialTheme.colorScheme.primary,
						modifier = Modifier.padding(end = 6.dp),
					)
				}

				val isStatusFresh = agentStatus?.isFresh() == true
				Box(
					modifier = Modifier
						.size(width = 14.dp, height = 14.dp)
						.padding(end = 6.dp),
					contentAlignment = Alignment.CenterStart,
				) {
					if (isStatusFresh) {
						val transition = rememberInfiniteTransition(label = "channelListActiveDot")
						val a by transition.animateFloat(
							initialValue = 0.5f, targetValue = 1f,
							animationSpec = infiniteRepeatable(
								animation = tween(durationMillis = 1600, easing = FastOutSlowInEasing),
								repeatMode = RepeatMode.Reverse,
							),
							label = "alpha",
						)
						val scl by transition.animateFloat(
							initialValue = 0.92f, targetValue = 1.05f,
							animationSpec = infiniteRepeatable(
								animation = tween(durationMillis = 1600, easing = FastOutSlowInEasing),
								repeatMode = RepeatMode.Reverse,
							),
							label = "scale",
						)
						Box(
							modifier = Modifier
								.size(8.dp)
								.scale(scl)
								.alpha(a)
								.background(MaterialTheme.colorScheme.primary, CircleShape),
						)
					}
				}
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
					Row(verticalAlignment = Alignment.CenterVertically) {
						// Task 39: stale session warning indicator
						if (staleWarning) {
							Text(
								"⚠",
								style = MaterialTheme.typography.labelSmall,
								color = MaterialTheme.colorScheme.error,
							)
							Spacer(Modifier.width(4.dp))
						}
						Text(
							text = formatRelativeTime(channel.lastActivityAt),
							style = MaterialTheme.typography.labelSmall,
							color = MaterialTheme.colorScheme.onSurfaceVariant,
						)
					}
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
				// Task 35: Resume menu item
				if (conversationSummary != null) {
					DropdownMenuItem(
						text = { Text("Resume") },
						enabled = isResumable,
						onClick = { onResumeClick(conversationSummary.id); contextMenuOpen = false },
					)
				}
				// Task 35: Combine into… menu item
				if (conversationSummary != null && isActive) {
					DropdownMenuItem(
						text = { Text("Combine into…") },
						onClick = { onCombineClick(conversationSummary.id); contextMenuOpen = false },
					)
				}
				// Hide / Unhide
				if (channel.hidden) {
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
				// Task 37: End conversation — Active conversations only
				if (conversationSummary != null && isActive) {
					DropdownMenuItem(
						text = { Text("End conversation") },
						onClick = { showEndConfirm = true; contextMenuOpen = false },
					)
				}
			}
		}
	}
}

internal fun leafName(cwdCanonical: String): String {
	return cwdCanonical.trimEnd('/').substringAfterLast('/')
}
