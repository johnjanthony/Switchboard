package io.github.johnjanthony.switchboard.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.Divider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.AssistChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.changedToUp
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.Channel
import io.github.johnjanthony.switchboard.network.ChannelMessage
import io.github.johnjanthony.switchboard.network.Pending
import kotlin.math.abs
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionViewScreen(
	channel: Channel,
	messages: List<Pair<String, ChannelMessage>>,
	awayActive: Boolean,
	isAwayOverride: Boolean,
	globalAway: Boolean,
	currentPending: Map<String, Pending>,
	scrollToMessageId: String? = null,
	onScrollConsumed: () -> Unit = {},
	onBack: () -> Unit,
	onLongPressPill: () -> Unit,
	onSubmitReply: (sender: String, text: String, requestId: String?) -> Unit,
	onDownloadFile: (url: String, filename: String) -> Unit,
	onLongPressDownloadFile: (url: String, filename: String) -> Unit,
	onMarkMessageOpened: (msgId: String) -> Unit,
	onShowTabInfo: () -> Unit,
) {
	val listState = rememberLazyListState()
	val activePending = currentPending.filterValues { !it.cancelled }
	var selectedRequestId by remember(channel.cwdKey) { mutableStateOf<String?>(null) }

	val answeredSet: Set<String> = remember(messages) {
		messages.mapNotNull { (_, m) -> m.attached_to_msg_id }
			.filter { targetId -> messages.any { it.first == targetId } }
			.toSet()
	}

	val timestampOpacity = remember { Animatable(0f) }
	val coroutineScope = rememberCoroutineScope()
	val density = LocalDensity.current
	val fullThresholdPx = with(density) { 80.dp.toPx() }
	val axisCommitThresholdPx = with(density) { 10.dp.toPx() }

	// Auto-select if there is exactly one pending question
	androidx.compose.runtime.LaunchedEffect(activePending.size) {
		if (activePending.size == 1) {
			selectedRequestId = activePending.keys.first()
		} else if (activePending.isEmpty()) {
			selectedRequestId = null
		}
	}

	androidx.compose.runtime.LaunchedEffect(scrollToMessageId, messages.size) {
		val targetId = scrollToMessageId
		if (targetId != null) {
			val idx = messages.indexOfFirst { it.first == targetId }
			if (idx >= 0) {
				listState.scrollToItem(idx)
				onScrollConsumed()
			}
			// If not yet in the list (still syncing), wait — recomposition with a larger
			// messages.size will retry. Don't fall through to scroll-to-bottom.
			return@LaunchedEffect
		}
		if (messages.isNotEmpty()) {
			listState.scrollToItem(messages.size - 1)
		}
	}

	Scaffold(
		topBar = {
			TopAppBar(
				title = {
					Text(
						text = buildAnnotatedString {
							append(channel.title ?: leafName(channel.cwdCanonical))
							if (channel.cwdCanonical.isNotEmpty()) {
								withStyle(
									style = MaterialTheme.typography.bodySmall.toSpanStyle().copy(
										color = MaterialTheme.colorScheme.onSurfaceVariant
									)
								) {
									append(" (${leafName(channel.cwdCanonical)})")
								}
							}
						},
						maxLines = 1, overflow = TextOverflow.Ellipsis,
					)
				},
				navigationIcon = {
					IconButton(onClick = onBack) {
						Icon(Icons.Default.ArrowBack, contentDescription = "Back")
					}
				},
				actions = {
					IconButton(onClick = onShowTabInfo) {
						Icon(Icons.Default.Info, contentDescription = "Tab info")
					}
					PerCwdAwayPill(
						awayActive = awayActive,
						isOverride = isAwayOverride,
						globalAway = globalAway,
						onLongPress = onLongPressPill,
					)
				},
			)
		},
		bottomBar = {
			val selected = activePending[selectedRequestId]
			if (selected != null) {
				ReplyInputBar(
					pending = selected,
					onSubmit = { text ->
						onSubmitReply(selected.sender, text, selected.requestId)
						// Optimistically clear selection if more remain
						if (activePending.size > 1) {
							selectedRequestId = null
						}
					},
				)
			} else if (activePending.isNotEmpty()) {
				Surface(tonalElevation = 2.dp) {
					Box(
						modifier = Modifier
							.fillMaxWidth()
							.padding(16.dp),
						contentAlignment = Alignment.Center
					) {
						Text(
							text = "Select a question to reply...",
							style = MaterialTheme.typography.bodyMedium,
							color = MaterialTheme.colorScheme.onSurfaceVariant
						)
					}
				}
			}
		},
	) { padding ->
		Box(
			modifier = Modifier
				.fillMaxSize()
				.padding(padding)
				.pointerInput(Unit) {
					awaitEachGesture {
						val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
						var horizontalClaimed = false

						while (true) {
							val event = awaitPointerEvent(pass = PointerEventPass.Initial)
							val change = event.changes.firstOrNull { it.id == down.id } ?: break
							if (change.changedToUp()) break
							val dx = change.position.x - down.position.x
							val dy = change.position.y - down.position.y

							if (!horizontalClaimed) {
								// Wait for unambiguous axis decision.
								if (abs(dx) > axisCommitThresholdPx || abs(dy) > axisCommitThresholdPx) {
									if (abs(dx) > abs(dy)) {
										horizontalClaimed = true
										change.consume()
									} else {
										// Vertical wins; let LazyColumn scroll. Stop tracking this gesture.
										break
									}
								}
							}
							if (horizontalClaimed) {
								change.consume()
								val target = (abs(dx) / fullThresholdPx).coerceIn(0f, 1f)
								// snapTo must be launched: awaitEachGesture's scope is
								// @RestrictsSuspension and won't allow direct calls to non-member
								// suspend funs.
								coroutineScope.launch { timestampOpacity.snapTo(target) }
							}
						}
						// Animate timestamps back to 0 on any loop exit (release, cancel, vertical
						// yield). animateTo from 0 to 0 is a no-op, so unconditional dispatch is safe.
						// Launched (not awaited) so the next gesture can start without waiting for the
						// fade-out animation to finish.
						coroutineScope.launch { timestampOpacity.animateTo(0f) }
					}
				},
		) {
			SelectionContainer {
				LazyColumn(
					state = listState,
					modifier = Modifier.fillMaxSize(),
					contentPadding = PaddingValues(8.dp),
				) {
					items(messages.size, key = { idx -> messages[idx].first }) { idx ->
						val (msgId, msg) = messages[idx]
						val prevTitle = if (idx > 0) messages[idx - 1].second.title else null
						val showSubheader = idx == 0 || (msg.title != null && msg.title != prevTitle)
						if (showSubheader && msg.title != null) {
							Column(modifier = Modifier.fillMaxWidth()) {
								Divider(modifier = Modifier.padding(horizontal = 24.dp))
								val titleText = msg.title
								Text(
									text = titleText ?: "",
									style = MaterialTheme.typography.labelMedium,
									color = MaterialTheme.colorScheme.onSurfaceVariant,
									modifier = Modifier
										.fillMaxWidth()
										.padding(horizontal = 12.dp, vertical = 4.dp),
									textAlign = TextAlign.Center,
								)
							}
						}
						MessageBubble(
							message = msg,
							isAnswered = msgId in answeredSet,
							timestampOpacity = timestampOpacity.value,
							isSelected = msg.request_id != null && msg.request_id == selectedRequestId,
							onClick = {
								if (msg.request_id != null && activePending.containsKey(msg.request_id)) {
									selectedRequestId = msg.request_id
								}
							},
							onDownloadClick = { url, filename ->
								onDownloadFile(url, filename)
								onMarkMessageOpened(msgId)
							},
							onDownloadLongClick = onLongPressDownloadFile,
						)
					}
				}
			}
		}
	}
}

@Composable
private fun ReplyInputBar(
	pending: Pending,
	onSubmit: (String) -> Unit,
) {
	var text by remember(pending.requestId) { mutableStateOf("") }
	val suggestions = pending.suggestions ?: listOf("Yes", "No", "Maybe", "On it!", "Done")

	Surface(tonalElevation = 2.dp) {
		Column(modifier = Modifier.fillMaxWidth().padding(8.dp)) {
			LazyRow(
				modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
				contentPadding = PaddingValues(horizontal = 4.dp),
			) {
				items(suggestions) { suggestion ->
					AssistChip(
						onClick = { onSubmit(suggestion) },
						label = { Text(suggestion) },
						modifier = Modifier.padding(horizontal = 4.dp)
					)
				}
			}
			Row(verticalAlignment = Alignment.CenterVertically) {
				OutlinedTextField(
					value = text,
					onValueChange = { text = it },
					modifier = Modifier.weight(1f),
					placeholder = { Text("Reply to ${pending.sender}…") },
					maxLines = 4,
					shape = RoundedCornerShape(24.dp),
				)
				Spacer(Modifier.width(8.dp))
				IconButton(onClick = { if (text.isNotBlank()) { onSubmit(text); text = "" } }) {
					Icon(Icons.Default.Send, contentDescription = "Send")
				}
			}
		}
	}
}
