package io.github.johnjanthony.switchboard.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

@Composable
fun HiddenChannelsToggleMenuItem(
	hiddenCount: Int,
	showHidden: Boolean,
	onToggle: () -> Unit,
) {
	DropdownMenuItem(
		text = { Text("Show hidden ($hiddenCount)") },
		onClick = onToggle,
		trailingIcon = { if (showHidden) Icon(Icons.Default.Check, null) },
		enabled = hiddenCount > 0,
	)
}
