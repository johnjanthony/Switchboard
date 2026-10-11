package io.github.johnjanthony.switchboard

import java.time.OffsetDateTime
import kotlin.math.roundToInt

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
 * Whether a heartbeat timestamp is stale: older than thresholdMs, or absent/unparseable. The
 * stale pill reads the server's presence/server_at, rewritten every 60 s.
 */
fun widgetStale(nowMs: Long, heartbeatIso: String?, thresholdMs: Long = 5L * 60L * 1000L): Boolean {
	if (heartbeatIso.isNullOrBlank()) return true
	val ms = try {
		OffsetDateTime.parse(heartbeatIso).toInstant().toEpochMilli()
	} catch (_: Exception) {
		return true
	}
	return (nowMs - ms) > thresholdMs
}

/**
 * "as of 5m ago" for a reading's timestamp (minutes, hours, then days); "as of just now" under a
 * minute; "" when absent or unparseable. The quota reading changes only when a session's turn
 * reports one, so its age tells John how current the bars are.
 */
fun formatAsOf(nowMs: Long, iso: String?): String {
	val ms = parseIsoMs(iso) ?: return ""
	val minutes = (nowMs - ms).coerceAtLeast(0L) / 60_000L
	return when {
		minutes < 1L -> "as of just now"
		minutes < 60L -> "as of ${minutes}m ago"
		minutes < 24L * 60L -> "as of ${minutes / 60L}h ago"
		else -> "as of ${minutes / (24L * 60L)}d ago"
	}
}

/**
 * Calculates the percentage of the quota window that has elapsed based on its reset time and total duration.
 * Returns a formatted string like "42%" or null if unparseable/absent.
 */
fun formatTimeElapsedPercentage(nowMs: Long, resetsAtIso: String?, durationMs: Long): String? {
	if (resetsAtIso.isNullOrBlank()) return null
	val resetsAtMs = try {
		OffsetDateTime.parse(resetsAtIso).toInstant().toEpochMilli()
	} catch (_: Exception) {
		return null
	}
	val remainingMs = (resetsAtMs - nowMs).coerceAtLeast(0L)
	val elapsedMs = (durationMs - remainingMs).coerceIn(0L, durationMs)
	return "${(elapsedMs.toDouble() / durationMs.toDouble() * 100).roundToInt()}%"
}

fun groupSortKey(displayName: String): Int {
	val d = displayName.lowercase()
	if (d.contains("claude")) return 0
	if (d.contains("gemini")) return 1
	return 2
}

fun formatAgyGroupName(displayName: String): String {
	val d = displayName.lowercase()
	if (d.contains("claude")) return "Antigravity w/ Claude"
	if (d.contains("gemini")) return "Antigravity w/ Gemini"
	return displayName.ifBlank { "Antigravity" }
}

fun isAgyGroupVisible(group: io.github.johnjanthony.switchboard.network.WidgetQuotaGroup): Boolean {
	val sPct = group.session?.pct ?: 0.0
	val wPct = group.weekly?.pct ?: 0.0
	return sPct > 0.0 || wPct > 0.0
}
