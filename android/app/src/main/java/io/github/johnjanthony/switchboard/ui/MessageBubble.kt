package io.github.johnjanthony.switchboard.ui

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
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
import androidx.compose.material.icons.automirrored.filled.Undo
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Drafts
import androidx.compose.material.icons.filled.Email
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.MarkdownText
import io.github.johnjanthony.switchboard.network.ChannelMessage
import io.github.johnjanthony.switchboard.ui.theme.DarkGreyPill
import io.github.johnjanthony.switchboard.ui.theme.SwitchboardTheme

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun MessageBubble(
	message: ChannelMessage,
	isAnswered: Boolean = false,
	timestampOpacity: Float = 0f,
	isSelected: Boolean = false,
	fontScale: Float = 1f,
	onClick: () -> Unit = {},
	onDownloadClick: (url: String, filename: String) -> Unit = { _, _ -> },
	onDownloadLongClick: (url: String, filename: String) -> Unit = { _, _ -> },
) {
	val isHuman = message.type == "human"
	val isQuestion = message.type == "question" || message.type == "ask_human"
	val isCancelled = message.cancelled
	val isRejected = message.rejected
	val isPending = (isQuestion && !isAnswered && !isCancelled && !isRejected)

	val alpha = if (isCancelled) 0.5f else 1f
	val bgColor = if (isHuman) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant
	val textColor = if (isHuman) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant

	val (pendingDotAlpha, pendingDotScale) = if (isPending) {
		val transition = rememberInfiniteTransition(label = "pendingDot")
		val alphaAnim by transition.animateFloat(
			initialValue = 0.4f,
			targetValue = 1f,
			animationSpec = infiniteRepeatable(
				animation = tween(durationMillis = 1500, easing = FastOutSlowInEasing),
				repeatMode = RepeatMode.Reverse,
			),
			label = "pendingDotAlpha",
		)
		val scaleAnim by transition.animateFloat(
			initialValue = 0.85f,
			targetValue = 1.15f,
			animationSpec = infiniteRepeatable(
				animation = tween(durationMillis = 1500, easing = FastOutSlowInEasing),
				repeatMode = RepeatMode.Reverse,
			),
			label = "pendingDotScale",
		)
		alphaAnim to scaleAnim
	} else {
		1f to 1f
	}

	val timestampLabel = formatBubbleTimestamp(message.timestamp)

	Column(
		modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp, horizontal = 8.dp).alpha(alpha),
		horizontalAlignment = if (isHuman) Alignment.End else Alignment.Start,
	) {
		// Sender + timestamp row: width matches the bubble's effective width (90%).
		// Sender stays at its alignment edge; timestamp lives centered, fading in/out
		// with the pull-gesture's `timestampOpacity`.
		Row(
			modifier = Modifier
				.fillMaxWidth(0.9f)
				.padding(horizontal = 6.dp, vertical = 2.dp),
			verticalAlignment = Alignment.CenterVertically,
		) {
			if (isHuman) {
				Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
					Text(
						text = timestampLabel,
						style = MaterialTheme.typography.labelSmall,
						color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
						modifier = Modifier.alpha(timestampOpacity),
					)
				}
				Text(
					text = message.sender,
					style = MaterialTheme.typography.labelSmall,
					color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
				)
			} else {
				Text(
					text = message.sender,
					style = MaterialTheme.typography.labelSmall,
					color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
				)
				Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
					Text(
						text = timestampLabel,
						style = MaterialTheme.typography.labelSmall,
						color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
						modifier = Modifier.alpha(timestampOpacity),
					)
				}
			}
		}
		Row(
			modifier = Modifier.fillMaxWidth(),
			verticalAlignment = Alignment.CenterVertically,
		) {
			if (isHuman) {
				Spacer(modifier = Modifier.weight(0.1f))
			}
			Surface(
				shape = RoundedCornerShape(12.dp),
				color = bgColor,
				border = if (isSelected) BorderStroke(2.dp, MaterialTheme.colorScheme.primary) else null,
				modifier = Modifier
					.weight(0.9f)
					.let {
						if (isPending) {
							it.combinedClickable(
								onClick = onClick
							)
						} else {
							it
						}
					},
			) {
				Column(modifier = Modifier.padding(12.dp)) {
					MarkdownText(content = message.text, format = message.format, color = textColor, isSelectable = !isPending, fontScale = fontScale)

					if (!message.url.isNullOrBlank() && !message.filename.isNullOrBlank()) {
						Spacer(Modifier.height(8.dp))
						Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
							Surface(
								modifier = Modifier
									.padding(top = 4.dp)
									.combinedClickable(
										onClick = { onDownloadClick(message.url!!, message.filename!!) },
										onLongClick = { onDownloadLongClick(message.url!!, message.filename!!) }
									),
								color = DarkGreyPill,
								tonalElevation = 2.dp,
								shadowElevation = 2.dp,
								border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.5f)),
								shape = RoundedCornerShape(16.dp),
							) {
								Row(
									modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
									verticalAlignment = Alignment.CenterVertically
								) {
									Text(
										text = leafName(message.filename!!),
										style = MaterialTheme.typography.labelLarge,
										color = MaterialTheme.colorScheme.onSurface
									)
									Spacer(Modifier.width(8.dp))
									Icon(
										imageVector = if (message.opened) Icons.Default.Drafts else Icons.Default.Email,
										contentDescription = if (message.opened) "Opened" else "Unopened",
										modifier = Modifier.size(18.dp),
										tint = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
									)
								}
							}
						}
					}
				}
			}
			if (!isHuman) {
				Box(
					modifier = Modifier
						.weight(0.1f)
						.padding(start = 4.dp),
					contentAlignment = Alignment.CenterStart,
				) {
					when {
						isPending -> Box(
							modifier = Modifier
								.size(18.dp)
								.scale(pendingDotScale)
								.alpha(pendingDotAlpha)
								.border(
									width = 1.5.dp,
									color = MaterialTheme.colorScheme.primary,
									shape = CircleShape,
								),
							contentAlignment = Alignment.Center,
						) {
							Text(
								text = "?",
								style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
								color = MaterialTheme.colorScheme.primary,
							)
						}
						message.rejected -> Icon(
							imageVector = Icons.Default.Block,
							contentDescription = "Rejected",
							tint = MaterialTheme.colorScheme.error,
							modifier = Modifier.size(18.dp),
						)
						isCancelled -> Icon(
							imageVector = Icons.AutoMirrored.Filled.Undo,
							contentDescription = "Withdrawn",
							tint = MaterialTheme.colorScheme.error,
							modifier = Modifier.size(18.dp),
						)
					}
				}
			}
		}
	}
}

@Preview(showBackground = true)
@Composable
fun PreviewMessageBubbleNormal() {
	SwitchboardTheme {
		MessageBubble(
			message = ChannelMessage(
				sender = "Claude",
				type = "notify",
				text = "This is a normal message.",
				timestamp = "2026-05-02T18:32:05+00:00",
			),
		)
	}
}

@Preview(showBackground = true)
@Composable
fun PreviewMessageBubblePendingQuestion() {
	SwitchboardTheme {
		MessageBubble(
			message = ChannelMessage(
				sender = "Claude",
				type = "question",
				text = "Is this a pending question?",
				request_id = "req1",
				timestamp = "2026-05-02T18:32:05+00:00",
			),
		)
	}
}

@Preview(showBackground = true)
@Composable
fun PreviewMessageBubbleAnsweredQuestion() {
	SwitchboardTheme {
		MessageBubble(
			message = ChannelMessage(
				sender = "Claude",
				type = "question",
				text = "Was this question answered?",
				request_id = "req2",
				timestamp = "2026-05-02T18:32:05+00:00",
			),
			isAnswered = true,
		)
	}
}

@Preview(showBackground = true)
@Composable
fun PreviewMessageBubbleCancelledQuestion() {
	SwitchboardTheme {
		MessageBubble(
			message = ChannelMessage(
				sender = "Claude",
				type = "question",
				text = "Was this question cancelled?",
				request_id = "req3",
				cancelled = true,
				timestamp = "2026-05-02T18:32:05+00:00",
			),
		)
	}
}

@Preview(showBackground = true)
@Composable
fun PreviewMessageBubbleRejectedQuestion() {
	SwitchboardTheme {
		MessageBubble(
			message = ChannelMessage(
				sender = "Claude",
				type = "question",
				text = "Was this question rejected?",
				request_id = "req4",
				rejected = true,
				timestamp = "2026-05-02T18:32:05+00:00",
			),
		)
	}
}

@Preview(showBackground = true, name = "All question states", heightDp = 600)
@Composable
fun PreviewMessageBubbleAllStates() {
	SwitchboardTheme {
		Column {
			MessageBubble(
				message = ChannelMessage(
					sender = "Claude",
					type = "question",
					text = "Pending question (blue dot, no icon).",
					request_id = "p1",
					timestamp = "2026-05-02T18:32:05+00:00",
				),
			)
			MessageBubble(
				message = ChannelMessage(
					sender = "Claude",
					type = "question",
					text = "Answered question (CheckCircle).",
					request_id = "p2",
					timestamp = "2026-05-02T18:32:05+00:00",
				),
				isAnswered = true,
			)
			MessageBubble(
				message = ChannelMessage(
					sender = "Claude",
					type = "question",
					text = "Rejected question (Block).",
					request_id = "p3",
					rejected = true,
					timestamp = "2026-05-02T18:32:05+00:00",
				),
			)
			MessageBubble(
				message = ChannelMessage(
					sender = "Claude",
					type = "question",
					text = "Withdrawn question (Undo).",
					request_id = "p4",
					cancelled = true,
					timestamp = "2026-05-02T18:32:05+00:00",
				),
			)
		}
	}
}

@Preview(showBackground = true, name = "Timestamp visible (agent)")
@Composable
fun PreviewMessageBubbleTimestampVisibleAgent() {
	SwitchboardTheme {
		MessageBubble(
			message = ChannelMessage(
				sender = "Claude",
				type = "notify",
				text = "Timestamp visible at full opacity.",
				timestamp = "2026-05-02T18:32:05+00:00",
			),
			timestampOpacity = 1f,
		)
	}
}

@Preview(showBackground = true, name = "Timestamp visible (human)")
@Composable
fun PreviewMessageBubbleTimestampVisibleHuman() {
	SwitchboardTheme {
		MessageBubble(
			message = ChannelMessage(
				sender = "John",
				type = "human",
				text = "Reply text",
				timestamp = "2026-05-02T18:32:05+00:00",
				attached_to_msg_id = "q1",
			),
			timestampOpacity = 1f,
		)
	}
}

@Preview(showBackground = true, name = "Timestamp visible (different day)")
@Composable
fun PreviewMessageBubbleTimestampDifferentDay() {
	SwitchboardTheme {
		MessageBubble(
			message = ChannelMessage(
				sender = "Claude",
				type = "notify",
				text = "Yesterday's message.",
				timestamp = "2026-05-01T15:32:00+00:00",
			),
			timestampOpacity = 1f,
		)
	}
}

@Preview(showBackground = true, name = "Font scale: 1.0 / 1.5 / 2.5", heightDp = 700)
@Composable
fun PreviewMessageBubbleFontScales() {
	SwitchboardTheme {
		Column {
			MessageBubble(
				message = ChannelMessage(
					sender = "Claude",
					type = "notify",
					text = "**Bold** text and `inline code` at fontScale 1.0.",
					timestamp = "2026-05-04T12:00:00+00:00",
				),
				fontScale = 1.0f,
			)
			MessageBubble(
				message = ChannelMessage(
					sender = "Claude",
					type = "notify",
					text = "**Bold** text and `inline code` at fontScale 1.5.",
					timestamp = "2026-05-04T12:00:00+00:00",
				),
				fontScale = 1.5f,
			)
			MessageBubble(
				message = ChannelMessage(
					sender = "Claude",
					type = "notify",
					text = "**Bold** text and `inline code` at fontScale 2.5.",
					timestamp = "2026-05-04T12:00:00+00:00",
				),
				fontScale = 2.5f,
			)
		}
	}
}
