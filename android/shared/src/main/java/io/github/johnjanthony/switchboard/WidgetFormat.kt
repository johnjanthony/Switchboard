package io.github.johnjanthony.switchboard

import java.time.OffsetDateTime

/**
 * Short countdown until a quota window resets ("3d" / "2h" / "5m"). Returns "" when the
 * timestamp is absent, already in the past, or unparseable. OffsetDateTime.parse accepts
 * both the +00:00 and Z forms the server may emit; anything else degrades to "" rather
 * than throwing.
 */
fun formatResetCountdown(nowMs: Long, resetsAtIso: String?): String {
	if (resetsAtIso.isNullOrBlank()) return ""
	val ms = try {
		OffsetDateTime.parse(resetsAtIso).toInstant().toEpochMilli()
	} catch (_: Exception) {
		return ""
	}
	val delta = ms - nowMs
	if (delta <= 0) return ""
	val minutes = delta / (60L * 1000L)
	return when {
		minutes >= 24L * 60L -> "${minutes / (24L * 60L)}d"
		minutes >= 60L -> "${minutes / 60L}h"
		else -> "${minutes}m"
	}
}

/**
 * Whether the widget snapshot is stale - Watchtower has not pushed within thresholdMs, or
 * pushed_at is absent/unparseable. Readers show "Watchtower offline / as of N min ago"
 * rather than presenting stale rings and quota as live.
 */
fun widgetStale(nowMs: Long, pushedAtIso: String?, thresholdMs: Long = 5L * 60L * 1000L): Boolean {
	if (pushedAtIso.isNullOrBlank()) return true
	val ms = try {
		OffsetDateTime.parse(pushedAtIso).toInstant().toEpochMilli()
	} catch (_: Exception) {
		return true
	}
	return (nowMs - ms) > thresholdMs
}
