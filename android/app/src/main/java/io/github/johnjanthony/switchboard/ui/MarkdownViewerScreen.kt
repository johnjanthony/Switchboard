package io.github.johnjanthony.switchboard.ui

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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
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
		Column(
			modifier = Modifier
				.fillMaxSize()
				.padding(padding)
				.verticalScroll(scrollState)
				.padding(16.dp),
		) {
			MarkdownText(
				content = content,
				format = "markdown",
				color = MaterialTheme.colorScheme.onSurfaceVariant,
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
