package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Drafts
import androidx.compose.material.icons.filled.Email
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.johnjanthony.switchboard.MarkdownText
import io.github.johnjanthony.switchboard.network.ChannelMessage
import io.github.johnjanthony.switchboard.ui.theme.SwitchboardTheme

// One transcript line, "instrument log" treatment: a recessive mono byline (sender + time)
// sits ABOVE a soft-bordered bubble. Everything is left-aligned; who is the byline (brass for
// you). A pending question is carried by the bubble highlight itself (coral left rail + tint),
// not a pill; rejected/withdrawn get a labelled tag, never color alone.
//
// timestampOpacity is retained for call-site compatibility but unused: the byline now shows the
// time permanently, so the old pull-to-reveal gesture is vestigial.
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
	val isPending = isQuestion && !isAnswered && !isCancelled && !isRejected

	val scheme = MaterialTheme.colorScheme
	val colAlpha = if (isCancelled) 0.5f else 1f

	// Bubble fill + left rail + outline by state.
	val bubbleBg = if (isPending) scheme.tertiary.copy(alpha = 0.09f) else scheme.surfaceVariant
	val railColor: Color? = when {
		isPending -> scheme.tertiary
		isRejected -> scheme.error
		else -> null
	}
	val outline = if (isPending && isSelected) scheme.tertiary else scheme.outline
	val shape = RoundedCornerShape(10.dp)

	Column(
		modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp, horizontal = 12.dp).alpha(colAlpha),
		horizontalAlignment = Alignment.Start,
	) {
		// Recessive byline above the bubble.
		Row(
			modifier = Modifier.fillMaxWidth().padding(start = 2.dp, bottom = 5.dp),
			verticalAlignment = Alignment.CenterVertically,
		) {
			Text(
				text = message.sender,
				fontFamily = FontFamily.Monospace,
				fontWeight = FontWeight.Medium,
				fontSize = 12.sp,
				color = if (isHuman) scheme.primary else scheme.onSurfaceVariant,
			)
			Spacer(Modifier.width(10.dp))
			Text(
				text = formatBubbleTimestamp(message.timestamp),
				fontFamily = FontFamily.Monospace,
				fontSize = 11.sp,
				color = scheme.onSurfaceVariant,
			)
			Spacer(Modifier.weight(1f))
			when {
				isRejected -> StateTag("REJECTED", scheme.error)
				isCancelled -> StateTag("WITHDRAWN", scheme.onSurfaceVariant)
			}
		}

		// Bubble: soft-bordered card, clipped top-left, state rail on the left edge.
		val bubbleModifier = Modifier
			.fillMaxWidth()
			.clip(shape)
			.background(bubbleBg)
			.border(1.dp, outline, shape)
			.let { if (isPending) it.combinedClickable(onClick = onClick) else it }

		Row(modifier = bubbleModifier.height(IntrinsicSize.Min)) {
			if (railColor != null) {
				Box(modifier = Modifier.width(3.dp).fillMaxHeight().background(railColor))
			}
			Column(
				modifier = Modifier.padding(
					start = if (railColor != null) 10.dp else 13.dp,
					top = 10.dp, end = 13.dp, bottom = 10.dp,
				),
			) {
				MarkdownText(
					content = message.text,
					format = message.format,
					color = scheme.onSurface,
					isSelectable = !isPending,
					fontScale = fontScale,
				)
				if (!message.url.isNullOrBlank() && !message.filename.isNullOrBlank()) {
					Spacer(Modifier.height(8.dp))
					FilePill(message, onDownloadClick, onDownloadLongClick)
				}
			}
		}
	}
}

@Composable
private fun StateTag(text: String, color: Color) {
	Box(
		modifier = Modifier
			.clip(RoundedCornerShape(3.dp))
			.border(1.dp, color.copy(alpha = 0.4f), RoundedCornerShape(3.dp))
			.padding(horizontal = 6.dp, vertical = 2.dp),
	) {
		Text(text = text, fontFamily = FontFamily.Monospace, fontSize = 9.sp, letterSpacing = 1.2.sp, color = color)
	}
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun FilePill(
	message: ChannelMessage,
	onDownloadClick: (url: String, filename: String) -> Unit,
	onDownloadLongClick: (url: String, filename: String) -> Unit,
) {
	Surface(
		modifier = Modifier.combinedClickable(
			onClick = { onDownloadClick(message.url!!, message.filename!!) },
			onLongClick = { onDownloadLongClick(message.url!!, message.filename!!) },
		),
		color = MaterialTheme.colorScheme.surface,
		border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
		shape = RoundedCornerShape(10.dp),
	) {
		Row(
			modifier = Modifier.padding(horizontal = 11.dp, vertical = 7.dp),
			verticalAlignment = Alignment.CenterVertically,
		) {
			Text(
				text = leafName(message.filename!!),
				fontFamily = FontFamily.Monospace,
				fontSize = 11.5.sp,
				color = MaterialTheme.colorScheme.onSurface,
			)
			Spacer(Modifier.width(8.dp))
			Icon(
				imageVector = if (message.opened) Icons.Default.Drafts else Icons.Default.Email,
				contentDescription = if (message.opened) "Opened" else "Unopened",
				modifier = Modifier.size(15.dp),
				tint = MaterialTheme.colorScheme.onSurfaceVariant,
			)
		}
	}
}

@Preview(showBackground = true, backgroundColor = 0xFF14161A)
@Composable
fun PreviewMessageBubbleNormal() {
	SwitchboardTheme {
		MessageBubble(message = ChannelMessage(sender = "claude", type = "notify", text = "This is a normal message.", timestamp = "2026-05-02T18:32:05+00:00"))
	}
}

@Preview(showBackground = true, backgroundColor = 0xFF14161A)
@Composable
fun PreviewMessageBubblePending() {
	SwitchboardTheme {
		MessageBubble(
			message = ChannelMessage(sender = "claude", type = "question", text = "Overwrite the file, or merge the new cases in?", request_id = "req1", timestamp = "2026-05-02T18:32:05+00:00"),
			isSelected = true,
		)
	}
}

@Preview(showBackground = true, backgroundColor = 0xFF14161A)
@Composable
fun PreviewMessageBubbleHuman() {
	SwitchboardTheme {
		MessageBubble(message = ChannelMessage(sender = "you", type = "human", text = "No, leave the version alone.", timestamp = "2026-05-02T18:33:05+00:00"))
	}
}

@Preview(showBackground = true, heightDp = 360, backgroundColor = 0xFF14161A)
@Composable
fun PreviewMessageBubbleStates() {
	SwitchboardTheme {
		Column {
			MessageBubble(message = ChannelMessage(sender = "claude", type = "question", text = "Answered question.", request_id = "a", timestamp = "2026-05-02T18:32:05+00:00"), isAnswered = true)
			MessageBubble(message = ChannelMessage(sender = "claude", type = "question", text = "Rejected question.", request_id = "r", rejected = true, timestamp = "2026-05-02T18:32:05+00:00"))
			MessageBubble(message = ChannelMessage(sender = "claude", type = "question", text = "Withdrawn question.", request_id = "w", cancelled = true, timestamp = "2026-05-02T18:32:05+00:00"))
		}
	}
}
