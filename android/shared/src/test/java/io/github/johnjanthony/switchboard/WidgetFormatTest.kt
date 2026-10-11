package io.github.johnjanthony.switchboard

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WidgetFormatTest {

	// 2026-06-26T12:00:00Z as epoch ms, used as "now" throughout.
	private val now = java.time.OffsetDateTime.parse("2026-06-26T12:00:00Z").toInstant().toEpochMilli()

	@Test
	fun `formatResetCountdown renders hours, minutes, and days`() {
		assertEquals("2h", formatResetCountdown(now, "2026-06-26T14:00:00Z"))
		assertEquals("30m", formatResetCountdown(now, "2026-06-26T12:30:00Z"))
		assertEquals("3d", formatResetCountdown(now, "2026-06-29T12:00:00Z"))
	}

	@Test
	fun `formatResetCountdown is empty for absent, past, or unparseable input`() {
		assertEquals("", formatResetCountdown(now, null))
		assertEquals("", formatResetCountdown(now, ""))
		assertEquals("", formatResetCountdown(now, "2026-06-26T11:00:00Z")) // past
		assertEquals("", formatResetCountdown(now, "not-a-date"))
	}

	@Test
	fun `formatResetCountdown accepts both Z and offset forms`() {
		assertEquals("2h", formatResetCountdown(now, "2026-06-26T14:00:00+00:00"))
	}

	@Test
	fun `widgetStale is false for a recent push`() {
		assertFalse(widgetStale(now, "2026-06-26T11:59:00Z")) // 1 min ago
	}

	@Test
	fun `widgetStale is true past the threshold and for absent or unparseable input`() {
		assertTrue(widgetStale(now, "2026-06-26T11:50:00Z"))  // 10 min ago > 5 min
		assertTrue(widgetStale(now, null))
		assertTrue(widgetStale(now, ""))
		assertTrue(widgetStale(now, "not-a-date"))
	}

	@Test
	fun `formatAsOf renders the reading's age`() {
		assertEquals("as of just now", formatAsOf(now, "2026-06-26T11:59:30Z"))
		assertEquals("as of 5m ago", formatAsOf(now, "2026-06-26T11:55:00Z"))
		assertEquals("as of 3h ago", formatAsOf(now, "2026-06-26T09:00:00+00:00"))
		assertEquals("as of 2d ago", formatAsOf(now, "2026-06-24T12:00:00Z"))
	}

	@Test
	fun `formatAsOf is empty for absent or unparseable input`() {
		assertEquals("", formatAsOf(now, null))
		assertEquals("", formatAsOf(now, "not-a-date"))
	}

	@Test
	fun `msUntilStale counts down to the first moment widgetStale turns true`() {
		val wait = msUntilStale(now, "2026-06-26T11:58:00Z")!!
		assertEquals(180_001L, wait)
		assertFalse(widgetStale(now + wait - 1, "2026-06-26T11:58:00Z"))
		assertTrue(widgetStale(now + wait, "2026-06-26T11:58:00Z"))
	}

	@Test
	fun `msUntilStale is one millisecond at exactly the threshold`() {
		assertEquals(1L, msUntilStale(now, "2026-06-26T11:55:00Z"))
	}

	@Test
	fun `msUntilStale is null once stale and for absent or unparseable input`() {
		assertNull(msUntilStale(now, "2026-06-26T11:50:00Z"))
		assertNull(msUntilStale(now, null))
		assertNull(msUntilStale(now, "not-a-date"))
	}
}
