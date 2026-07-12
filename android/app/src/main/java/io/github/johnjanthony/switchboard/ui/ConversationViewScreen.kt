package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.AwayModePillChip
import io.github.johnjanthony.switchboard.network.ConversationRow
import io.github.johnjanthony.switchboard.network.Pending

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConversationViewScreen(
	row: ConversationRow,
	scrollToMessageId: String? = null,
	onScrollConsumed: () -> Unit = {},
	awayActive: Boolean,
	predecessorTitle: String? = null,
	onOpenPredecessor: () -> Unit = {},
	onBack: () -> Unit,
	onLongPressPill: () -> Unit,
	onSubmitReply: (sender: String, text: String, requestId: String?) -> Unit,
	onDownloadFile: (url: String, filename: String) -> Unit,
	onLongPressDownloadFile: (url: String, filename: String) -> Unit,
	onMarkMessageOpened: (msgId: String) -> Unit,
	onShowTabInfo: () -> Unit,
) {
	val messages = row.messages
	val currentPending = row.pendingQuestions
	val agentStatus = row.agentStatus
	val listState = rememberLazyListState()
	val activePending = currentPending.filterValues { !it.cancelled }
	var selectedRequestId by remember(row.id) { mutableStateOf<String?>(null) }

	// Consume the shared VM's authoritative answered set directly; no local
	// re-derivation (matches wear).
	val answeredSet: Set<String> = row.answeredQuestionMsgIds

	val context = androidx.compose.ui.platform.LocalContext.current
	var feedFontScale by remember { androidx.compose.runtime.mutableFloatStateOf(context.feedFontScale()) }

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
			// If not yet in the list (still syncing), wait - recomposition with a larger
			// messages.size will retry. Don't fall through to scroll-to-bottom.
			return@LaunchedEffect
		}
		if (messages.isNotEmpty()) {
			listState.scrollToItem(messages.size - 1)
		}
	}

	Scaffold(
		topBar = {
			Column {
				TopAppBar(
					title = {
						Text(
							text = buildAnnotatedString {
								append(row.title)
								val roster = row.memberRoster
								if (roster.isNotEmpty()) {
									withStyle(
										style = MaterialTheme.typography.bodySmall.toSpanStyle().copy(
											color = MaterialTheme.colorScheme.onSurfaceVariant
										)
									) {
										append(" ($roster)")
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
						AwayModePillChip(
							active = awayActive,
							onLongPress = onLongPressPill,
						)
					},
				)
				if (predecessorTitle != null) {
					PredecessorBanner(title = predecessorTitle, onClick = onOpenPredecessor)
				}
			}
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
					// Pinch to scale the feed font. We don't bail on consumed events here: the
					// embedded TextView (markdown bubbles with isSelectable=true) consumes
					// single-finger touches for selection mode setup. A consume-gate would
					// prevent multi-finger pinch from ever engaging on selectable bubbles. We
					// only consume ourselves when zoom != 1f, which fires only on real pinches.
					awaitEachGesture {
						awaitFirstDown(requireUnconsumed = false)
						var didPinch = false
						do {
							val event = awaitPointerEvent()
							if (event.changes.size >= 2) {
								val zoom = event.calculateZoom()
								if (zoom != 1f) {
									didPinch = true
									val next = (feedFontScale * zoom).coerceIn(MIN_FONT_SCALE, MAX_FONT_SCALE)
									feedFontScale = next
									event.changes.forEach { it.consume() }
								}
							}
						} while (event.changes.any { it.pressed })
						if (didPinch) {
							val snapped = snapFontScale(feedFontScale)
							feedFontScale = snapped
							context.setFeedFontScale(snapped)
						}
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
							isSelected = msg.request_id != null && msg.request_id == selectedRequestId,
							fontScale = feedFontScale,
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
					if (agentStatus?.isFresh() == true) {
						item(key = "agent_status_row") {
							AgentStatusRow(status = agentStatus)
						}
					}
				}
			}
		}
	}
}

// Slim tappable banner shown directly under the app bar when this conversation was
// continued from a predecessor (meta.continued_from resolves to a loaded row). Tapping
// it navigates one hop back to that predecessor; multi-hop chains walk back one at a time.
@Composable
private fun PredecessorBanner(title: String, onClick: () -> Unit) {
	Surface(onClick = onClick, tonalElevation = 1.dp, modifier = Modifier.fillMaxWidth()) {
		Row(
			modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
			verticalAlignment = Alignment.CenterVertically,
		) {
			Icon(
				Icons.Default.ArrowBack,
				contentDescription = null,
				modifier = Modifier.size(16.dp),
				tint = MaterialTheme.colorScheme.onSurfaceVariant,
			)
			Spacer(Modifier.width(8.dp))
			Text(
				text = "Continued from \"$title\"",
				style = MaterialTheme.typography.bodySmall,
				color = MaterialTheme.colorScheme.onSurfaceVariant,
				maxLines = 1,
				overflow = TextOverflow.Ellipsis,
			)
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
