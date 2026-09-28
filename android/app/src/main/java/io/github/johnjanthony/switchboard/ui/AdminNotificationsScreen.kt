package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.selection.SelectionContainer
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.ConversationRow

// Read-only view of the synthetic _admin row: server notices such as a rejected spawn
// pick. There is deliberately no composer - the row has no Firebase conversation
// behind it (R3), so nothing typed here could be delivered. A null or empty row (every
// notice dismissed from Operator or aged out) shows an empty state rather than
// bouncing the user back.
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AdminNotificationsScreen(row: ConversationRow?, onBack: () -> Unit) {
	val messages = row?.messages.orEmpty()
	val listState = rememberLazyListState()
	LaunchedEffect(messages.size) {
		if (messages.isNotEmpty()) listState.scrollToItem(messages.size - 1)
	}

	Scaffold(
		topBar = {
			TopAppBar(
				title = { Text("Admin") },
				navigationIcon = {
					IconButton(onClick = onBack) {
						Icon(Icons.Default.ArrowBack, contentDescription = "Back")
					}
				},
			)
		},
	) { padding ->
		if (messages.isEmpty()) {
			Box(modifier = Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
				Text(
					text = "No admin notices",
					style = MaterialTheme.typography.bodyMedium,
					color = MaterialTheme.colorScheme.onSurfaceVariant,
				)
			}
		} else {
			SelectionContainer {
				LazyColumn(
					state = listState,
					modifier = Modifier.fillMaxSize().padding(padding),
					contentPadding = PaddingValues(8.dp),
				) {
					items(messages.size, key = { idx -> messages[idx].first }) { idx ->
						MessageBubble(message = messages[idx].second)
					}
				}
			}
		}
	}
}
