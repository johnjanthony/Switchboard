package io.github.johnjanthony.switchboard.ui

import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val TIME_FORMAT = DateTimeFormatter.ofPattern("h:mm a", Locale.ENGLISH)
private val DATE_FORMAT = DateTimeFormatter.ofPattern("MMM d", Locale.ENGLISH)

/**
 * Format a server-supplied ISO-8601 UTC timestamp string for display in a message bubble.
 *
 * Same-day messages render as "h:mm am/pm" (lowercase meridiem, no seconds).
 * Different-day messages render as "h:mm am/pm MMM d".
 *
 * Returns an empty string for null or unparseable input — display layer treats
 * empty as "no timestamp to render."
 *
 * @param isoUtc the ISO-8601 UTC string from `ChannelMessage.timestamp`.
 * @param nowProvider supplies "today" in the display zone (defaults to current local date).
 * @param zone display time zone (defaults to system default).
 */
fun formatBubbleTimestamp(
	isoUtc: String?,
	nowProvider: () -> LocalDate = { LocalDate.now() },
	zone: ZoneId = ZoneId.systemDefault(),
): String {
	if (isoUtc.isNullOrBlank()) return ""
	val parsed = try {
		OffsetDateTime.parse(isoUtc).atZoneSameInstant(zone)
	} catch (_: Exception) {
		return ""
	}
	val today = nowProvider()
	val time = parsed.format(TIME_FORMAT).lowercase(Locale.ENGLISH)
	return if (parsed.toLocalDate() == today) {
		time
	} else {
		"$time ${parsed.format(DATE_FORMAT)}"
	}
}
