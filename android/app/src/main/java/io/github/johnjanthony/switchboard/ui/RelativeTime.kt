package io.github.johnjanthony.switchboard.ui

fun formatRelativeTime(iso: String?): String {
	if (iso.isNullOrBlank()) return ""
	val instant = try { java.time.Instant.parse(iso) } catch (_: Exception) { return "" }
	val now = java.time.Instant.now()
	val seconds = java.time.Duration.between(instant, now).seconds
	return when {
		seconds < 60 -> "${seconds}s"
		seconds < 3600 -> "${seconds / 60}m"
		seconds < 86400 -> "${seconds / 3600}h"
		seconds < 604800 -> "${seconds / 86400}d"
		else -> {
			val zdt = instant.atZone(java.time.ZoneId.systemDefault())
			zdt.format(java.time.format.DateTimeFormatter.ofPattern("MMM d"))
		}
	}
}
