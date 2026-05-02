package io.github.johnjanthony.switchboard.ui

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

class TimestampFormatTest {
	private val nyTz = ZoneId.of("America/New_York")

	@Test
	fun `same-day timestamp formats as time-only`() {
		// "Today" (relative to nowProvider) is 2026-05-02 in NY.
		// Message is 2026-05-02 14:32:05 UTC == 10:32 AM EDT.
		val out = formatBubbleTimestamp(
			isoUtc = "2026-05-02T14:32:05+00:00",
			nowProvider = { LocalDate.of(2026, 5, 2) },
			zone = nyTz,
		)
		assertEquals("10:32 am", out)
	}

	@Test
	fun `same-day timestamp drops seconds and uses lowercase meridiem`() {
		val out = formatBubbleTimestamp(
			isoUtc = "2026-05-02T18:05:59+00:00",  // 2:05 PM EDT
			nowProvider = { LocalDate.of(2026, 5, 2) },
			zone = nyTz,
		)
		assertEquals("2:05 pm", out)
	}

	@Test
	fun `different-day timestamp appends month and day`() {
		// Message: 2026-05-01 19:32 UTC == 3:32 PM EDT on May 1.
		// Now: May 2 in NY.
		val out = formatBubbleTimestamp(
			isoUtc = "2026-05-01T19:32:00+00:00",
			nowProvider = { LocalDate.of(2026, 5, 2) },
			zone = nyTz,
		)
		assertEquals("3:32 pm May 1", out)
	}

	@Test
	fun `noon and midnight format correctly`() {
		val noon = formatBubbleTimestamp(
			isoUtc = "2026-05-02T16:00:00+00:00",  // 12:00 PM EDT
			nowProvider = { LocalDate.of(2026, 5, 2) },
			zone = nyTz,
		)
		assertEquals("12:00 pm", noon)

		val midnight = formatBubbleTimestamp(
			isoUtc = "2026-05-02T04:00:00+00:00",  // 12:00 AM EDT
			nowProvider = { LocalDate.of(2026, 5, 2) },
			zone = nyTz,
		)
		assertEquals("12:00 am", midnight)
	}

	@Test
	fun `null timestamp returns empty string`() {
		val out = formatBubbleTimestamp(
			isoUtc = null,
			nowProvider = { LocalDate.of(2026, 5, 2) },
			zone = nyTz,
		)
		assertEquals("", out)
	}

	@Test
	fun `malformed timestamp returns empty string and does not throw`() {
		val out = formatBubbleTimestamp(
			isoUtc = "not-an-iso-string",
			nowProvider = { LocalDate.of(2026, 5, 2) },
			zone = nyTz,
		)
		assertEquals("", out)
	}
}
