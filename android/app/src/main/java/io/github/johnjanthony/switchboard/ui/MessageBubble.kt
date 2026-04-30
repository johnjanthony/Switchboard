package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
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
import androidx.compose.material.icons.filled.Description
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Divider
import androidx.compose.material3.ElevatedButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
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
	isSelected: Boolean = false,
	onClick: () -> Unit = {},
	onDownloadClick: (url: String, filename: String) -> Unit = { _, _ -> },
	onDownloadLongClick: (url: String, filename: String) -> Unit = { _, _ -> },
) {
	val isHuman = message.type == "human"
	val isQuestion = message.type == "question" || message.type == "ask_human"
	val isCancelled = message.cancelled
	val isRejected = message.rejected
	val isAnswered = isQuestion && message.response_text != null
	val isPending = (isQuestion && !isAnswered && !isCancelled && !isRejected)

	val alpha = if (isCancelled) 0.5f else 1f
	val bgColor = if (isHuman) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant
	val textColor = if (isHuman) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant

	Column(
		modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp, horizontal = 8.dp).alpha(alpha),
		horizontalAlignment = if (isHuman) Alignment.End else Alignment.Start,
	) {
		Text(
			text = message.sender,
			style = MaterialTheme.typography.labelSmall,
			color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
			modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
		)
		Surface(
			shape = RoundedCornerShape(12.dp),
			color = bgColor,
			border = if (isSelected) BorderStroke(2.dp, MaterialTheme.colorScheme.primary) else null,
			modifier = Modifier
				.fillMaxWidth(0.9f)
				.combinedClickable(
					enabled = isPending,
					onClick = onClick,
				),
		) {
			Column(modifier = Modifier.padding(12.dp)) {
				Row(verticalAlignment = Alignment.CenterVertically) {
					if (isPending) {
						Box(
							modifier = Modifier
								.size(8.dp)
								.background(MaterialTheme.colorScheme.primary, CircleShape),
						)
						Spacer(Modifier.width(8.dp))
					}
					if (isAnswered) {
						Surface(
							shape = RoundedCornerShape(4.dp),
							color = MaterialTheme.colorScheme.secondaryContainer,
						) {
							Text(
								text = "RESPONDED",
								style = MaterialTheme.typography.labelSmall,
								modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
								color = MaterialTheme.colorScheme.onSecondaryContainer,
							)
						}
						Spacer(Modifier.width(8.dp))
					}
					if (message.rejected) {
						Surface(
							shape = RoundedCornerShape(4.dp),
							color = MaterialTheme.colorScheme.errorContainer,
						) {
							Text(
								text = "REJECTED",
								style = MaterialTheme.typography.labelSmall,
								modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
								color = MaterialTheme.colorScheme.onErrorContainer,
							)
						}
						Spacer(Modifier.width(8.dp))
					}
					if (isCancelled) {
						Surface(
							shape = RoundedCornerShape(4.dp),
							color = MaterialTheme.colorScheme.errorContainer,
						) {
							Text(
								text = "WITHDRAWN",
								style = MaterialTheme.typography.labelSmall,
								modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
								color = MaterialTheme.colorScheme.onErrorContainer,
							)
						}
						Spacer(Modifier.width(8.dp))
					}
				}
				MarkdownText(content = message.text, format = message.format, color = textColor)

				if (isAnswered && message.response_text != null) {
					Spacer(Modifier.height(8.dp))
					Divider(color = textColor.copy(alpha = 0.2f))
					Spacer(Modifier.height(8.dp))
					Row(verticalAlignment = Alignment.CenterVertically) {
						Text(
							text = "John: ",
							style = MaterialTheme.typography.labelMedium,
							color = textColor.copy(alpha = 0.7f),
						)
						Text(
							text = message.response_text!!,
							style = MaterialTheme.typography.bodyMedium,
							color = textColor,
						)
					}
				}

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
								Icon(
									imageVector = Icons.Default.Description,
									contentDescription = null,
									modifier = Modifier.size(18.dp),
									tint = MaterialTheme.colorScheme.onSurface
								)
								Spacer(Modifier.width(8.dp))
								Text(
									text = leafName(message.filename!!),
									style = MaterialTheme.typography.labelLarge,
									color = MaterialTheme.colorScheme.onSurface
								)
							}
						}
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
				text = "This is a normal message."
			)
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
				request_id = "req1"
			)
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
				response_text = "Yes, it was."
			)
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
				cancelled = true
			)
		)
	}
}
