package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.background
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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.MarkdownText
import io.github.johnjanthony.switchboard.network.ChannelMessage
import io.github.johnjanthony.switchboard.ui.theme.SwitchboardTheme

@Composable
fun MessageBubble(message: ChannelMessage) {
	val isQuestion = message.type == "question"
	val isCancelled = message.cancelled
	val isSystem = message.type == "system"
	val isPending = isQuestion && message.response_text == null && !isCancelled

	val alpha = if (isCancelled) 0.5f else 1f
	val bgColor = when {
		isSystem -> MaterialTheme.colorScheme.surfaceVariant
		isQuestion -> MaterialTheme.colorScheme.secondaryContainer
		else -> MaterialTheme.colorScheme.primaryContainer
	}

	Surface(
		shape = RoundedCornerShape(12.dp),
		color = bgColor,
		modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp).alpha(alpha),
	) {
		Column(modifier = Modifier.padding(12.dp)) {
			Row(verticalAlignment = Alignment.CenterVertically) {
				if (isPending) {
					Box(
						modifier = Modifier
							.size(8.dp)
							.background(MaterialTheme.colorScheme.primary, CircleShape)
					)
					Spacer(Modifier.width(8.dp))
				}
				Text(
					text = message.sender,
					style = MaterialTheme.typography.labelMedium,
					fontWeight = FontWeight.Bold,
				)
				Spacer(Modifier.weight(1f))
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
				}
			}
			Spacer(Modifier.height(4.dp))
			MarkdownText(content = message.text, format = message.format)
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
