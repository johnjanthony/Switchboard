package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.ConversationSummary

/**
 * Convene target picker: choose a brand-new conversation (with an optional title) or route into
 * an existing active one. Mirrors SpawnSessionDialog's radio-group idiom and picker copy so the
 * two conversation-choice UIs read as the same control.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConveneTargetSheet(
	selectedCount: Int,
	activeConversations: List<ConversationSummary>,
	onDismiss: () -> Unit,
	onConvene: (target: String, title: String?) -> Unit,
) {
	val sheetState = rememberModalBottomSheetState()

	// "new" or an existing conversation's id - one flat radio group, same shape as spawn's picker.
	var target by remember { mutableStateOf("new") }
	var title by remember { mutableStateOf("") }

	ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
		Column(
			modifier = Modifier
				.fillMaxWidth()
				.padding(horizontal = 20.dp)
				.padding(bottom = 24.dp),
			verticalArrangement = Arrangement.spacedBy(8.dp),
		) {
			Text(
				text = "Convene $selectedCount session${if (selectedCount == 1) "" else "s"}",
				style = MaterialTheme.typography.titleMedium,
			)

			Row(verticalAlignment = Alignment.CenterVertically) {
				RadioButton(selected = target == "new", onClick = { target = "new" })
				Text("New conversation")
			}
			if (target == "new") {
				OutlinedTextField(
					value = title,
					onValueChange = { title = it },
					label = { Text("Title (optional)") },
					singleLine = true,
					modifier = Modifier.fillMaxWidth(),
				)
			}

			activeConversations.forEach { conv ->
				Row(verticalAlignment = Alignment.CenterVertically) {
					RadioButton(selected = target == conv.id, onClick = { target = conv.id })
					Text("${conv.title} (${conv.memberRoster})")
				}
			}

			Row(
				modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
				horizontalArrangement = Arrangement.End,
			) {
				Button(
					onClick = {
						val trimmed = if (target == "new") title.trim().ifBlank { null } else null
						onConvene(target, trimmed)
					},
				) { Text("Convene") }
			}
		}
	}
}
