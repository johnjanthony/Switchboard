package io.github.johnjanthony.switchboard.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import io.github.johnjanthony.switchboard.modelChipIsSpawnPick
import io.github.johnjanthony.switchboard.network.RegistrySession

// A session's model + effort exactly as the server resolved it. A spawn pick no transcript has
// confirmed yet renders muted, so it never reads as an observed value. Nothing without a label.
@Composable
fun ModelChip(rec: RegistrySession?, modifier: Modifier = Modifier) {
	if (rec == null) return
	val label = rec.modelLabel
	if (label.isNullOrBlank()) return
	Text(
		text = label,
		style = MaterialTheme.typography.labelSmall,
		color = if (modelChipIsSpawnPick(rec)) MaterialTheme.colorScheme.onSurfaceVariant
			else MaterialTheme.colorScheme.onSurface,
		maxLines = 1,
		overflow = TextOverflow.Ellipsis,
		modifier = modifier,
	)
}
