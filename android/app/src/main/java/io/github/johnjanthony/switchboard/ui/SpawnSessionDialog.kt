package io.github.johnjanthony.switchboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SpawnSessionDialog(
	mruList: List<String>,
	onDismiss: () -> Unit,
	onSpawn: (project: String, prompt: String, useClaude: Boolean, useGemini: Boolean) -> Unit,
	onRemoveFromMru: (String) -> Unit,
) {
	var project by remember { mutableStateOf("") }
	var prompt by remember { mutableStateOf("") }
	var useClaude by remember { mutableStateOf(true) }
	var useGemini by remember { mutableStateOf(false) }
	var expanded by remember { mutableStateOf(false) }

	AlertDialog(
		onDismissRequest = onDismiss,
		title = { Text("Spawn Session") },
		text = {
			Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
				ExposedDropdownMenuBox(
					expanded = expanded,
					onExpandedChange = { expanded = !expanded },
				) {
					OutlinedTextField(
						value = project,
						onValueChange = { project = it },
						label = { Text("Project (optional)") },
						singleLine = true,
						modifier = Modifier.fillMaxWidth().menuAnchor(),
						trailingIcon = {
							ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded)
						},
						colors = ExposedDropdownMenuDefaults.outlinedTextFieldColors(),
					)
					if (mruList.isNotEmpty()) {
						ExposedDropdownMenu(
							expanded = expanded,
							onDismissRequest = { expanded = false },
						) {
							mruList.forEach { item ->
								DropdownMenuItem(
									text = {
										Row(
											verticalAlignment = Alignment.CenterVertically,
											modifier = Modifier.fillMaxWidth(),
										) {
											Text(item, modifier = Modifier.weight(1f))
											IconButton(
												onClick = { onRemoveFromMru(item) },
												modifier = Modifier.size(24.dp),
											) {
												Icon(
													Icons.Default.Delete,
													contentDescription = "Remove",
													modifier = Modifier.size(16.dp),
													tint = MaterialTheme.colorScheme.error,
												)
											}
										}
									},
									onClick = {
										project = item
										expanded = false
									},
								)
							}
						}
					}
				}
				OutlinedTextField(
					value = prompt,
					onValueChange = { prompt = it },
					label = { Text("Initial Prompt / Instructions") },
					modifier = Modifier.fillMaxWidth(),
					minLines = 2,
				)
				Row(
					verticalAlignment = Alignment.CenterVertically,
					modifier = Modifier.fillMaxWidth(),
				) {
					Checkbox(checked = useClaude, onCheckedChange = { useClaude = it })
					Text("Claude")
					Spacer(Modifier.width(16.dp))
					Checkbox(checked = useGemini, onCheckedChange = { useGemini = it })
					Text("Gemini")
				}
			}
		},
		confirmButton = {
			Button(
				onClick = { onSpawn(project.trim(), prompt.trim(), useClaude, useGemini) },
				enabled = prompt.isNotBlank() && (useClaude || useGemini),
			) { Text("Spawn") }
		},
		dismissButton = {
			TextButton(onClick = onDismiss) { Text("Cancel") }
		},
	)
}
