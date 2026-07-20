package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.MarkdownText
import kotlinx.coroutines.launch
import android.text.Spanned
import io.noties.markwon.core.spans.HeadingSpan

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MarkdownViewerScreen(
	title: String,
	content: String,
	onBack: () -> Unit,
) {
	val scrollState = rememberScrollState()
	val coroutineScope = rememberCoroutineScope()
	val density = LocalDensity.current
	val context = LocalContext.current
	var viewerFontScale by remember { mutableFloatStateOf(context.viewerFontScale()) }

	Scaffold(
		topBar = {
			TopAppBar(
				title = {
					Text(
						text = title,
						maxLines = 1,
						overflow = TextOverflow.Ellipsis,
					)
				},
				navigationIcon = {
					IconButton(onClick = onBack) {
						Icon(Icons.Default.ArrowBack, contentDescription = "Back")
					}
				},
			)
		},
		containerColor = MaterialTheme.colorScheme.surfaceVariant,
	) { padding ->
		Box(
			modifier = Modifier
				.fillMaxSize()
				.padding(padding)
				.pointerInput(Unit) {
					// See ConversationViewScreen for the no-consume-bail rationale.
					awaitEachGesture {
						awaitFirstDown(requireUnconsumed = false)
						var didPinch = false
						do {
							val event = awaitPointerEvent()
							if (event.changes.size >= 2) {
								val zoom = event.calculateZoom()
								if (zoom != 1f) {
									didPinch = true
									val next = (viewerFontScale * zoom).coerceIn(MIN_FONT_SCALE, MAX_FONT_SCALE)
									viewerFontScale = next
									event.changes.forEach { it.consume() }
								}
							}
						} while (event.changes.any { it.pressed })
						if (didPinch) {
							val snapped = snapFontScale(viewerFontScale)
							viewerFontScale = snapped
							context.setViewerFontScale(snapped)
						}
					}
				},
		) {
			Column(
				modifier = Modifier
					.fillMaxSize()
					.verticalScroll(scrollState)
					.padding(16.dp),
			) {
				MarkdownText(
					content = content,
					format = "markdown",
					color = MaterialTheme.colorScheme.onSurfaceVariant,
					isSelectable = true,
					fontScale = viewerFontScale,
				) { textView, link ->
					val anchor = link.removePrefix("#").lowercase()
					val layout = textView.layout ?: return@MarkdownText
					val spanned = textView.text as? Spanned ?: return@MarkdownText

					// Find headers using HeadingSpans for precision
					val headings = spanned.getSpans(0, spanned.length, HeadingSpan::class.java)
					var foundLine = -1

					for (span in headings) {
						val start = spanned.getSpanStart(span)
						val end = spanned.getSpanEnd(span)
						val headerText = spanned.subSequence(start, end).toString()

						val slug = headerText.trim().lowercase()
							.replace(Regex("[^a-z0-9\\s-]"), "")
							.replace(Regex("\\s+"), "-")
							.trim('-')

						if (slug == anchor) {
							foundLine = layout.getLineForOffset(start)
							break
						}
					}

					if (foundLine != -1) {
						// Add padding offset (16.dp converted to pixels)
						val paddingOffset = with(density) { 16.dp.toPx() }.toInt()
						val y = layout.getLineTop(foundLine) + paddingOffset

						coroutineScope.launch {
							scrollState.animateScrollTo(y)
						}
					}
				}
			}
		}
	}
}
